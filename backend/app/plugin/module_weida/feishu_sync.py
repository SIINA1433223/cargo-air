"""飞书多维表格 -> Fast 本地库单向同步。

以飞书为准：
- 飞书存在的记录 upsert 到本地；
- 本地多出的记录删除（--prune）；
- 本地字段只要飞书没有同名/同标签字段，就写空。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import bindparam, text

from app.core.database import async_db_session

API = "https://open.feishu.cn/open-apis"
ENV_FILE = Path("/home/ubuntu/weida/.secrets/mail.env")

MAIN = "Ane6bNFAYacsUPsi3gscwy7hnPb"
FLEET = "XRzkbqeiZaEsqOs09yccO2P7n0c"

# (base_token, table_id, 表名, 本地表)
TABLES: list[tuple[str, str, str, str]] = [
    (MAIN, "tblXmBgagFcTGWJr", "商机主表", "biz_opportunity"),
    (MAIN, "tbltztWS5DLZlly1", "航线报价", "biz_pricing"),
    (MAIN, "tblcTmqLBPpDxbIb", "对客方案", "biz_plan"),
    (MAIN, "tblBuuIwHo4JEuQJ", "对客方案费用明细", "biz_planfee"),
    (MAIN, "tblqNRxcBz4HRbY6", "订单表", "biz_order"),
    (MAIN, "tbl99ei11SxswR8v", "iFreight 数据表", "biz_ifreight"),
    (MAIN, "tblh2CFaVMHlSkXy", "ifreight额外费用表", "biz_extrafee"),
    (MAIN, "tblf6CB6EQzmujQz", "客商档案", "biz_vendor"),
    (MAIN, "tblki9CYlrC2rbln", "复制提单库", "biz_billlib"),
    (FLEET, "tbl5K5TvKk4x4iDs", "车队计划", "fleet_plan"),
    (FLEET, "tbllpJxs3VU9VXyO", "车队数据", "fleet_data"),
    (FLEET, "tblEPwAqRB9DOCgK", "车队账单", "fleet_bill"),
    (FLEET, "tblDBV8ZVXP1EtMd", "报价审核", "fleet_quote_review"),
]

JSON_TABLES = {"fleet_plan", "fleet_data", "fleet_bill", "fleet_quote_review"}

LINK_FIELDS = {
    ("biz_opportunity", "客户"): ("vendor", "merchant_short_name"),
    ("biz_order", "客户"): ("vendor", "merchant_short_name"),
    ("biz_order", "供应商"): ("vendor", "merchant_short_name"),
    ("biz_order", "国外代理"): ("vendor", "merchant_short_name"),
    ("biz_order", "关联商机"): ("opportunity", "opportunity_no"),
    ("biz_ifreight", "所属订单"): ("order", "order_no"),
}

ALIASES: dict[str, dict[str, str]] = {
    "biz_extrafee": {
        "国内/国外": "domestic_abroad",
        "费用类别": "fee_category",
        "币种": "currency",
        "计费单位": "billing_unit",
        "数量": "app_quantity",
        "单价": "unit_price",
        "税点": "tax_point",
        "计税类型": "tax_calc_type",
        "费用录入情况": "fee_entry_status",
    },
    "biz_order": {
        "客户": "customer_name",
        "供应商": "supplier",
        "国外代理": "foreign_agent_name",
        "主单号": "master_no",
        "分单号": "house_bl_no",
        "商机编号": "opportunity_no",
        "客户 REF NO.": "client_ref_no",
        "关联商机": "opportunity_no",
        "入仓操作时间": "warehouse_in_operation_time",
        "运输条款": "transport_clause",
        "费用记录": "fee_record",
        "账单状态": "bill_status_step",
    },
    "biz_ifreight": {
        "分单号": "house_bl_no",
        "客户 REF NO.": "customer_ref_no",
        "主单号": "master_no",
        "运输条款": "transport_clause",
        "件数": "quantity",
        "件数单位": "piece_unit",
        "毛重": "gross_weight",
        "尺寸": "dimension",
        "体积": "volume",
    },
}


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def req(url: str, token: str, method: str = "GET", body: dict | None = None) -> dict[str, Any]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    for attempt in range(5):
        try:
            with urllib.request.urlopen(request, timeout=60) as resp:
                payload = json.loads(resp.read().decode())
            if payload.get("code") == 0:
                return payload
            if payload.get("code") in (1254290, 1254291, 99991400):
                time.sleep(2 * (attempt + 1))
                continue
            raise RuntimeError(f"{payload.get('code')} {payload.get('msg')}")
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504):
                time.sleep(2 * (attempt + 1))
                continue
            raise
    raise RuntimeError("feishu request failed")


def token(env: dict[str, str]) -> str:
    payload = req(
        API + "/auth/v3/tenant_access_token/internal",
        "",
        "POST",
        {"app_id": env["FEISHU_APP_ID"], "app_secret": env["FEISHU_APP_SECRET"]},
    )
    return payload["tenant_access_token"]


def records(tk: str, app: str, table: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    page = ""
    while True:
        url = f"{API}/bitable/v1/apps/{app}/tables/{table}/records/search?page_size=500"
        if page:
            url += "&page_token=" + page
        data = req(url, tk, "POST", {})["data"]
        out.extend(data.get("items", []))
        if not data.get("has_more"):
            return out
        page = data.get("page_token", "")
        time.sleep(0.1)


def conv(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        if isinstance(value, list) and value and isinstance(value[0], dict):
            if "text" in value[0]:
                return "".join(str(x.get("text") or "") for x in value)
            if "name" in value[0]:
                return ",".join(str(x.get("name") or "") for x in value if x.get("name"))
            return ""
        if isinstance(value, dict):
            nested = value.get("value")
            if isinstance(nested, list):
                return "".join(
                    str(x.get("text") or "") if isinstance(x, dict) else str(x)
                    for x in nested
                )
            return str(value.get("text") or value.get("name") or "")
        return json.dumps(value, ensure_ascii=False)
    return value


async def columns(session, table: str) -> dict[str, str]:
    rows = (await session.execute(text(f"SHOW COLUMNS FROM `{table}`"))).all()
    return {str(r[0]): str(r[1]).lower() for r in rows}


async def labels(session, table: str) -> dict[str, str]:
    rows = (
        await session.execute(
            text("SELECT col,label FROM sys_field_label WHERE tbl=:t"), {"t": table}
        )
    ).all()
    return {str(label): col for col, label in rows}


# 关联字段要落成**本地 id** 而不是名字的列（商机的「最终对客方案」）
LINK_ID_COLUMNS = {
    ("biz_opportunity", "最终对客方案"): ("biz_plan", "final_plan_id"),
}


LINK_TABLES = {
    "opportunity": "biz_opportunity",
    "order": "biz_order",
    "pricing": "biz_pricing",
    "plan": "biz_plan",
    "ifreight": "biz_ifreight",
    "vendor": "biz_vendor",
}

# 飞书关联字段 → biz_link 行（妙搭那边「商机↔订单」这些关联全靠它，只写文本列不够）。
# value = (from_key, to_key, link_field)：**from/to 用的是 biz_link 里的表键**
# （opportunity / order / pricing / plan / ifreight），不是本地表名；
# 订单表的「关联商机」按 canonical 落成 商机→订单（field=订单表），与页面查询同一方向。
LINK_PAIRS = {
    ("biz_order", "关联商机"): ("opportunity", "order", "订单表"),
    ("biz_ifreight", "所属订单"): ("order", "ifreight", "所属订单"),
    ("biz_opportunity", "航线报价"): ("opportunity", "pricing", "航线报价"),
    ("biz_opportunity", "对客方案"): ("opportunity", "plan", "对客方案"),
    ("biz_pricing", "关联商机"): ("pricing", "opportunity", "关联商机"),
    ("biz_plan", "关联商机"): ("plan", "opportunity", "关联商机"),
    ("biz_plan", "航线报价"): ("plan", "pricing", "航线报价"),
}


async def sync_links(session, local: str, items: list[dict[str, Any]]) -> int:
    """按飞书关联字段把 biz_link 补齐（幂等，只增不删，方向按 canonical）。"""
    pairs = {f: spec for (tbl, f), spec in LINK_PAIRS.items() if tbl == local}
    if not pairs:
        return 0

    async def local_id(table: str, feishu_id: str) -> int | None:
        return (
            await session.execute(
                text(f"SELECT id FROM `{table}` WHERE feishu_record_id=:r"),
                {"r": feishu_id},
            )
        ).scalar()

    written = 0
    for item in items:
        rid = str(item.get("record_id") or "")
        if not rid:
            continue
        me = await local_id(local, rid)
        if not me:
            continue
        for field, (from_key, to_key, link_field) in pairs.items():
            raw = item.get("fields", {}).get(field)
            ids = raw.get("link_record_ids") if isinstance(raw, dict) else None
            from_table = LINK_TABLES[from_key]
            to_table = LINK_TABLES[to_key]
            other_table = to_table if from_table == local else from_table
            for other_fid in ids or []:
                other = await local_id(other_table, str(other_fid))
                if not other:
                    continue
                src, dst = (me, other) if from_table == local else (other, me)
                await session.execute(
                    text(
                        "INSERT INTO biz_link (field, from_table, from_id, to_table, to_id) "
                        "VALUES (:f, :ft, :fid, :tt, :tid) ON DUPLICATE KEY UPDATE id = id"
                    ),
                    {"f": link_field, "ft": from_key, "fid": src, "tt": to_key, "tid": dst},
                )
                written += 1
    return written


async def sync_table(
    tk: str,
    app: str,
    table_id: str,
    name: str,
    local: str,
    prune: bool,
    ref_maps: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    items = records(tk, app, table_id)
    async with async_db_session() as session:
        col_types = await columns(session, local)
        cols = set(col_types)
        label_map = await labels(session, local)
        alias = ALIASES.get(local, {})
        mapping: dict[str, str] = {}
        all_fields: set[str] = set()
        for item in items:
            all_fields.update(item.get("fields", {}).keys())
        for field in all_fields:
            if field in cols:
                mapping[field] = field
            elif field in alias and alias[field] in cols:
                mapping[field] = alias[field]
            elif field in label_map and label_map[field] in cols:
                mapping[field] = label_map[field]
        mapping["feishu_record_id"] = "feishu_record_id"
        json_table = local in JSON_TABLES and "data" in cols
        inserted = updated = deleted = 0
        seen: set[str] = set()
        for item in items:
            rid = str(item.get("record_id") or "")
            if not rid:
                continue
            seen.add(rid)
            values = {"feishu_record_id": rid}
            if json_table:
                values["data"] = json.dumps(item.get("fields", {}), ensure_ascii=False)
                mapping = {"feishu_record_id": "feishu_record_id", "data": "data"}
            for field, column in mapping.items():
                if field == "feishu_record_id":
                    continue
                # JSON 表（车队计划/数据/账单/报价审核）整行收进 data 列，
                # 不能再按字段循环覆盖成 None —— 之前就是这样把 data 写成空的。
                if json_table and column == "data":
                    continue
                if column == "chargeable_weight":
                    continue
                raw = item.get("fields", {}).get(field)
                id_col = LINK_ID_COLUMNS.get((local, field))
                if id_col and isinstance(raw, dict) and raw.get("link_record_ids"):
                    target_table, target_col = id_col
                    values[target_col] = (
                        await session.execute(
                            text(
                                f"SELECT id FROM `{target_table}` WHERE feishu_record_id=:r"
                            ),
                            {"r": str(raw["link_record_ids"][0])},
                        )
                    ).scalar()
                    continue
                resolver = LINK_FIELDS.get((local, field))
                if isinstance(raw, dict) and raw.get("link_record_ids") and resolver:
                    target, target_col = resolver
                    ids = raw.get("link_record_ids") or []
                    resolved = (ref_maps or {}).get(target, {}).get(str(ids[0])) if ids else None
                    if resolved:
                        values[column] = resolved
                        continue
                if raw is None:
                    values[column] = None
                else:
                    value = conv(raw)
                    ctype = col_types.get(column, "")
                    if "datetime" in ctype or ctype.startswith("date"):
                        if isinstance(value, str) and value.strip().isdigit():
                            value = float(value)
                        if isinstance(value, (int, float)) and not isinstance(value, bool):
                            value = datetime.fromtimestamp(float(value) / 1000).strftime(
                                "%Y-%m-%d %H:%M:%S"
                            )
                    ctype = col_types.get(column, "")
                    if ctype == "json" and value in ("", None):
                        value = None
                    if value == "" and ctype not in ("text", "longtext", "mediumtext", "tinytext", "json"):
                        value = None
                    if isinstance(value, str) and ctype == "json":
                        try:
                            json.loads(value)
                        except Exception:
                            value = json.dumps(value, ensure_ascii=False)
                    if isinstance(value, str) and (ctype.startswith("decimal") or ctype.startswith("int") or ctype.startswith("bigint")):
                        cleaned = value.replace(",", "").strip()
                        try:
                            value = float(cleaned)
                            if abs(value) > 1e9:
                                value = None
                            elif ctype.startswith("int") or ctype.startswith("bigint"):
                                value = int(value)
                        except ValueError:
                            value = None
                    if isinstance(value, str) and ctype.startswith("varchar("):
                        limit = int(ctype.split("(")[1].split(")")[0])
                        if len(value) > limit:
                            value = value[:limit]
                    values[column] = value
            exists = (
                await session.execute(
                    text(f"SELECT id FROM `{local}` WHERE feishu_record_id=:r"), {"r": rid}
                )
            ).scalar()
            if exists:
                sets = ", ".join(f"`{c}`=:{c}" for c in values if c != "feishu_record_id")
                if sets:
                    await session.execute(
                        text(f"UPDATE `{local}` SET {sets} WHERE feishu_record_id=:feishu_record_id"),
                        values,
                    )
                updated += 1
            else:
                keys = list(values.keys())
                updates = ", ".join(
                    f"`{k}`=VALUES(`{k}`)" for k in keys if k != "feishu_record_id"
                )
                await session.execute(
                    text(
                        f"INSERT INTO `{local}` ({','.join('`' + k + '`' for k in keys)}) "
                        f"VALUES ({','.join(':' + k for k in keys)}) "
                        + (f"ON DUPLICATE KEY UPDATE {updates}" if updates else "")
                    ),
                    values,
                )
                inserted += 1
        links = await sync_links(session, local, items)
        if prune and "feishu_record_id" in cols:
            if seen:
                stmt = text(
                    f"DELETE FROM `{local}` WHERE (feishu_record_id IS NULL "
                    "OR feishu_record_id = '' OR feishu_record_id NOT IN :ids)"
                ).bindparams(bindparam("ids", expanding=True))
                result = await session.execute(stmt, {"ids": tuple(seen)})
                deleted = result.rowcount or 0
        await session.commit()
    return {"table": name, "local": local, "feishu": len(items), "inserted": inserted, "updated": updated, "deleted": deleted, "links": links, "mapped": len(mapping) - 1}





async def cleanup_dangling_links() -> int:
    """prune 之后：清掉方向写反的旧关联 + 指向已删记录的关联行。"""
    removed = 0
    async with async_db_session() as session:
        # 上一版把「本地表名」当成关联键写成 from_table='biz_opportunity' 这类脏行，清掉
        result = await session.execute(
            text(
                "DELETE FROM biz_link WHERE from_table NOT IN "
                "('opportunity','order','pricing','plan','ifreight','vendor') "
                "OR to_table NOT IN ('opportunity','order','pricing','plan','ifreight','vendor')"
            )
        )
        removed += result.rowcount or 0
        for from_t, to_t, field in (
            ("biz_order", "biz_opportunity", "订单表"),
            ("biz_ifreight", "biz_order", "所属订单"),
        ):
            result = await session.execute(
                text(
                    "DELETE FROM biz_link WHERE from_table=:f AND to_table=:t AND field=:k"
                ),
                {"f": from_t, "t": to_t, "k": field},
            )
            removed += result.rowcount or 0
        for key, table in LINK_TABLES.items():
            for side, col in (("from", "from_id"), ("to", "to_id")):
                result = await session.execute(
                    text(
                        f"DELETE l FROM biz_link l WHERE l.{side}_table = :k "
                        f"AND NOT EXISTS (SELECT 1 FROM `{table}` x WHERE x.id = l.{col})"
                    ),
                    {"k": key},
                )
                removed += result.rowcount or 0
        await session.commit()
    return removed


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prune", action="store_true")
    parser.add_argument("--only", default="")
    args = parser.parse_args()
    env = load_env()
    tk = token(env)
    ref_maps: dict[str, dict[str, str]] = {}
    async with async_db_session() as ref_session:
        for key, table, col in (
            ("vendor", "biz_vendor", "merchant_short_name"),
            ("opportunity", "biz_opportunity", "opportunity_no"),
            ("order", "biz_order", "order_no"),
        ):
            rows = (
                await ref_session.execute(
                    text(
                        f"SELECT feishu_record_id,`{col}` FROM `{table}` "
                        "WHERE feishu_record_id IS NOT NULL"
                    )
                )
            ).all()
            ref_maps[key] = {str(r[0]): str(r[1] or "") for r in rows}
    report = []
    for app, table_id, name, local in TABLES:
        if args.only and args.only not in local and args.only not in name:
            continue
        try:
            result = await sync_table(tk, app, table_id, name, local, args.prune, ref_maps)
            print(json.dumps(result, ensure_ascii=False))
            report.append(result)
        except Exception as exc:
            print(json.dumps({"table": name, "error": type(exc).__name__ + ": " + str(exc)[:300]}, ensure_ascii=False))
    if args.prune:
        dangling = await cleanup_dangling_links()
        print(json.dumps({"dangling_links_removed": dangling}, ensure_ascii=False))
    print(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "tables": len(report)}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
