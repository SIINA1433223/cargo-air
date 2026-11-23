"""发送预报 / 报关 / 下发货到计划三封信：按 Java 引擎（wfs）的模板与取数口径移植。

收件人：订单「操作负责人」在飞书人员代码表里的电子邮箱；读不到就退回操作邮箱。
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from sqlalchemy import text

from app.core.database import async_db_session

from . import feishu_sync as fs
from .mailer import send_mail

PEOPLE_TABLE = "tbl5fwAc52aMj2Ub"  # 人员代码表
FALLBACK_TO = "weida-tao@foxmail.com"

_people_cache: tuple[float, dict[str, str]] | None = None


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "".join(
            str(item.get("text") or "") if isinstance(item, dict) else str(item)
            for item in value
        ).strip()
    if isinstance(value, dict):
        return str(value.get("text") or value.get("name") or "").strip()
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    return str(value).strip()


def _people_emails() -> dict[str, str]:
    global _people_cache
    now = time.time()
    if _people_cache and now - _people_cache[0] < 600:
        return _people_cache[1]
    out: dict[str, str] = {}
    try:
        env = fs.load_env()
        token = fs.token(env)
        for item in fs.records(token, fs.MAIN, PEOPLE_TABLE):
            fields = item.get("fields", {})
            name = _text(fields.get("英文名") or fields.get("姓名") or fields.get("名字"))
            mail = _text(fields.get("电子邮箱") or fields.get("邮箱"))
            if name and mail and "@" in mail:
                out[name] = mail
    except Exception:
        out = {}
    _people_cache = (now, out)
    return out


async def _load(order_id: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    async with async_db_session() as session:
        order = (
            await session.execute(
                text("SELECT * FROM biz_order WHERE id=:id AND is_deleted=0"), {"id": order_id}
            )
        ).mappings().first()
        if not order:
            raise ValueError("订单不存在")
        ifreights = (
            await session.execute(
                text(
                    "SELECT i.* FROM biz_link l JOIN biz_ifreight i ON i.id=l.to_id "
                    "AND i.is_deleted=0 WHERE l.from_table='order' AND l.to_table='ifreight' "
                    "AND l.field='所属订单' AND l.from_id=:id ORDER BY i.id"
                ),
                {"id": order_id},
            )
        ).mappings().all()
    return dict(order), [dict(row) for row in ifreights]


def _recipient(order: dict[str, Any]) -> str:
    name = _text(order.get("domestic_operation"))
    return _people_emails().get(name) or FALLBACK_TO


def _unit(value: Any, unit: str) -> str:
    raw = _text(value)
    if not raw:
        return ""
    return raw if raw.lower().endswith(unit.lower()) else raw + unit


def _eta(millis: Any) -> str:
    if isinstance(millis, datetime):
        when = millis
    else:
        try:
            when = datetime.fromtimestamp(int(millis) / 1000)
        except (TypeError, ValueError):
            return ""
    return "ETA " + when.strftime("%d%b").upper()


def _prealert(order: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[str, str]:
    hawbs = "/".join(_text(r.get("house_bl_no")) for r in rows if _text(r.get("house_bl_no")))
    subject = f"{_text(order.get('client_ref_no'))}//{hawbs}//{_text(order.get('master_no'))} 预报表格"
    head = "INV NO\tMAWBNO\tHAWBNO\tPCS\tGROSS WEIGHT\tDIMENSION\tCBM\tETA\tDELIVERY ADDRESS"
    lines = [head]
    for row in rows:
        lines.append(
            "\t".join(
                [
                    _text(order.get("client_ref_no")),
                    _text(order.get("master_no")),
                    _text(row.get("house_bl_no")),
                    _text(row.get("quantity")),
                    _unit(row.get("gross_weight"), "kg"),
                    _text(row.get("dimension")),
                    _unit(row.get("volume"), "CBM"),
                    _eta(row.get("estimated_arrival_time")),
                    _text(row.get("consignee_address")),
                ]
            )
        )
    if not rows:
        lines.append("\t".join([_text(order.get("client_ref_no")), _text(order.get("master_no"))] + [""] * 7))
    return subject, "\n".join(lines)


def _customs(order: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[str, str]:
    hawbs = "、".join(_text(r.get("house_bl_no")) for r in rows if _text(r.get("house_bl_no")))
    subject = f"{_text(order.get('client_ref_no'))}//{hawbs}报关计划"
    when = _text(order.get("warehouse_in_operation_time"))
    header = "您好\n\n此票货物的报关资料如附件，麻烦出一下预录单吧，谢谢\n"
    if len(rows) <= 1:
        row = rows[0] if rows else {}
        body = (
            f"\n主单号：{_text(order.get('master_no'))}\n"
            f"分单号：{_text(row.get('house_bl_no'))}\n"
            f"口岸：{_text(order.get('export_port'))}\n"
            f"预计报关时间：{when}\n"
        )
        return subject, header + body
    lines = [header, f"\n预计报关时间：{when}\n", "主单号\t分单号\t件数\t毛重\t口岸\t预计报关时间"]
    for row in rows:
        lines.append(
            "\t".join(
                [
                    _text(order.get("master_no")),
                    _text(row.get("house_bl_no")),
                    _text(row.get("quantity")),
                    _text(row.get("gross_weight")),
                    _text(order.get("export_port")),
                    when,
                ]
            )
        )
    return subject, "\n".join(lines)


def _arrival_text(value: Any) -> str:
    when = value if isinstance(value, datetime) else None
    if when is None:
        try:
            when = datetime.fromisoformat(str(value)[:19])
        except (TypeError, ValueError):
            return ""
    return when.strftime("%m-%d") + ("上午" if when.hour < 12 else "下午")


def _chinese_number(count: int) -> str:
    return {1: "一", 2: "两", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八", 9: "九", 10: "十"}.get(
        count, str(count)
    )


def _operation_plan(order: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[str, str]:
    house_bl = _text(order.get("house_bl_no"))
    subject = f"{_text(order.get('client_ref_no'))}//{house_bl}到货计划"
    supplier = _text(order.get("supplier"))
    if supplier:
        subject += f"//{supplier}"
    chinese = ";".join(dict.fromkeys(_text(r.get("customs_declaration_goods_name")) for r in rows if _text(r.get("customs_declaration_goods_name"))))
    english = ";".join(dict.fromkeys(_text(r.get("goods_description")) for r in rows if _text(r.get("goods_description"))))
    products = ";".join(x for x in (chinese, english) if x)
    declaration = "出直单" if (not house_bl or not rows) else f"一主{_chinese_number(len(rows))}分报{_chinese_number(len(rows))}票关"
    base = "\n".join(
        [
            f"代理入仓号：{_text(order.get('agent_warehouse_no'))}",
            f"制单信息请查收，{declaration}",
            f"主单号：{_text(order.get('master_no'))}",
            f"报关方式：{_text(order.get('customs_declaration_method'))}",
            f"分单号：{house_bl}",
            f"货物预计到达时间：{_arrival_text(order.get('warehouse_in_operation_time'))}",
            f"货物中英文品名：{products}",
            f"操作要求：{_text(order.get('agent_operation_requirement'))}",
            f"目的地：{_text(order.get('destination'))}",
        ]
    )
    if len(rows) <= 1:
        return subject, base
    lines = [base, "主单号\t分单号\t件数\t毛重\t体积\t中文品名\t英文品名"]
    for row in rows:
        lines.append(
            "\t".join(
                [
                    _text(order.get("master_no")),
                    _text(row.get("house_bl_no")),
                    _text(row.get("quantity")),
                    _text(row.get("gross_weight")),
                    _text(row.get("volume")),
                    _text(row.get("customs_declaration_goods_name")),
                    _text(row.get("goods_description")),
                ]
            )
        )
    return subject, "\n".join(lines)


KINDS = {"prealert": _prealert, "customs": _customs, "operation_plan": _operation_plan}
FLAG_FIELDS = {
    "prealert": ("send_forecast", "forecast_sent_time"),
    "customs": ("customs_declaration", None),
    "operation_plan": ("issue_operation_plan", None),
}


async def send_action_mail(order_id: int, kind: str) -> dict[str, Any]:
    compose = KINDS.get(kind)
    if compose is None:
        raise ValueError("未知邮件类型")
    order, rows = await _load(order_id)
    subject, body = compose(order, rows)
    to = _recipient(order)
    await send_mail(to, subject, body)
    field, stamp = FLAG_FIELDS[kind]
    async with async_db_session() as session:
        if stamp:
            await session.execute(
                text(
                    f"UPDATE biz_order SET `{field}`=1, `{stamp}`=NOW(3), update_time=NOW(3) WHERE id=:id"
                ),
                {"id": order_id},
            )
        else:
            await session.execute(
                text(f"UPDATE biz_order SET `{field}`=1, update_time=NOW(3) WHERE id=:id"),
                {"id": order_id},
            )
        await session.commit()
    return {"kind": kind, "to": to, "subject": subject, "rows": len(rows)}
