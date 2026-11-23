"""把飞书里的选项/提示词表同步进本库（基础资料维护用）。

来源（主 Base）：
- 国内操作选项设置 tblFf1Zce9K801K6 → weida_ref_option（kind=单据类型/揽货方式/…）
- 航线 tblwxQw1CHshI9QX、业务部门列表 tblpOizn4wPNYvJB、航司对照表 tblH2eYH3OKDpwBq
- PKG UNIT tblpjmrf1jrSydj → kind=件数单位
- 委托书识别提示词 tblj5Z8qm99vuexQ / 账单识别提示词 tblk9mONkMuQuebh → weida_prompt
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from app.core.database import async_db_session

from . import feishu_sync as fs

OPTION_SOURCES = {
    "国内操作选项设置": "tblFf1Zce9K801K6",
    "航线": "tblwxQw1CHshI9QX",
    "业务部门列表": "tblpOizn4wPNYvJB",
    "航司对照表": "tblH2eYH3OKDpwBq",
    "PKG UNIT": "tblpPjmrf1jrSydj",
}
PROMPT_SOURCES = {
    "委托书": "tblj5Z8qm99vuexQ",
    "账单": "tblk9mONkMuQuebh",
}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "".join(
            str(item.get("text") or "") if isinstance(item, dict) else str(item)
            for item in value
        ).strip()
    if isinstance(value, dict):
        return str(value.get("text") or value.get("name") or "").strip()
    return str(value).strip()


PAIRS = (
    ("单据类型", "单据类型-iFreight编码", "单据类型-iFreight 编码"),
    ("揽货方式", "揽货方式-iFreight 编码", "揽货方式-iFreight编码"),
    ("业务类型", "业务类型-iFreight 编码", "业务类型-iFreight编码"),
    ("业务所属部门", "业务所属部门-iFreight编码", "业务所属部门-iFreight 编码"),
    ("航线", "航线-iFreight 编码", "航线-iFreight编码"),
)


async def sync_options() -> dict[str, int]:
    """拉飞书选项表写本库。表不存在时抛 FileNotFound，由调用方兜底。"""
    env = fs.load_env()
    token = fs.token(env)
    counts: dict[str, int] = {}
    rows: list[tuple[str, str, str | None, int]] = []
    sort = 0
    operations = fs.records(token, fs.MAIN, OPTION_SOURCES["国内操作选项设置"])
    for item in operations:
        fields = item.get("fields", {})
        for kind, *code_keys in PAIRS:
            value = _text(fields.get(kind))
            if not value:
                continue
            code = ""
            for key in code_keys:
                code = _text(fields.get(key))
                if code:
                    break
            rows.append((kind, value, code or None, sort))
    for kind, table in (("航线", OPTION_SOURCES["航线"]), ("业务部门", OPTION_SOURCES["业务部门列表"])):
        code_key = "代码"
        label_key = "描述" if table == OPTION_SOURCES["业务部门列表"] else "描述"
        for item in fs.records(token, fs.MAIN, table):
            fields = item.get("fields", {})
            code = _text(fields.get(code_key))
            label = _text(fields.get(label_key))
            value = f"{label}" if label else code
            if value:
                rows.append((kind, value, code or None, sort))
    for item in fs.records(token, fs.MAIN, OPTION_SOURCES["航司对照表"]):
        fields = item.get("fields", {})
        code = _text(fields.get("航空公司代码"))
        name = _text(fields.get("航空公司名称"))
        if code or name:
            # 用户 2026-11-24：航空公司选项是两位代码（QF、M7…），不是全名
            rows.append(("航空公司", code or name, code or None, sort))
    for item in fs.records(token, fs.MAIN, OPTION_SOURCES["PKG UNIT"]):
        fields = item.get("fields", {})
        code = _text(fields.get("Code"))
        if code:
            rows.append(("件数单位", code, code, sort))
    async with async_db_session() as session:
        for kind, value, code, order in rows:
            await session.execute(
                text(
                    "INSERT INTO weida_ref_option (kind, value, code, sort) "
                    "VALUES (:k, :v, :c, :s) ON DUPLICATE KEY UPDATE code=VALUES(code), sort=VALUES(sort)"
                ),
                {"k": kind, "v": value, "c": code, "s": order},
            )
            counts[kind] = counts.get(kind, 0) + 1
        await session.commit()
    return counts


async def sync_prompts() -> dict[str, int]:
    env = fs.load_env()
    token = fs.token(env)
    counts: dict[str, int] = {}
    async with async_db_session() as session:
        for kind, table in PROMPT_SOURCES.items():
            for item in fs.records(token, fs.MAIN, table):
                fields = item.get("fields", {})
                type_name = _text(fields.get("提示词类型")) or _text(fields.get("类型"))
                if not type_name:
                    continue
                prompt = _text(fields.get("提示词"))
                customer_link = fields.get("客户")
                customer = ""
                if isinstance(customer_link, dict):
                    ids = [str(x) for x in customer_link.get("link_record_ids") or []]
                    # 只留第一个客户 id 做「客户专用」标记，其余放不下列宽
                    customer = (ids[0] if ids else "")[:64]
                await session.execute(
                    text(
                        "INSERT INTO weida_prompt (kind, customer, type_name, prompt) "
                        "VALUES (:k, :c, :t, :p) ON DUPLICATE KEY UPDATE prompt=VALUES(prompt)"
                    ),
                    {"k": kind, "c": customer or None, "t": type_name, "p": prompt},
                )
                counts[kind] = counts.get(kind, 0) + 1
        await session.commit()
    return counts


async def ref_options(kind: str) -> list[dict[str, Any]]:
    async with async_db_session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT value, code FROM weida_ref_option WHERE kind = :k "
                    "ORDER BY sort, id"
                ),
                {"k": kind},
            )
        ).mappings().all()
    return [{"value": r["value"], "code": r["code"]} for r in rows]


async def prompt_templates(kind: str) -> list[dict[str, Any]]:
    async with async_db_session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT type_name, customer, prompt FROM weida_prompt WHERE kind = :k "
                    "ORDER BY type_name"
                ),
                {"k": kind},
            )
        ).mappings().all()
    return [dict(r) for r in rows]


async def admin_ref_options(kind: str = "") -> list[dict[str, Any]]:
    async with async_db_session() as session:
        where = "WHERE kind = :k" if kind else ""
        rows = (
            await session.execute(
                text(
                    "SELECT id, kind, value, code FROM weida_ref_option "
                    + where
                    + " ORDER BY kind, sort, id"
                ),
                {"k": kind},
            )
        ).mappings().all()
    return [dict(r) for r in rows]


async def save_ref_option(option_id: int | None, data: dict[str, Any]) -> int:
    async with async_db_session() as session:
        if option_id:
            await session.execute(
                text(
                    "UPDATE weida_ref_option SET kind=:k, value=:v, code=:c WHERE id=:id"
                ),
                {"k": data["kind"], "v": data["value"], "c": data.get("code"), "id": option_id},
            )
            new_id = option_id
        else:
            result = await session.execute(
                text(
                    "INSERT INTO weida_ref_option (kind, value, code) VALUES (:k, :v, :c) "
                    "ON DUPLICATE KEY UPDATE code=VALUES(code), id=LAST_INSERT_ID(id)"
                ),
                {"k": data["kind"], "v": data["value"], "c": data.get("code")},
            )
            new_id = int(result.lastrowid or 0)
        await session.commit()
    return new_id


async def delete_ref_option(option_id: int) -> None:
    async with async_db_session() as session:
        await session.execute(
            text("DELETE FROM weida_ref_option WHERE id=:id"), {"id": option_id}
        )
        await session.commit()


async def save_prompt(prompt_id: int, data: dict[str, Any]) -> None:
    async with async_db_session() as session:
        await session.execute(
            text("UPDATE weida_prompt SET type_name=:t, prompt=:p WHERE id=:id"),
            {"t": data.get("type_name"), "p": data.get("prompt", ""), "id": prompt_id},
        )
        await session.commit()


async def admin_prompts(kind: str = "委托书") -> list[dict[str, Any]]:
    async with async_db_session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id, kind, customer, type_name, prompt FROM weida_prompt "
                    "WHERE kind = :k ORDER BY type_name, id"
                ),
                {"k": kind},
            )
        ).mappings().all()
    return [dict(r) for r in rows]
