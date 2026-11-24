"""Fast 本地库 -> 飞书多维表格的单向回写（用户 2026-11-24）。

口径：
- **只写非空**：本地这一格空着就跳过，绝不拿空值覆盖飞书；
- 飞书里没有的本地记录 → **新建**（新建后把 record_id 写回本地，两边从此对齐）；
- 不动附件、不删飞书记录，查找引用 / 公式 / 按钮 / 阶段 / 自动编号这些只读字段一律跳过；
- 逐字段比对，值没变的字段不写（少触发自动化、少改更新时间）。

用法（在 ~/weida/backend 下）：
    PYTHONPATH=$PWD .venv/bin/python -m app.plugin.module_weida.push_sync            # 预演
    PYTHONPATH=$PWD .venv/bin/python -m app.plugin.module_weida.push_sync --apply    # 真写
    ... --only order          # 只跑名字含 order 的表
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from app.core.database import async_db_session

from .feishu_sync import (
    ALIASES,
    API,
    JSON_TABLES,
    LINK_PAIRS,
    LINK_TABLES,
    TABLES,
    columns,
    labels,
    load_env,
    records,
    req,
    token,
)

# 可以直接写的字段类型；其余（附件/查找引用/公式/按钮/阶段/自动编号/电话/超链接）跳过
WRITABLE_TYPES = {1, 2, 3, 4, 5, 7, 11, 18, 21}
SKIP_LOCAL_COLUMNS = {"id", "create_time", "update_time", "is_deleted", "feishu_record_id"}
CN_TZ = timezone(timedelta(hours=8))
BATCH = 100


class Skip:
    """哨兵：这一格不写。"""


SKIP = Skip()


def to_ms(value: Any) -> int | None:
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=CN_TZ)
        return int(dt.timestamp() * 1000)
    if isinstance(value, (int, float)):
        return int(value) if value > 0 else None
    if isinstance(value, str) and value.strip():
        raw = value.strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S"):
            try:
                return int(datetime.strptime(raw[: len(fmt) + 2], fmt).replace(tzinfo=CN_TZ).timestamp() * 1000)
            except ValueError:
                continue
        try:
            return int(float(raw))
        except ValueError:
            return None
    return None


def as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "".join(str(x.get("text") if isinstance(x, dict) else x) for x in value)
    if isinstance(value, dict):
        return str(value.get("text") or value.get("name") or "")
    return str(value) if value is not None else ""


def as_multi(value: Any) -> list[str]:
    if isinstance(value, list):
        return [as_text(v) for v in value if as_text(v)]
    if isinstance(value, str) and value.strip():
        raw = value.strip()
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return [as_text(v) for v in parsed if as_text(v)]
            except Exception:
                pass
        return [x.strip() for x in raw.replace("，", ",").split(",") if x.strip()]
    return []


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("1", "true", "是", "yes", "y")


def as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def normalize_existing(field_type: int, raw: Any) -> Any:
    """把飞书读回来的值折成和我们将要写入的值同一形状，方便比对。"""
    if field_type == 1:
        return as_text(raw).strip()
    if field_type == 2:
        return as_number(raw)
    if field_type == 3:
        return as_text(raw).strip()
    if field_type == 4:
        return sorted(as_multi(raw))
    if field_type == 5:
        # 本地 MySQL 只存到秒，飞书是毫秒；比对按秒截断，免得整表都被判成「有变化」
        return int(raw) // 1000 if isinstance(raw, (int, float)) else None
    if field_type == 7:
        return as_bool(raw)
    if field_type == 11:
        if isinstance(raw, list):
            return sorted(str(x.get("id")) for x in raw if isinstance(x, dict) and x.get("id"))
        return []
    if field_type in (18, 21):
        if isinstance(raw, dict):
            return sorted(str(x) for x in raw.get("link_record_ids") or [])
        if isinstance(raw, list):
            return sorted(str(x) for x in raw)
        return []
    return raw


async def build_link_index(session) -> dict[tuple[str, str, str], list[int]]:
    """(from_table, to_table, field) -> [(from_id, to_id)]，用于回写关联字段。"""
    rows = (
        await session.execute(
            text("SELECT from_table,to_table,field,from_id,to_id FROM biz_link")
        )
    ).mappings().all()
    index: dict[tuple[str, str, str], list[tuple[int, int]]] = {}
    for row in rows:
        key = (str(row["from_table"]), str(row["to_table"]), str(row["field"]))
        index.setdefault(key, []).append((int(row["from_id"]), int(row["to_id"])))
    return index


async def feishu_ids(session, table: str) -> dict[int, str]:
    rows = (
        await session.execute(
            text(f"SELECT id,feishu_record_id FROM `{table}` WHERE feishu_record_id IS NOT NULL")
        )
    ).all()
    return {int(r[0]): str(r[1]) for r in rows if r[1]}


async def person_open_ids(session) -> dict[str, str]:
    rows = (await session.execute(text("SELECT name,open_id FROM weida_feishu_user"))).all()
    return {str(r[0]).strip(): str(r[1]) for r in rows if r[0] and r[1]}


async def push_table(
    tk: str,
    app: str,
    table_id: str,
    name: str,
    local: str,
    apply: bool,
    ctx: dict[str, Any],
) -> dict[str, Any]:
    field_list = req(
        f"{API}/bitable/v1/apps/{app}/tables/{table_id}/fields?page_size=200", tk
    )["data"]["items"]
    fields = {str(f["field_name"]): int(f["type"]) for f in field_list}
    # 单选/多选的合法选项：写一个不存在的选项会让整批失败（1254062），先对照着过滤
    field_options: dict[str, set[str]] = {}
    for f in field_list:
        names = set()
        for option in ((f.get("property") or {}).get("options") or []):
            if isinstance(option, dict) and option.get("name"):
                names.add(str(option["name"]))
        if names:
            field_options[str(f["field_name"])] = names
    existing = {str(r.get("record_id")): r.get("fields", {}) for r in records(tk, app, table_id)}

    async with async_db_session() as session:
        col_types = await columns(session, local)
        cols = set(col_types)
        label_map = await labels(session, local)
        alias = ALIASES.get(local, {})
        is_json = local in JSON_TABLES
        where = "" if "is_deleted" not in col_types else " WHERE is_deleted=0"
        rows = (
            await session.execute(text(f"SELECT * FROM `{local}`{where}"))
        ).mappings().all()

    def column_of(field_name: str) -> str | None:
        if field_name in cols:
            return field_name
        if field_name in alias and alias[field_name] in cols:
            return alias[field_name]
        if field_name in label_map and label_map[field_name] in cols:
            return label_map[field_name]
        return None

    def local_value(row: Any, field_name: str, column: str | None) -> Any:
        if is_json:
            try:
                data = json.loads(row.get("data") or "{}")
            except Exception:
                data = {}
            return data.get(field_name)
        if column is None or column in SKIP_LOCAL_COLUMNS:
            return None
        return row.get(column)

    ctx["options"] = field_options

    def convert(field_type: int, value: Any) -> Any:
        if field_type == 1:
            text_value = as_text(value).strip()
            return text_value or SKIP
        if field_type == 2:
            number = as_number(value)
            return SKIP if number is None else number
        if field_type == 3:
            text_value = as_text(value).strip()
            if not text_value:
                return SKIP
            allowed = ctx["options"].get(field_name)
            if allowed is not None and text_value not in allowed:
                return SKIP
            return text_value
        if field_type == 4:
            many = as_multi(value)
            allowed = ctx["options"].get(field_name)
            if allowed:
                many = [one for one in many if one in allowed]
            return many or SKIP
        if field_type == 5:
            stamp = to_ms(value)
            return SKIP if stamp is None else stamp
        if field_type == 7:
            return as_bool(value) if value not in (None, "") else SKIP
        if field_type == 11:
            names = [n.strip() for n in as_text(value).replace("，", ",").split(",") if n.strip()]
            ids = [ctx["people"][n] for n in names if n in ctx["people"]]
            return [{"id": one} for one in ids] if ids else SKIP
        if field_type in (18, 21):
            return SKIP  # 关联字段单独处理
        return SKIP

    def key_of(table: str) -> str:
        for key, value in LINK_TABLES.items():
            if value == table:
                return key
        return table

    def live_ids(table: str) -> set[str]:
        return ctx["live"].get(table, set())

    def link_key_of(_table_name: str, field_name: str) -> str:
        pair = LINK_PAIRS.get((local, field_name))
        return pair[1] if pair else ""

    def link_value(row_id: int, field_name: str) -> Any:
        pair = LINK_PAIRS.get((local, field_name))
        if not pair:
            return SKIP
        from_key, to_key, link_field = pair
        here = key_of(local)
        out: list[str] = []
        for from_id, to_id in ctx["links"].get((from_key, to_key, link_field), []):
            if from_key == here and from_id == row_id:
                target = LINK_TABLES[to_key]
                rid = ctx["ids"][target].get(to_id)
                if rid and rid in live_ids(target):
                    out.append(rid)
            elif to_key == here and to_id == row_id:
                target = LINK_TABLES[from_key]
                rid = ctx["ids"][target].get(from_id)
                if rid and rid in live_ids(target):
                    out.append(rid)
        return sorted(set(out)) or SKIP

    updates: list[dict[str, Any]] = []
    creates: list[tuple[int, dict[str, Any]]] = []
    skipped_fields: dict[str, int] = {}

    for row in rows:
        rid = str(row.get("feishu_record_id") or "")
        row_id = int(row["id"])
        payload: dict[str, Any] = {}
        for field_name, field_type in fields.items():
            if field_type in (18, 21):
                what = link_value(row_id, field_name)
            elif field_type in WRITABLE_TYPES:
                column = column_of(field_name)
                raw = local_value(row, field_name, column)
                what = convert(field_type, raw) if raw not in (None, "", []) else SKIP
            else:
                skipped_fields[field_name] = field_type
                continue
            if isinstance(what, Skip):
                continue
            payload[field_name] = what

        if not payload:
            continue

        if rid and rid in existing:
            current = existing[rid]
            # 关联字段是集合：只做并集，绝不因为本地少一条就把飞书那条删掉
            for key in list(payload):
                if fields.get(key) not in (18, 21):
                    continue
                old = current.get(key)
                old_ids = old.get("link_record_ids") if isinstance(old, dict) else old
                target = LINK_TABLES.get(link_key_of(name, key), "")
                live = live_ids(target)
                merged = {
                    one
                    for one in set(map(str, payload[key])) | set(map(str, old_ids or []))
                    if not live or one in live
                }
                if not merged:
                    payload.pop(key, None)
                    continue
                payload[key] = sorted(merged)
            diff = {
                key: value
                for key, value in payload.items()
                if normalize_existing(fields[key], current.get(key)) != normalize_existing(fields[key], value)
            }
            if diff:
                if ctx.get("explain") and len(ctx.setdefault("explain_rows", [])) < 6:
                    ctx["explain_rows"].append(
                        {
                            "table": name,
                            "record": rid,
                            "old": {
                                key: normalize_existing(fields[key], current.get(key))
                                for key in diff
                            },
                            "new": diff,
                        }
                    )
                updates.append({"record_id": rid, "fields": diff})
        else:
            # 新建时跳过「测试」记录（用户 2026-11-24 确认）——这类行建到飞书只会变成噪音
            if any(isinstance(v, str) and "测试" in v for v in payload.values()):
                ctx.setdefault("skipped_test", []).append(f"{name}#{row_id}")
                continue
            creates.append((row_id, payload))
            if ctx.get("explain") and len(ctx.setdefault("create_rows", [])) < 30:
                ctx["create_rows"].append(
                    {
                        "table": name,
                        "row": row_id,
                        "keys": {
                            k: payload[k]
                            for k in list(payload)[:4]
                        },
                    }
                )

    if apply:
        errors: list[str] = []
        for start in range(0, len(updates), BATCH):
            chunk = updates[start : start + BATCH]
            url = f"{API}/bitable/v1/apps/{app}/tables/{table_id}/records/batch_update"
            try:
                req(url, tk, "POST", {"records": chunk})
            except Exception as exc:  # 整批失败就逐条重试，把坏记录单拎出来
                errors.append(f"update {start}: {type(exc).__name__}: {str(exc)[:160]}")
                for one in chunk:
                    try:
                        req(url, tk, "POST", {"records": [one]})
                    except Exception as inner:
                        errors.append(
                            f"update {one['record_id']}: {type(inner).__name__}: {str(inner)[:160]}"
                        )
        for start in range(0, len(creates), BATCH):
            chunk = creates[start : start + BATCH]
            try:
                result = req(
                    f"{API}/bitable/v1/apps/{app}/tables/{table_id}/records/batch_create",
                    tk,
                    "POST",
                    {"records": [{"fields": fields_payload} for _, fields_payload in chunk]},
                )
                new_items = result["data"].get("records", [])
                async with async_db_session() as session:
                    for (local_id, _), item in zip(chunk, new_items):
                        new_rid = str(item.get("record_id") or "")
                        if new_rid:
                            await session.execute(
                                text(f"UPDATE `{local}` SET feishu_record_id=:r WHERE id=:i"),
                                {"r": new_rid, "i": local_id},
                            )
                    await session.commit()
            except Exception as exc:
                errors.append(f"create {start}: {type(exc).__name__}: {str(exc)[:160]}")
        if errors:
            ctx.setdefault("errors", []).extend(f"{name}: {e}" for e in errors)

    return {
        "table": name,
        "local": local,
        "feishu": len(existing),
        "update": len(updates),
        "create": len(creates),
        "skipped_readonly": len(skipped_fields),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="真正写回飞书（默认只预演）")
    parser.add_argument("--only", default="")
    parser.add_argument("--explain", action="store_true", help="打印前几条差异明细")
    args = parser.parse_args()

    env = load_env()
    tk = token(env)
    async with async_db_session() as session:
        ctx: dict[str, Any] = {
            "links": await build_link_index(session),
            "people": await person_open_ids(session),
            "ids": {},
            "explain": args.explain,
        }
        for table in LINK_TABLES.values():
            ctx["ids"][table] = await feishu_ids(session, table)
    # 关联写入前要确认目标记录在飞书里真的存在（本地可能留着已删除记录的旧 id）
    ctx["live"] = {}
    for app, table_id, name, local in TABLES:
        for key, table in LINK_TABLES.items():
            if table == local:
                ctx["live"][table] = {
                    str(item.get("record_id")) for item in records(tk, app, table_id)
                }

    report = []
    for app, table_id, name, local in TABLES:
        if args.only and args.only not in local and args.only not in name:
            continue
        try:
            result = await push_table(tk, app, table_id, name, local, args.apply, ctx)
            print(json.dumps(result, ensure_ascii=False))
            report.append(result)
        except Exception as exc:
            print(
                json.dumps(
                    {"table": name, "error": type(exc).__name__ + ": " + str(exc)[:300]},
                    ensure_ascii=False,
                )
            )
    if args.explain:
        for row in ctx.get("explain_rows", []):
            print(json.dumps(row, ensure_ascii=False))
        for row in ctx.get("create_rows", []):
            print(json.dumps(row, ensure_ascii=False, default=str))
    print(
        json.dumps(
            {
                "mode": "apply" if args.apply else "dry-run",
                "at": datetime.now().isoformat(timespec="seconds"),
                "tables": len(report),
                "errors": ctx.get("errors", []),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
