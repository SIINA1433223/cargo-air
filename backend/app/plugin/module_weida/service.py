"""威达工作台的查询 / 更新服务。

只做三件事：读配置、按配置查本地 `biz_*`、按白名单更新。
不碰飞书；在线路径全部走本地 MySQL。
"""

from __future__ import annotations

import json
import math
import mimetypes
import os
import re
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_db_session
from app.utils.sm_crypto_util import PwdUtil

from .booking import render as render_booking
from .config import (
    DETAIL_ACTIONS,
    LABEL_FALLBACK,
    OPS_BASELINE_FEISHU_IDS,
    TYPE_MAP,
    LinkBlockDef,
    WorkbenchDef,
)
from .events import publish_event
from .quotation import render as render_quotation

MAX_PAGE_SIZE = 200
MAX_PAGE_SIZE_ALL = 1000
FILTER_OPS = {
    "eq",
    "ne",
    "contains",
    "is_empty",
    "is_not_empty",
    "gt",
    "lt",
    "date_today",
    "date_tomorrow",
    "date_this_week",
    "date_next_week",
    "date_last_7_days",
    "date_next_7_days",
    "date_on",
    "date_before",
    "date_after",
    "date_between",
}
OP_ALIASES = {"empty": "is_empty", "not_empty": "is_not_empty", "equals": "eq"}

# 系统/派生字段不允许人工改
SYSTEM_READONLY = {
    "id",
    "create_time",
    "update_time",
    "is_deleted",
    "opportunity_editor",
    "feishu_approval_code",
    "operation_single_status",
}


def editable_columns(wb: WorkbenchDef, available: set[str]) -> set[str]:
    """`editable=("*",)` 表示放开全部业务字段，只排除系统/派生字段。"""
    if "*" in wb.editable:
        return {c for c in available if c not in SYSTEM_READONLY}
    return set(wb.editable) & available


def clean_value(value: Any) -> Any:
    """还原飞书镜像里双重编码的 JSON / 文本数组。"""
    for _ in range(2):
        if not isinstance(value, str):
            return value
        raw = value.strip()
        if not raw:
            return value
        if raw.startswith(("{", "[", '"')):
            try:
                value = json.loads(raw)
                continue
            except Exception:
                if raw.startswith("{") and raw.endswith("}"):
                    body = raw[1:-1]
                    if body and ":" not in body:
                        return [x.strip().strip('"') for x in body.split(",") if x.strip()]
        return value
    return value


def _normalize_numeric_fields(meta: dict[str, dict[str, Any]], payload: dict[str, Any]) -> None:
    """数字列空字符串转 NULL，避免 MySQL 1366。"""
    for col, value in list(payload.items()):
        if meta.get(col, {}).get("type") not in ("number", "decimal"):
            continue
        if isinstance(value, str) and not value.strip():
            payload[col] = None


def _normalize_date_fields(meta: dict[str, dict[str, Any]], payload: dict[str, Any]) -> None:
    """空日期转 NULL，ISO 日期转 MySQL 可接受的空格格式。"""
    for col, value in list(payload.items()):
        if meta.get(col, {}).get("type") not in ("date", "datetime"):
            continue
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            payload[col] = None
        elif "T" in text:
            payload[col] = text.replace("T", " ")[:23]


def _normalize_json_fields(meta: dict[str, dict[str, Any]], payload: dict[str, Any]) -> None:
    """JSON 列必须收到合法 JSON；裸字符串自动编码，避免 MySQL 3140。"""
    for col, value in list(payload.items()):
        if meta.get(col, {}).get("type") != "json" or not isinstance(value, str):
            continue
        raw = value.strip()
        if not raw:
            payload[col] = None
            continue
        try:
            json.loads(raw)
        except Exception:
            payload[col] = json.dumps(raw)


def normalize(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return clean_value(value)


async def get_table_meta(session: AsyncSession, table: str) -> dict[str, dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                "SELECT COLUMN_NAME AS col, DATA_TYPE AS dtype, IS_NULLABLE AS nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = :t"
            ),
            {"t": table},
        )
    ).mappings().all()
    return {
        r["col"]: {
            "type": TYPE_MAP.get(str(r["dtype"]).lower(), "text"),
            "nullable": r["nullable"] == "YES",
        }
        for r in rows
    }


async def get_labels(session: AsyncSession, table: str) -> dict[str, str]:
    rows = (
        await session.execute(
            text("SELECT col, label FROM sys_field_label WHERE tbl = :t"), {"t": table}
        )
    ).all()
    labels = dict(rows)
    for col, label in LABEL_FALLBACK.items():
        labels.setdefault(col, label)
    return labels


VIRTUAL_COLUMNS = {
    "goods_data": {"label": "货物数据", "type": "textarea"},
    "final_plan": {"label": "最终方案", "type": "textarea"},
    "bill_status": {"label": "账单状态", "type": "text"},
}
BLOCK_VIRTUALS = {
    "opportunity_no": {"label": "商机编号", "type": "text"},
    "related_opportunity_no": {"label": "关联商机", "type": "text"},
    "latest_quote_deadline": {"label": "报价截止时间", "type": "datetime"},
    "adopted_plan_count": {"label": "被方案采用", "type": "text"},
    "deal_status": {"label": "是否达成", "type": "text"},
    "quote_create_week": {"label": "报价创建周", "type": "text"},
}


def build_columns(
    wb: WorkbenchDef,
    meta: dict[str, dict[str, Any]],
    labels: dict[str, str],
    editable: set[str],
) -> list[dict[str, Any]]:
    out = []
    for col in wb.columns:
        if col in meta:
            out.append(
                {
                    "column": col,
                    "label": labels.get(col, col),
                    "type": meta[col]["type"],
                    "editable": col in editable,
                }
            )
        elif col in VIRTUAL_COLUMNS:
            out.append(
                {
                    "column": col,
                    **VIRTUAL_COLUMNS[col],
                    "editable": False,
                    "virtual": True,
                }
            )
    return out


def _block_field(
    block: LinkBlockDef, col: str, block_meta: dict[str, tuple[dict[str, dict[str, Any]], dict[str, str]]]
) -> dict[str, Any]:
    meta, labels = block_meta[block.table]
    if col in meta:
        return {
            "column": col,
            "label": labels.get(col, col),
            "type": meta[col]["type"],
            "editable": True,
        }
    return {**BLOCK_VIRTUALS[col], "column": col, "editable": False}


async def get_config(
    role: str, wb: WorkbenchDef, viewer_user_id: int | None = None
) -> dict[str, Any]:
    async with async_db_session() as session:
        meta = await get_table_meta(session, wb.table)
        labels = await get_labels(session, wb.table)
        block_meta: dict[str, tuple[dict[str, dict[str, Any]], dict[str, str]]] = {}
        for block in (*wb.link_blocks, *wb.related_blocks):
            if block.table not in block_meta:
                block_meta[block.table] = (
                    await get_table_meta(session, block.table),
                    await get_labels(session, block.table),
                )
        current_staff = None
        if viewer_user_id:
            current_staff = (
                await session.execute(
                    text(
                        "SELECT id, name, role, roles, suda_user_id FROM weida_user_map "
                        "WHERE local_user_id = :uid AND active = 1 LIMIT 1"
                    ),
                    {"uid": viewer_user_id},
                )
            ).mappings().first()
    available = set(meta)
    editable = editable_columns(wb, available)
    columns = build_columns(wb, meta, labels, editable)

    def field_meta(col: str) -> dict[str, Any]:
        if col not in meta and col in VIRTUAL_COLUMNS:
            return {
                "column": col,
                **VIRTUAL_COLUMNS[col],
                "editable": False,
                "virtual": True,
            }
        return {
            "column": col,
            "label": labels.get(col, col),
            "type": meta[col]["type"],
            "editable": col in editable,
        }

    detail_fields = [
        field_meta(col) for col in meta if col not in wb.hidden
    ]

    sections: list[dict[str, Any]] = []
    used: set[str] = set()
    # 先处理点名分组，再让空分组收剩余字段；顺序仍按配置。
    for idx, (label, names) in enumerate(wb.detail_sections):
        if not names:
            continue
        seen: set[str] = set()
        fields = []
        for col in names:
            if col in seen or col in wb.hidden:
                continue
            if col not in meta and col not in VIRTUAL_COLUMNS:
                continue
            fields.append(field_meta(col))
            seen.add(col)
        if fields:
            sections.append((idx, {"label": label, "fields": fields}))
            used.update(f["column"] for f in fields)
    for idx, (label, names) in enumerate(wb.detail_sections):
        if names:
            continue
        fields = [
            field_meta(col)
            for col in meta
            if col not in used and col not in wb.hidden
        ]
        if fields:
            sections.append((idx, {"label": label, "fields": fields}))
            used.update(f["column"] for f in fields)

    block_labels = {block.label for block in wb.link_blocks}
    ordered_sections = [
        section
        for _idx, section in sorted(sections, key=lambda item: item[0])
        if section["label"] not in block_labels
    ]
    label_overrides = {(tab, col): label for tab, col, label in wb.section_labels}
    for section in ordered_sections:
        for f in section["fields"]:
            override = label_overrides.get((section["label"], f["column"]))
            if override:
                f["label"] = override
    payload = {
        "role": wb.role,
        "current_staff": dict(current_staff) if current_staff else None,
        "name": wb.name,
        "description": wb.description,
        "table": wb.table,
        "columns": columns,
        "search_fields": [c for c in wb.search_fields if c in available],
        "editable": sorted(editable),
        "detail_fields": detail_fields,
        "detail_sections": ordered_sections,
        "default_sort": {"field": wb.default_sort[0], "desc": wb.default_sort[1] == "desc"},
        "todo_checkboxes": [c for c in wb.todo_checkboxes if c in available],
        "link_blocks": [
            {
                "key": block.key,
                "label": block.label,
                "table": block.table,
                "columns": [
                    _block_field(block, col, block_meta)
                    for col in block.columns
                    if col in block_meta[block.table][0] or col in BLOCK_VIRTUALS
                ],
                "expand_fields": [
                    _block_field(block, col, block_meta)
                    for col in block.expand_fields
                    if col in block_meta[block.table][0] or col in BLOCK_VIRTUALS
                ],
                "footer_fields": [
                    {
                        "column": col,
                        "label": labels.get(col, col),
                        "type": meta[col]["type"],
                        "editable": col in editable,
                    }
                    for col in block.footer_fields
                    if col in meta
                ],
            }
            for block in wb.link_blocks
        ],
        "no_paging": wb.no_paging,
        "related_blocks": [
            {
                "key": rb.key,
                "label": rb.label,
                "table": rb.table,
                "when_field": rb.when_field,
                "when_fields": list(rb.when_fields),
                "when_contains": rb.when_contains,
                "columns": [
                    {
                        "column": col,
                        "label": dict(rb.column_labels).get(
                            col, block_meta[rb.table][1].get(col, col)
                        ),
                        "type": block_meta[rb.table][0][col]["type"],
                    }
                    for col in rb.columns
                    if col in block_meta[rb.table][0]
                ],
            }
            for rb in wb.related_blocks
        ],
        "commands": [
            {"key": key, "label": label, "tab": tab} for key, label, tab in wb.commands
        ],
        "detail_actions": [
            {
                "field": field,
                "label": DETAIL_ACTIONS[field][0],
                "done": DETAIL_ACTIONS[field][1],
                "tab": DETAIL_ACTIONS[field][2],
            }
            for field in wb.todo_checkboxes
            if field in available and field in DETAIL_ACTIONS
        ],
        "owner_field": wb.owner_field,
        "field_count": len(meta),
    }
    option_map: dict[str, list[str]] = {}
    try:
        async with async_db_session() as option_session:
            rows = (
                await option_session.execute(
                    text("SELECT category,value FROM weida_option WHERE active=1 ORDER BY sort,id")
                )
            ).all()
        for category, value in rows:
            raw = str(value or "").strip().strip('"')
            if raw.startswith("{") and raw.endswith("}"):
                raw = raw[1:-1]
            raw = raw.strip().strip('"')
            if raw:
                option_map.setdefault(str(category), []).append(raw)
    except Exception:
        option_map = {}
    aliases = {"goods_name_special_attr": "goods_special_attr"}
    for col in columns + detail_fields:
        opts = option_map.get(col.get("column", "")) or option_map.get(
            aliases.get(col.get("column", ""), "")
        )
        if opts:
            col["options"] = opts
    option_fields = {
        "biz_opportunity": {
            "business_type", "packaging_form", "goods_special_attr",
            "transport_clause", "approval_status",
        },
        "biz_order": {
            "transport_clause", "customs_declaration_method", "cost_currency",
            "flight_status", "goods_name_special_attr", "goods_special_attr",
            "exception_status", "leader_review_status", "operation_single_status",
        },
        "biz_ifreight": {
            "business_type", "piece_unit", "document_type", "insurance_clause",
            "cargo_receiving_method", "surcharge_payment_method",
            "freight_payment_method", "transport_mode", "transport_clause",
            "pp_currency", "currency", "poa_ai_prompt_type", "bill_prompt_type",
        },
        "biz_pricing": {
            "cost_currency", "quotation_status", "cabin_status",
        },
    }.get(wb.table, set())
    if option_fields:
        try:
            async with async_db_session() as opt_session:
                for candidate in option_fields:
                    if candidate not in meta or candidate not in {
                        c.get("column") for c in columns + detail_fields
                    }:
                        continue
                    rows = (
                        await opt_session.execute(
                            text(
                                f"SELECT DISTINCT `{candidate}` AS v FROM `{wb.table}` "
                                f"WHERE `{candidate}` IS NOT NULL AND `{candidate}` <> '' "
                                "ORDER BY v LIMIT 80"
                            )
                        )
                    ).scalars().all()
                    values = [str(v).strip().strip('"') for v in rows if str(v).strip()]
                    if not values:
                        continue
                    for col in columns + detail_fields:
                        if col.get("column") == candidate:
                            col["options"] = values
        except Exception:
            pass
    return payload


def _default_order_sql(wb: WorkbenchDef) -> str:
    """三个工作台的默认行序，按妙搭原配置复刻。"""
    if wb.role == "customer_service":
        return (
            "CASE WHEN is_goods_received IS NULL OR is_goods_received = '' THEN 0 "
            "WHEN is_goods_received = '是' THEN 1 WHEN is_goods_received = '否' THEN 2 ELSE 3 END, "
            "opportunity_no DESC, id DESC"
        )
    if wb.role == "pricing":
        order_exists = (
            "EXISTS (SELECT 1 FROM biz_link lo JOIN biz_order o ON o.id = lo.to_id "
            "AND o.is_deleted = 0 WHERE lo.from_table = 'opportunity' "
            "AND lo.from_id = biz_opportunity.id AND lo.to_table = 'order' "
            "AND lo.field = '订单表' AND o.%s = %s)"
        )
        priced = order_exists % ("route_manager_priced", "'已核价'")
        handed = order_exists % ("handover_domestic_operation", "1")
        no_quote = (
            "NOT EXISTS (SELECT 1 FROM biz_link lp JOIN biz_pricing pr ON pr.id = lp.to_id "
            "AND pr.is_deleted = 0 WHERE lp.from_table = 'opportunity' "
            "AND lp.from_id = biz_opportunity.id AND lp.to_table = 'pricing' "
            "AND lp.field = '航线报价')"
        )
        status = (
            f"CASE WHEN {no_quote} THEN 0 WHEN {priced} THEN 4 WHEN {handed} THEN 2 "
            f"WHEN DATE(latest_quote_deadline) = CURDATE() THEN 1 ELSE 3 END"
        )
        return (
            f"{status}, CASE WHEN {no_quote} THEN latest_quote_deadline END ASC, "
            "latest_quote_deadline DESC, id DESC"
        )
    if wb.role == "domestic_ops":
        return (
            "CASE WHEN send_forecast = 1 THEN 0 "
            "WHEN handover_domestic_operation = 1 THEN 1 ELSE 2 END, "
            "warehouse_in_operation_time ASC, id DESC"
        )
    order_col = wb.default_sort[0]
    desc = wb.default_sort[1] == "desc"
    return f"`{order_col}` {'DESC' if desc else 'ASC'}, id DESC"


def _parse_filters(raw: str | None, allowed: set[str]) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=422, detail="filters 不是合法 JSON") from e
    if not isinstance(data, list):
        raise HTTPException(status_code=422, detail="filters 必须是数组")
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        col = str(item.get("column", ""))
        op = OP_ALIASES.get(str(item.get("op", "eq")), str(item.get("op", "eq")))
        if col not in allowed or op not in FILTER_OPS:
            continue
        out.append(
            {
                "column": col,
                "op": op,
                "value": item.get("value"),
                "value2": item.get("value2"),
            }
        )
    return out


KEYWORD_TYPES = {"text", "textarea", "select", "multiselect", "json", "date", "datetime", "number"}


def _where_clause(
    wb: WorkbenchDef,
    allowed: set[str],
    keyword: str | None,
    filters: list[dict[str, Any]],
    params: dict[str, Any],
    conjunction: str = "and",
    meta: dict[str, dict[str, Any]] | None = None,
) -> str:
    clauses = ["is_deleted = 0"]
    if keyword:
        # 与妙搭一致：关键词搜**这一行的全部字段**（不限 search_fields）。
        # 之前只搜 search_fields，操作台没点 商机编号，所以搜不到；
        # 这里按物理列类型挑可搜的，再并上 search_fields。
        fields = list(wb.search_fields)
        if meta:
            fields += [
                col
                for col, info in meta.items()
                if col not in wb.hidden
                and str(info.get("type") or "").split("(")[0].strip().lower() in KEYWORD_TYPES
            ]
        fields = [c for c in dict.fromkeys(fields) if c in allowed]
        if fields:
            ors = []
            params["kw"] = f"%{keyword}%"
            for i, col in enumerate(fields):
                key = f"kw{i}"
                params[key] = params["kw"]
                ors.append(f"`{col}` LIKE :{key}")
            clauses.append("(" + " OR ".join(ors) + ")")

    filter_clauses: list[str] = []
    person_filter_columns = {
        "opportunity_person_in_charge", "responsible_route", "domestic_operation",
        "sales_person", "operator", "quotation_person_in_charge",
    }
    for i, f in enumerate(filters):
        col, op, value = f["column"], f["op"], f["value"]
        col_sql = f"JSON_UNQUOTE(`{col}`)" if col in person_filter_columns else f"`{col}`"
        key = f"f{i}"
        if op == "eq":
            filter_clauses.append(f"{col_sql} = :{key}")
            params[key] = value
        elif op == "ne":
            filter_clauses.append(f"({col_sql} IS NULL OR {col_sql} <> :{key})")
            params[key] = value
        elif op == "contains":
            filter_clauses.append(f"{col_sql} LIKE :{key}")
            params[key] = f"%{value}%"
        elif op == "is_empty":
            filter_clauses.append(f"({col_sql} IS NULL OR {col_sql} = '')")
        elif op == "is_not_empty":
            filter_clauses.append(f"({col_sql} IS NOT NULL AND {col_sql} <> '')")
        elif op in ("gt", "lt"):
            filter_clauses.append(f"`{col}` {'>' if op == 'gt' else '<'} :{key}")
            params[key] = value
        elif op == "date_today":
            filter_clauses.append(f"DATE(`{col}`) = CURDATE()")
        elif op == "date_tomorrow":
            filter_clauses.append(f"DATE(`{col}`) = CURDATE() + INTERVAL 1 DAY")
        elif op == "date_this_week":
            filter_clauses.append(f"YEARWEEK(`{col}`, 1) = YEARWEEK(CURDATE(), 1)")
        elif op == "date_next_week":
            filter_clauses.append(
                f"YEARWEEK(`{col}`, 1) = YEARWEEK(CURDATE(), 1) + 1"
            )
        elif op == "date_last_7_days":
            filter_clauses.append(
                f"`{col}` >= CURDATE() - INTERVAL 6 DAY "
                f"AND `{col}` < CURDATE() + INTERVAL 1 DAY"
            )
        elif op == "date_next_7_days":
            filter_clauses.append(
                f"`{col}` >= CURDATE() AND `{col}` < CURDATE() + INTERVAL 7 DAY"
            )
        elif op == "date_on":
            filter_clauses.append(f"DATE(`{col}`) = :{key}")
            params[key] = value
        elif op == "date_before":
            filter_clauses.append(f"DATE(`{col}`) < :{key}")
            params[key] = value
        elif op == "date_after":
            filter_clauses.append(f"DATE(`{col}`) > :{key}")
            params[key] = value
        elif op == "date_between":
            k2 = f"{key}b"
            filter_clauses.append(f"DATE(`{col}`) BETWEEN :{key} AND :{k2}")
            params[key] = value
            params[k2] = f.get("value2")

    if filter_clauses:
        joiner = " OR " if conjunction == "or" else " AND "
        clauses.append("(" + joiner.join(filter_clauses) + ")")
    return " WHERE " + " AND ".join(clauses)


def _row_to_item(row: Any, labels: dict[str, str]) -> dict[str, Any]:
    fields = {}
    for col, value in row.items():
        if col in ("id", "is_deleted", "create_by", "update_by", "feishu_record_id"):
            continue
        fields[col] = {"label": labels.get(col, col), "value": normalize(value)}
    return {"id": row["id"], "fields": fields}


PERSON_FIELDS = {
    "opportunity_person_in_charge",
    "quotation_person_in_charge",
    "merchant_person_in_charge",
    "sales_person",
    "domestic_operation",
    "responsible_route",
}

_PERSON_ID_RE = re.compile(r"[({]?\s*(\d{10,})\s*[)}]?")


def _num_text(value: Any) -> str:
    if value is None or value == "":
        return ""
    try:
        return f"{float(value):.4f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(value)


def _cargo_text(row: Any) -> str:
    head = "/".join(
        _num_text(row.get(key)) for key in ("quantity", "gross_weight", "volume")
    )
    lines = [head] if head.strip("/") else []
    dimension = str(row.get("dimension") or "").strip()
    if dimension:
        lines.append(dimension)
    return "\n".join(lines)


def _ms(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return int(value.timestamp() * 1000)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def create_handover(order_id: int, to_name: str, from_name: str | None = None) -> dict[str, Any]:
    now = datetime.now()
    days = (7 - now.weekday()) if now.weekday() >= 4 else 2
    end_at = now + timedelta(days=days)
    async with async_db_session() as session:
        await session.execute(
            text("UPDATE weida_handover SET cancel_at=NOW(3) WHERE order_id=:id AND cancel_at IS NULL"),
            {"id": order_id},
        )
        result = await session.execute(
            text(
                "INSERT INTO weida_handover (order_id,from_name,to_name,start_at,end_at) "
                "VALUES (:id,:fname,:tname,:start,:end)"
            ),
            {"id": order_id, "fname": from_name, "tname": to_name, "start": now, "end": end_at},
        )
        await session.commit()
        hid = int(result.lastrowid)
    return {"id": hid, "to_name": to_name, "end_at": str(end_at)}


async def cancel_handover(order_id: int) -> None:
    async with async_db_session() as session:
        await session.execute(
            text("UPDATE weida_handover SET cancel_at=NOW(3) WHERE order_id=:id AND cancel_at IS NULL"),
            {"id": order_id},
        )
        await session.commit()


async def save_check(order_id: int, kind: str, user_name: str, items: dict[str, Any], confirmed: bool = False) -> dict[str, Any]:
    async with async_db_session() as session:
        await session.execute(
            text(
                "INSERT INTO weida_check (order_id,user_name,kind,items,confirmed) "
                "VALUES (:oid,:uname,:kind,:items,:confirmed) ON DUPLICATE KEY UPDATE "
                "items=VALUES(items), confirmed=VALUES(confirmed), updated_at=NOW(3)"
            ),
            {
                "oid": order_id,
                "uname": user_name,
                "kind": kind,
                "items": json.dumps(items, ensure_ascii=False),
                "confirmed": 1 if confirmed else 0,
            },
        )
        await session.commit()
    return {"order_id": order_id, "kind": kind, "items": items, "confirmed": confirmed}


async def get_checks(order_id: int) -> dict[str, Any]:
    async with async_db_session() as session:
        rows = (
            await session.execute(
                text("SELECT user_name,kind,items,confirmed FROM weida_check WHERE order_id=:id"),
                {"id": order_id},
            )
        ).mappings().all()
    out: dict[str, Any] = {}
    for row in rows:
        try:
            items = json.loads(row["items"] or "{}")
        except (TypeError, json.JSONDecodeError):
            items = {}
        out[row["kind"]] = {"user_name": row["user_name"], "items": items, "confirmed": bool(row["confirmed"])}
    return out


async def mark_flight_seen(order_id: int) -> None:
    async with async_db_session() as session:
        row = (
            await session.execute(
                text("SELECT * FROM biz_order WHERE id=:id"), {"id": order_id}
            )
        ).mappings().first()
        if not row:
            return
        sig = _flight_signature(row)
        await session.execute(
            text(
                "INSERT INTO weida_flight_watch (order_id,baseline) VALUES (:id,:sig) "
                "ON DUPLICATE KEY UPDATE baseline=VALUES(baseline), updated_at=NOW(3)"
            ),
            {"id": order_id, "sig": sig},
        )
        await session.commit()


async def sync_approval_statuses() -> int:
    token = await _feishu_token()
    if not token:
        return 0
    async with async_db_session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id,feishu_approval_code FROM biz_opportunity WHERE is_deleted=0 "
                    "AND approval_status='审批中' AND feishu_approval_code IS NOT NULL "
                    "AND feishu_approval_code<>'' LIMIT 50"
                )
            )
        ).mappings().all()
    updated = 0
    for row in rows:
        code = str(row["feishu_approval_code"])

        def fetch(code: str = code) -> dict[str, Any]:
            import urllib.request

            req = urllib.request.Request(
                f"https://open.feishu.cn/open-apis/approval/v4/instances/{code}",
                headers={"Authorization": f"Bearer {token}"},
            )
            return json.load(urllib.request.urlopen(req, timeout=20))

        try:
            data = await run_in_threadpool(fetch, code)
        except Exception:
            continue
        status = str((data.get("data") or {}).get("status") or "").upper()
        mapping = {"APPROVED": "已通过", "REJECTED": "已驳回", "CANCELED": "已撤销"}
        if status not in mapping:
            continue
        async with async_db_session() as session:
            await session.execute(
                text("UPDATE biz_opportunity SET approval_status=:status WHERE id=:id"),
                {"status": mapping[status], "id": row["id"]},
            )
            await session.commit()
        updated += 1
    return updated


async def _ops_alerts(session: AsyncSession, rows: list[Any]) -> dict[int, list[dict[str, str]]]:
    ids = [int(r["id"]) for r in rows]
    if not ids:
        return {}
    day = 86_400_000
    now_ms = int(time.time() * 1000)
    bill_ok: set[int] = set()
    overseas_ok: set[int] = set()
    for order_id, field in (
        await session.execute(
            text(
                "SELECT l.from_id, a.field FROM biz_link l "
                "JOIN biz_attachment a ON a.owner_table='ifreight' AND a.owner_id=l.to_id "
                "WHERE l.from_table='order' AND l.to_table='ifreight' "
                "AND l.from_id IN :ids AND a.field IN ('账单附件','国外账单附件')"
            ).bindparams(bindparam("ids", expanding=True)),
            {"ids": ids},
        )
    ).all():
        if field == "账单附件":
            bill_ok.add(int(order_id))
        else:
            overseas_ok.add(int(order_id))
    invoice_ok = {
        int(r[0])
        for r in (
            await session.execute(
                text(
                    "SELECT owner_id FROM biz_attachment WHERE owner_table='order' "
                    "AND field='发票' AND owner_id IN :ids"
                ).bindparams(bindparam("ids", expanding=True)),
                {"ids": ids},
            )
        ).all()
    }
    freight_feedback: dict[int, list[dict[str, Any]]] = {}
    try:
        for fb in (
            await session.execute(
                text(
                    "SELECT l.from_id, i.id AS iid, i.house_bl_no, i.quote_entry_feedback, "
                    "i.domestic_bill_entry_feedback, i.foreign_bill_entry_feedback "
                    "FROM biz_link l JOIN biz_ifreight i ON i.id=l.to_id AND i.is_deleted=0 "
                    "WHERE l.from_table='order' AND l.to_table='ifreight' "
                    "AND l.field='所属订单' AND l.from_id IN :ids"
                ).bindparams(bindparam("ids", expanding=True)),
                {"ids": ids},
            )
        ).mappings().all():
            oid = int(fb["from_id"])
            for text_key, field, label in (
                ("quote_entry_feedback", "报价单录入", "报价单录入反馈"),
                ("domestic_bill_entry_feedback", "国内账单录入", "国内账单录入反馈"),
                ("foreign_bill_entry_feedback", "国外账单录入", "国外账单录入反馈"),
            ):
                text_value = str(fb.get(text_key) or "").strip()
                if not text_value:
                    continue
                bad = any(word in text_value for word in ("失败", "错误", "异常", "未成功", "超时"))
                freight_feedback.setdefault(oid, []).append(
                    {
                        "severity": "critical" if bad else "warning",
                        "title": label,
                        "detail": text_value[:80],
                        "action": {
                            "kind": "tick",
                            "table": "ifreight",
                            "record_id": int(fb["iid"]),
                            "field": field,
                            "label": "重新录入",
                        },
                    }
                )
    except Exception:
        pass
    fee_alerts: dict[int, list[dict[str, Any]]] = {}
    try:
        for fee in (
            await session.execute(
                text(
                    "SELECT order_id, id, fee_category, fee_entry_status FROM biz_extrafee "
                    "WHERE is_deleted=0 AND order_id IN :ids AND fee_entry_status IS NOT NULL "
                    "AND fee_entry_status<>''"
                ).bindparams(bindparam("ids", expanding=True)),
                {"ids": ids},
            )
        ).mappings().all():
            text_value = str(fee["fee_entry_status"] or "").strip()
            if not text_value:
                continue
            bad = any(word in text_value for word in ("失败", "错误", "异常", "未成功", "超时"))
            fee_alerts.setdefault(int(fee["order_id"]), []).append(
                {
                    "severity": "critical" if bad else "warning",
                    "title": f"额外费用录入：{fee.get('fee_category') or fee['id']}",
                    "detail": text_value[:80],
                    "action": {
                        "kind": "fee",
                        "table": "extrafee",
                        "record_id": int(fee["id"]),
                        "field": "extra_fee_entry",
                        "label": "重新录入",
                    },
                }
            )
    except Exception:
        pass
    out: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        oid = int(row["id"])
        alerts: list[dict[str, str]] = []
        flight = _ms(row.get("flight"))
        if (
            flight is not None
            and now_ms >= flight + 3 * day
            and now_ms - (flight + 3 * day) <= 14 * day
            and oid not in bill_ok
        ):
            alerts.append(
                {
                    "severity": "critical",
                    "title": "未收到国内账单",
                    "detail": "航班已过 3 天，仍没有账单附件",
                }
            )
        delivery = _ms(row.get("delivery_to_door_time"))
        if (
            delivery is not None
            and now_ms >= delivery + 3 * day
            and now_ms - (delivery + 3 * day) <= 14 * day
            and oid not in overseas_ok
        ):
            alerts.append(
                {
                    "severity": "critical",
                    "title": "未收到国外账单",
                    "detail": "派送到门已过 3 天，仍没有国外账单附件",
                }
            )
        sent = _ms(row.get("bill_sent_time"))
        confirm = _ms(row.get("bill_confirm_time"))
        if confirm is not None:
            # 正向态：确认了就留一条 info 历史，不再闪（与妙搭同一口径）
            alerts.append(
                {
                    "severity": "info",
                    "title": "客人已确认账单",
                    "detail": "账单已确认",
                }
            )
        elif sent is not None and now_ms - sent >= day:
            alerts.append(
                {
                    "severity": "warning",
                    "title": "客人未确认账单",
                    "detail": "账单发出超过 24 小时仍未确认",
                }
            )
        ticked = row.get("invoice_issuance") in (1, True, "1")
        invoice_time = _ms(row.get("invoice_time"))
        if oid in invoice_ok:
            # 正向态：发票附件到了 → info 历史，不再闪
            alerts.append(
                {
                    "severity": "info",
                    "title": "财务已开票",
                    "detail": "发票附件已回传",
                }
            )
        elif ticked and (invoice_time is None or now_ms >= invoice_time + day):
            alerts.append(
                {
                    "severity": "critical",
                    "title": "财务尚未开票",
                    "detail": "已勾开票，但订单上还没有发票附件",
                }
            )
        feedback = str(row.get("invoice_system_feedback") or "").strip()
        if feedback:
            bad = any(word in feedback for word in ("失败", "错误", "异常", "未成功", "超时"))
            alerts.append(
                {
                    "severity": "critical" if bad else "warning",
                    "title": "系统开票反馈",
                    "detail": feedback[:80],
                }
            )
        alerts.extend(freight_feedback.get(oid, []))
        alerts.extend(fee_alerts.get(oid, []))
        if alerts:
            # 「点掉不闪」按数据签名记：数据一变签名就变，提醒自己回来。
            for a in alerts:
                a.setdefault("key", a["title"])
                a.setdefault("signature", f'{a["title"]}|{a.get("detail", "")}')
            alerts.sort(key=lambda a: 0 if a["severity"] == "critical" else 1)
            out[oid] = alerts
    return out


async def _customer_flight_map(
    session: AsyncSession, opp_ids: list[int]
) -> dict[int, dict[str, Any]]:
    if not opp_ids:
        return {}
    rows = (
        await session.execute(
            text(
                "SELECT l.from_id, o.flight_status, o.full_flight_info, o.agent_deadline, "
                "o.latest_port_arrival_time FROM biz_link l JOIN biz_order o ON o.id=l.to_id "
                "AND o.is_deleted=0 WHERE l.from_table='opportunity' AND l.to_table='order' "
                "AND l.field='订单表' AND l.from_id IN :ids ORDER BY o.id DESC"
            ).bindparams(bindparam("ids", expanding=True)),
            {"ids": opp_ids},
        )
    ).mappings().all()
    out: dict[int, dict[str, Any]] = {}
    for row in rows:
        out.setdefault(int(row["from_id"]), dict(row))
    return out


async def _ops_goods_data(session: AsyncSession, order_ids: list[int]) -> dict[int, str]:
    if not order_ids:
        return {}
    rows = (
        await session.execute(
            text(
                "SELECT DISTINCT l.from_id AS order_id, i.id AS ifreight_id, i.house_bl_no, "
                "i.quantity, i.gross_weight, i.volume, i.dimension FROM biz_link l "
                "JOIN biz_ifreight i ON i.id=l.to_id "
                "AND i.is_deleted=0 WHERE l.from_table='order' AND l.to_table='ifreight' "
                "AND l.field='所属订单' AND l.from_id IN :ids ORDER BY i.id"
            ).bindparams(bindparam("ids", expanding=True)),
            {"ids": order_ids},
        )
    ).mappings().all()
    grouped: dict[int, dict[tuple[str, ...], tuple[str, str]]] = {}
    for row in rows:
        text_value = _cargo_text(row)
        if not text_value:
            continue
        oid = int(row["order_id"])
        data_key = (
            _num_text(row.get("quantity")),
            _num_text(row.get("gross_weight")),
            _num_text(row.get("volume")),
            str(row.get("dimension") or ""),
        )
        hawb = str(row.get("house_bl_no") or "")
        current = grouped.setdefault(oid, {}).get(data_key)
        if current is None or (not current[0] and hawb):
            grouped[oid][data_key] = (hawb, text_value)
    out: dict[int, str] = {}
    for oid, blocks in grouped.items():
        parts = list(blocks.values())
        out[oid] = "\n".join(text for _hawb, text in parts)
    return out


def _normalize_goods_text(value: Any) -> str:
    text_value = str(value or "").replace("\\n", "\n")
    text_value = re.sub(r"(?<![\d.])(\d+)\.0+(?![\d])", r"\1", text_value)
    text_value = re.sub(r"(\d+\.\d*?)0+(?![\d])", r"\1", text_value)
    return "\n".join(line.strip() for line in text_value.split("\n") if line.strip())


async def _supplier_map(session: AsyncSession) -> dict[str, str]:
    try:
        rows = (
            await session.execute(
                text(
                    "SELECT merchant_full_name, merchant_short_name FROM biz_vendor "
                    "WHERE is_deleted=0 AND merchant_full_name IS NOT NULL "
                    "AND merchant_full_name<>'' AND merchant_short_name IS NOT NULL "
                    "AND merchant_short_name<>''"
                )
            )
        ).all()
    except Exception:
        return {}
    return {str(r[0]).strip(): str(r[1]).strip() for r in rows}


def _apply_supplier(item: dict[str, Any], suppliers: dict[str, str]) -> None:
    if not suppliers:
        return
    for key in ("supplier", "app_supplier"):
        field = item.get("fields", {}).get(key)
        if not field:
            continue
        value = field.get("value")
        if isinstance(value, str):
            field["value"] = suppliers.get(value.strip(), value)


async def _people_map(session: AsyncSession) -> dict[str, str]:
    try:
        rows = (
            await session.execute(
                text("SELECT suda_user_id, name FROM weida_user_map WHERE active=1")
            )
        ).all()
    except Exception:
        return {}
    people = {str(r[0]): str(r[1]) for r in rows if r[0] and r[1]}
    # 妙搭历史人员兜底（不在当前 weida_user_map 里的旧管理员）
    people.setdefault("1859891022399531", "Heisenberg Jiang")
    return people


def _replace_people(value: Any, people: dict[str, str]) -> Any:
    if isinstance(value, str):
        def repl(match: re.Match[str]) -> str:
            return people.get(match.group(1), match.group(1))

        return _PERSON_ID_RE.sub(repl, value).replace("{", "").replace("}", "").strip().strip('"')
    if isinstance(value, list):
        return [_replace_people(x, people) for x in value]
    if isinstance(value, dict):
        return {k: _replace_people(v, people) for k, v in value.items()}
    return value


def _apply_people(item: dict[str, Any], people: dict[str, str]) -> None:
    if not people:
        return
    for key, field in item.get("fields", {}).items():
        if key in PERSON_FIELDS:
            field["value"] = _replace_people(field.get("value"), people)


async def _viewer_scope(
    session: AsyncSession,
    wb: WorkbenchDef,
    viewer_user_id: int | None,
    viewer_username: str | None,
    params: dict[str, Any],
) -> str:
    """行级权限：客服看本人商机，操作看本人操作，航线看本人或未分配；管理员全量。"""
    if not wb.owner_field or not viewer_user_id:
        return ""
    if viewer_username in ("super", "admin"):
        return ""
    try:
        row = (
            await session.execute(
                text(
                    "SELECT su.name, su.username, su.is_superuser, u.suda_user_id, "
                    "u.role, u.roles FROM sys_user su LEFT JOIN weida_user_map u "
                    "ON u.name = su.name WHERE su.id = :id"
                ),
                {"id": viewer_user_id},
            )
        ).mappings().first()
    except Exception:
        return ""
    if not row:
        return ""
    try:
        roles = json.loads(row.get("roles") or "[]")
    except (TypeError, json.JSONDecodeError):
        roles = []
    if row.get("is_superuser") or (
        isinstance(roles, list) and any(r in ("admin", "super_admin") for r in roles)
    ):
        return ""
    name = str(row.get("name") or viewer_username or "").strip()
    if wb.role == "customer_service":
        if not name and not row.get("suda_user_id"):
            return ""
        params["owner_like_name"] = f"%{name}%"
        if row.get("suda_user_id"):
            params["owner_like_suda"] = f"%{row['suda_user_id']}%"
            return (
                f"(`{wb.owner_field}` LIKE :owner_like_suda "
                f"OR `{wb.owner_field}` LIKE :owner_like_name)"
            )
        return f"`{wb.owner_field}` LIKE :owner_like_name"
    if wb.role == "domestic_ops":
        if not name:
            return ""
        params["owner_name"] = f"%{name}%"
        return (
            f"(`{wb.owner_field}` IS NULL OR `{wb.owner_field}` = '' "
            f"OR `{wb.owner_field}` LIKE :owner_name)"
        )
    if wb.role == "pricing":
        if not name:
            return ""
        params["owner_name"] = f"%{name}%"
        return (
            f"(`{wb.owner_field}` IS NULL OR `{wb.owner_field}` = '' "
            f"OR `{wb.owner_field}` LIKE :owner_name)"
        )
    return ""


BILL_STEPS = (
    "",
    "没有国内账单",
    "没有国外账单",
    "未发送账单确认",
    "已发送账单确认",
    "客人未确认",
    "账单已确认",
    "已申请开票（发票未开具）",
    "发票已开具（未提供客户发票）",
    "开票完成",
)


async def _customer_bill_alerts(
    session: AsyncSession, opp_ids: list[int]
) -> dict[int, str]:
    """商机 → 它关联订单里「客人未确认」的那一档（客服单票状态格的黄叹号）。"""
    if not opp_ids:
        return {}
    orders = (
        await session.execute(
            text(
                "SELECT l.from_id AS opp_id, o.* FROM biz_link l "
                "JOIN biz_order o ON o.id = l.to_id AND o.is_deleted = 0 "
                "WHERE l.from_table = 'opportunity' AND l.to_table = 'order' "
                "AND l.field = '订单表' AND l.from_id IN :ids"
            ).bindparams(bindparam("ids", expanding=True)),
            {"ids": opp_ids},
        )
    ).mappings().all()
    if not orders:
        return {}
    statuses = await _ops_bill_status(session, orders)
    out: dict[int, str] = {}
    for order in orders:
        label = statuses.get(int(order["id"]), {}).get("label")
        if label == "客人未确认":
            out[int(order["opp_id"])] = label
    return out


async def _ops_bill_status(session: AsyncSession, rows: list[Any]) -> dict[int, dict[str, Any]]:
    ids = [int(r["id"]) for r in rows]
    if not ids:
        return {}
    bills: dict[int, dict[str, bool]] = {}
    result = await session.execute(
        text(
            "SELECT l.from_id, MAX(IFNULL(i.domestic_bill_entry,0)) AS domestic, "
            "MAX(IFNULL(i.foreign_bill_entry,0)) AS foreign_bill "
            "FROM biz_link l JOIN biz_ifreight i ON i.id=l.to_id AND i.is_deleted=0 "
            "WHERE l.from_table='order' AND l.to_table='ifreight' "
            "AND l.field='所属订单' AND l.from_id IN :ids GROUP BY l.from_id"
        ).bindparams(bindparam("ids", expanding=True)),
        {"ids": ids},
    )
    for r in result.mappings().all():
        bills[int(r["from_id"])] = {
            "domestic": bool(r["domestic"]),
            "foreign": bool(r["foreign_bill"]),
        }
    out: dict[int, dict[str, Any]] = {}
    now = datetime.now()
    for row in rows:
        rid = int(row["id"])
        step = row.get("bill_status_step")
        if isinstance(step, int) and 0 < step < len(BILL_STEPS):
            out[rid] = {"label": BILL_STEPS[step], "step": step}
            continue
        b = bills.get(rid, {})
        sent = str(row.get("confirm_bill_sent_to_customer") or "").strip() == "是"
        confirmed = str(row.get("customer_reply_confirmation") or "").strip() == "是"
        if str(row.get("invoice_sent_to_customer") or "").strip() == "是":
            out[rid] = {"label": "开票完成", "step": None}
        elif str(row.get("finance_invoice_issued") or "").strip() == "是":
            out[rid] = {"label": "发票已开具（未提供客户发票）", "step": None}
        elif row.get("invoice_issuance") == 1:
            out[rid] = {"label": "已申请开票（发票未开具）", "step": None}
        elif confirmed:
            out[rid] = {"label": "账单已确认", "step": None}
        elif sent:
            sent_at = row.get("bill_sent_time")
            if sent_at and (now - sent_at).days >= 3:
                out[rid] = {"label": "客人未确认", "step": None}
            else:
                out[rid] = {"label": "已发送账单确认", "step": None}
        elif b.get("foreign"):
            out[rid] = {"label": "未发送账单确认", "step": None}
        elif b.get("domestic"):
            out[rid] = {"label": "没有国外账单", "step": None}
        else:
            out[rid] = {"label": "没有国内账单", "step": None}
    return out


async def _pricing_virtuals(session: AsyncSession, rows: list[Any]) -> dict[int, dict[str, str]]:
    """航线工作台的「货物数据」「最终方案」合成列。"""
    ids = [int(r["id"]) for r in rows]
    if not ids:
        return {}
    orders: dict[int, list[Any]] = {}
    result = await session.execute(
        text(
            "SELECT l.from_id, o.supplier, o.cost_amount, o.cost_currency, "
            "o.departure_location, o.destination, o.level_ratio_req, "
            "o.handover_domestic_operation "
            "FROM biz_link l JOIN biz_order o ON o.id=l.to_id AND o.is_deleted=0 "
            "WHERE l.from_table='opportunity' AND l.to_table='order' "
            "AND l.field='订单表' AND l.from_id IN :ids"
        ).bindparams(bindparam("ids", expanding=True)),
        {"ids": ids},
    )
    for order in result.mappings().all():
        orders.setdefault(int(order["from_id"]), []).append(order)
    out: dict[int, dict[str, str]] = {}
    for row in rows:
        rid = int(row["id"])

        def _num(value: Any, digits: int) -> str:
            if value is None or value == "":
                return ""
            try:
                return f"{float(value):.{digits}f}".rstrip("0").rstrip(".")
            except (TypeError, ValueError):
                return str(value)

        parts = [
            _num(row.get("piece_count"), 0),
            _num(row.get("gross_weight_kg"), 2),
            _num(row.get("volume_m3"), 4),
        ]
        head = "/".join(p for p in parts if p)
        size = str(row.get("size_length_width_height_quantity") or "").strip()
        goods_data = f"{head}\n{size}" if size else head
        final_plan = ""
        for order in orders.get(rid, []):
            if order.get("handover_domestic_operation") != 1:
                continue
            bits = [
                str(order.get("supplier") or "").strip(),
                " ".join(
                    x for x in (
                        str(order.get("cost_amount") or "").strip(),
                        str(order.get("cost_currency") or "").strip(),
                    ) if x
                ),
                "/".join(
                    x for x in (
                        str(order.get("departure_location") or "").strip(),
                        str(order.get("destination") or "").strip(),
                    ) if x
                ),
                str(order.get("level_ratio_req") or "").strip(),
            ]
            final_plan = "\n".join(b for b in bits if b)
            break
        out[rid] = {"goods_data": goods_data, "final_plan": final_plan}
    return out


def _flight_signature(row: Any) -> str:
    bits = [
        str(row.get("flight_status") or ""),
        str(row.get("full_flight_info") or ""),
        str(row.get("agent_deadline") or ""),
        str(row.get("latest_port_arrival_time") or ""),
    ]
    return "|".join(bits)


async def _apply_ops_overlays(
    session: AsyncSession, wb: WorkbenchDef, items: list[dict[str, Any]]
) -> None:
    if wb.role != "domestic_ops" or not items:
        return
    ids = [int(x["id"]) for x in items]
    now = datetime.now()
    handovers = {
        int(r[0]): dict(r._mapping)
        for r in (
            await session.execute(
                text(
                    "SELECT * FROM weida_handover WHERE order_id IN :ids AND cancel_at IS NULL "
                    "AND start_at<=:now AND end_at>=:now ORDER BY id DESC"
                ).bindparams(bindparam("ids", expanding=True)),
                {"ids": ids, "now": now},
            )
        ).all()
    }
    checks = {
        (int(r[0]), str(r[1])): dict(r._mapping)
        for r in (
            await session.execute(
                text(
                    "SELECT order_id,kind,user_name,items,confirmed FROM weida_check "
                    "WHERE order_id IN :ids"
                ).bindparams(bindparam("ids", expanding=True)),
                {"ids": ids},
            )
        ).all()
    }
    watches = {
        int(r[0]): r[1]
        for r in (
            await session.execute(
                text("SELECT order_id,baseline FROM weida_flight_watch WHERE order_id IN :ids")
                .bindparams(bindparam("ids", expanding=True)),
                {"ids": ids},
            )
        ).all()
    }
    for item in items:
        oid = int(item["id"])
        handover = handovers.get(oid)
        if handover:
            item["handover"] = {
                "to_name": handover.get("to_name"),
                "end_at": str(handover.get("end_at") or ""),
                "can_cancel": True,
            }
            item["fields"]["single_status"] = {"label": "单票状态", "value": "周末交接"}
        # weekend check: 入仓时间落在周六/周日
        raw = item["fields"].get("warehouse_in_operation_time", {}).get("value")
        pending = False
        check_items = {"insured": False, "declDocs": False, "declChecked": False, "clearDocs": False, "customerContact": False}
        if raw:
            try:
                when = raw if isinstance(raw, datetime) else datetime.fromisoformat(str(raw)[:19])
                if when.weekday() >= 5 and str(item["fields"].get("single_status", {}).get("value")) != "已完成":
                    saved = checks.get((oid, "weekend"))
                    if saved:
                        try:
                            check_items = json.loads(saved.get("items") or "{}") or check_items
                        except (TypeError, json.JSONDecodeError):
                            pass
                    pending = not all(check_items.values())
            except (TypeError, ValueError):
                pass
        item["weekend_check"] = {"pending": pending, "items": check_items}
        saved_arrival = checks.get((oid, "arrival"))
        item["arrival_check"] = {"confirmed": bool(saved_arrival and saved_arrival.get("confirmed"))}
        baseline = watches.get(oid)
        item["flight_changed"] = bool(baseline and baseline != _flight_signature(item["fields"]))


async def _status_map(session: AsyncSession, wb: WorkbenchDef, rows: list[Any]) -> dict[int, str]:
    """按妙搭口径计算三工作台的「单票状态」。"""
    ids = [int(r["id"]) for r in rows]
    if not ids:
        return {}
    if wb.role == "domestic_ops":
        return {
            int(r["id"]): (
                "已完成" if r.get("send_forecast") == 1
                else "已交接" if r.get("handover_domestic_operation") == 1
                else "未交接"
            )
            for r in rows
        }
    if wb.role == "pricing":
        quotes = {
            int(r[0]) for r in (await session.execute(
                text("SELECT DISTINCT from_id FROM biz_link WHERE from_table='opportunity' "
                     "AND to_table='pricing' AND field='航线报价' AND from_id IN :ids")
                .bindparams(bindparam("ids", expanding=True)),
                {"ids": ids},
            )).all()
        }
        flags = {}
        for oid, priced, handed in (await session.execute(
            text("SELECT l.from_id, o.route_manager_priced, o.handover_domestic_operation "
                 "FROM biz_link l JOIN biz_order o ON o.id=l.to_id AND o.is_deleted=0 "
                 "WHERE l.from_table='opportunity' AND l.to_table='order' "
                 "AND l.field='订单表' AND l.from_id IN :ids")
            .bindparams(bindparam("ids", expanding=True)),
            {"ids": ids},
        )).all():
            flags.setdefault(int(oid), {"priced": False, "handed": False})
            flags[int(oid)]["priced"] = flags[int(oid)]["priced"] or priced == "已核价"
            flags[int(oid)]["handed"] = flags[int(oid)]["handed"] or handed == 1
        out = {}
        for r in rows:
            rid = int(r["id"])
            f = flags.get(rid, {})
            if rid not in quotes:
                out[rid] = "未报价"
            elif f.get("priced"):
                out[rid] = "已完成"
            elif f.get("handed"):
                out[rid] = "待主管核价"
            else:
                out[rid] = "已报价"
        return out
    # 客服：拿到货状态 + 报价/方案/订单阶梯
    quote_ids = {
        int(r[0]) for r in (await session.execute(
            text("SELECT DISTINCT from_id FROM biz_link WHERE from_table='opportunity' "
                 "AND to_table='pricing' AND field='航线报价' AND from_id IN :ids")
            .bindparams(bindparam("ids", expanding=True)),
            {"ids": ids},
        )).all()
    }
    plan_ids = {
        int(r[0]) for r in (await session.execute(
            text("SELECT DISTINCT from_id FROM biz_link WHERE from_table='opportunity' "
                 "AND to_table='plan' AND field='对客方案' AND from_id IN :ids")
            .bindparams(bindparam("ids", expanding=True)),
            {"ids": ids},
        )).all()
    }
    flags = {}
    # 注意列顺序：route_priced, handover, booked —— 之前按 (priced, booked, handed) 解包，
    # 把「交接」当成了「订舱」、「订舱」当成了「交接」，导致没人显示「完成操作交接」。
    for oid, priced, handed, booked in (await session.execute(
        text(
            "SELECT l.from_id, o.route_priced, o.handover_domestic_operation, "
            "EXISTS(SELECT 1 FROM biz_attachment a WHERE a.owner_table='order' "
            "  AND a.owner_id=o.id AND a.field='订舱单') AS booked "
            "FROM biz_link l JOIN biz_order o ON o.id=l.to_id AND o.is_deleted=0 "
            "WHERE l.from_table='opportunity' AND l.to_table='order' "
            "AND l.field='订单表' AND l.from_id IN :ids"
        ).bindparams(bindparam("ids", expanding=True)),
        {"ids": ids},
    )).all():
        f = flags.setdefault(int(oid), {"priced": False, "booked": False, "handed": False})
        f["priced"] = f["priced"] or priced == "已核价"
        f["booked"] = f["booked"] or bool(booked)
        f["handed"] = f["handed"] or handed == 1
    out = {}
    for r in rows:
        rid = int(r["id"])
        if r.get("is_goods_received") == "否":
            out[rid] = "未拿到货"
        else:
            f = flags.get(rid, {})
            if f.get("handed"):
                out[rid] = "完成操作交接"
            elif f.get("booked"):
                out[rid] = "完成代理订舱"
            elif f.get("priced"):
                out[rid] = "航线完成核价"
            elif rid in plan_ids:
                out[rid] = "完成客户报价"
            elif rid in quote_ids:
                out[rid] = "收到航线报价"
            elif r.get("send_inquiry") == 1:
                out[rid] = "发送航线询价"
            else:
                out[rid] = "未发送航线询价"
    return out


async def list_records(
    wb: WorkbenchDef,
    page: int = 1,
    page_size: int = 50,
    keyword: str | None = None,
    filters_raw: str | None = None,
    conjunction: str = "and",
    sort_field: str | None = None,
    sort_desc: bool | None = None,
    viewer_user_id: int | None = None,
    viewer_username: str | None = None,
    offset: int | None = None,
    tail: bool = False,
) -> dict[str, Any]:
    page_size = min(max(page_size, 1), MAX_PAGE_SIZE_ALL if wb.no_paging else MAX_PAGE_SIZE)
    async with async_db_session() as session:
        meta = await get_table_meta(session, wb.table)
        labels = await get_labels(session, wb.table)
        people = await _people_map(session)
        suppliers = await _supplier_map(session)
        allowed = set(meta)
        filters = _parse_filters(filters_raw, allowed)
        person_columns = {
            "opportunity_person_in_charge", "responsible_route", "domestic_operation",
            "sales_person", "operator", "quotation_person_in_charge",
        }
        for filter_item in filters:
            if filter_item["column"] not in person_columns:
                continue
            raw_value = str(filter_item.get("value") or "").strip()
            match = re.search(r"local-(\d+)", raw_value)
            if match:
                mapped = (
                    await session.execute(
                        text("SELECT name FROM weida_user_map WHERE id=:id"),
                        {"id": int(match.group(1))},
                    )
                ).scalar()
                if mapped:
                    filter_item["value"] = str(mapped)
                    continue
            cleaned = raw_value.strip("()[]{}").strip().strip('"')
            if cleaned.count(",") == 1:
                filter_item["value"] = cleaned
            elif cleaned in people:
                filter_item["value"] = people[cleaned]
            else:
                filter_item["value"] = cleaned
        params: dict[str, Any] = {}
        where = _where_clause(
            wb, allowed, keyword, filters, params, conjunction, meta
        )
        scope = await _viewer_scope(
            session, wb, viewer_user_id, viewer_username, params
        )
        if scope:
            where += " AND " + scope
        # 航线价格台不过滤「已发送询价」——妙搭看全量商机（send_inquiry 只决定最底档状态名）

        if wb.role == "customer_service":
            order = _default_order_sql(wb)
        elif sort_field in allowed:
            desc = wb.default_sort[1] == "desc" if sort_desc is None else sort_desc
            order = f"`{sort_field}` {'DESC' if desc else 'ASC'}, id DESC"
        else:
            order = _default_order_sql(wb)

        total = (
            await session.execute(
                text(f"SELECT COUNT(*) FROM `{wb.table}`" + where), params
            )
        ).scalar_one()
        start = max(int(total) - page_size, 0) if tail else (
            max(offset or 0, 0) if offset is not None else (page - 1) * page_size
        )
        params.update({"limit": page_size, "offset": start})
        rows = (
            await session.execute(
                text(
                    f"SELECT * FROM `{wb.table}`" + where
                    + f" ORDER BY {order} LIMIT :limit OFFSET :offset"
                ),
                params,
            )
        ).mappings().all()

        items = [_row_to_item(r, labels) for r in rows]
        for item in items:
            _apply_people(item, people)
            _apply_supplier(item, suppliers)
        if wb.role == "domestic_ops":
            goods = await _ops_goods_data(session, [int(x["id"]) for x in items])
            alerts = await _ops_alerts(session, rows)
            for item in items:
                current = item["fields"].get("goods_data", {}).get("value")
                value = goods.get(int(item["id"])) or _normalize_goods_text(current)
                item["fields"]["goods_data"] = {"label": "货物数据", "value": value}
                if int(item["id"]) in alerts:
                    item["alerts"] = alerts[int(item["id"])]
        status = await _status_map(session, wb, rows)
        for item in items:
            value = status.get(int(item["id"]))
            if value:
                item["fields"]["single_status"] = {"label": "单票状态", "value": value}
        await _apply_ops_overlays(session, wb, items)
        if wb.role == "pricing":
            virtuals = await _pricing_virtuals(session, rows)
            for item in items:
                extra = virtuals.get(int(item["id"]), {})
                if "goods_data" in extra:
                    item["fields"]["goods_data"] = {"label": "货物数据", "value": extra["goods_data"] or ""}
                if "final_plan" in extra:
                    item["fields"]["final_plan"] = {"label": "最终方案", "value": extra["final_plan"] or ""}
        if wb.role == "customer_service":
            # 妙搭：客服那一屏不摆账单状态列，只在「单票状态」格里画黄叹号 ——
            # 该商机挂的订单中任一张走到「客人未确认」就亮。
            alerts = await _customer_bill_alerts(session, [int(r["id"]) for r in rows])
            for item in items:
                label = alerts.get(int(item["id"]))
                if label:
                    item["fields"]["bill_status"] = {
                        "label": "账单状态",
                        "value": label,
                        "step": None,
                    }
        if wb.role == "domestic_ops":
            bill_status = await _ops_bill_status(session, rows)
            for item, row in zip(items, rows):
                status = bill_status.get(int(item["id"]), {})
                item["fields"]["bill_status"] = {
                    "label": "账单状态",
                    "value": status.get("label", ""),
                    "step": status.get("step"),
                }
                # 历史底稿里的单永不加粗（妙搭 ops-baseline）
                item["is_new"] = (
                    str(row.get("feishu_record_id") or "")
                    not in OPS_BASELINE_FEISHU_IDS
                )
    return {
        "items": items,
        "total": int(total),
        "page": page,
        "page_size": page_size,
        "offset": start,
        "columns": build_columns(
            wb, meta, labels, editable_columns(wb, set(meta))
        ),
    }


async def _attach_customer_flight(
    wb: WorkbenchDef, record_id: int, item: dict[str, Any]
) -> dict[str, Any]:
    if wb.role != "customer_service":
        return item
    async with async_db_session() as session:
        info = (await _customer_flight_map(session, [record_id])).get(record_id)
    if info:
        for key, label in (
            ("flight_status", "航班状态"),
            ("full_flight_info", "全程航班信息"),
            ("agent_deadline", "代理deadline"),
            ("latest_port_arrival_time", "最晚到港时间"),
        ):
            if info.get(key):
                item["fields"][key] = {"label": label, "value": info[key]}
    return item


async def _attach_status(
    wb: WorkbenchDef, row: Any, item: dict[str, Any]
) -> dict[str, Any]:
    async with async_db_session() as session:
        status = (await _status_map(session, wb, [row])).get(int(row["id"]))
        if status:
            item["fields"]["single_status"] = {"label": "单票状态", "value": status}
        await _apply_ops_overlays(session, wb, [item])
    return item


async def get_record(wb: WorkbenchDef, record_id: int) -> dict[str, Any] | None:
    async with async_db_session() as session:
        labels = await get_labels(session, wb.table)
        people = await _people_map(session)
        suppliers = await _supplier_map(session)
        row = (
            await session.execute(
                text(f"SELECT * FROM `{wb.table}` WHERE id = :id AND is_deleted = 0"),
                {"id": record_id},
            )
        ).mappings().first()
        if row and wb.role == "domestic_ops":
            goods = await _ops_goods_data(session, [record_id])
            row_item = _row_to_item(row, labels)
            _apply_people(row_item, people)
            _apply_supplier(row_item, suppliers)
            row_item["fields"]["goods_data"] = {
                "label": "货物数据",
                "value": goods.get(record_id)
                or _normalize_goods_text(row_item["fields"].get("goods_data", {}).get("value")),
            }
            other = (await _pricing_virtuals(session, [row])).get(record_id, {})
            if "final_plan" in other:
                row_item["fields"]["final_plan"] = {
                    "label": "最终方案",
                    "value": other["final_plan"] or "",
                }
            return await _attach_status(wb, row, row_item)
        if row and wb.role == "pricing":
            extra = (await _pricing_virtuals(session, [row])).get(int(row["id"]), {})
            item = _row_to_item(row, labels)
            _apply_people(item, people)
            _apply_supplier(item, suppliers)
            if "goods_data" in extra:
                item["fields"]["goods_data"] = {
                    "label": "货物数据",
                    "value": extra["goods_data"] or "",
                }
            if "final_plan" in extra:
                item["fields"]["final_plan"] = {
                    "label": "最终方案",
                    "value": extra["final_plan"] or "",
                }
            return await _attach_status(wb, row, item)
    if row:
        item = _row_to_item(row, labels)
        _apply_people(item, people)
        _apply_supplier(item, suppliers)
        item = await _attach_customer_flight(wb, record_id, item)
        return await _attach_status(wb, row, item)
    return None


def _plain_text(value: Any) -> str | None:
    """把关联/查找引用读回来的各种形状压成纯文本名（`["xx"]`、`[{"name":..}]`、JSON 串）。

    客户 / 供应商 / 国外代理在飞书上是关联字段，不同接口回来的包装层不一样；订单表这
    三列存的是客商名称，界面按客商档案的下拉选，所以这里只留名字本身。
    """
    if value in (None, ""):
        return None
    if isinstance(value, str):
        raw = value.strip()
        if raw.startswith("[") or raw.startswith("{"):
            try:
                return _plain_text(json.loads(raw))
            except (TypeError, ValueError):
                return raw or None
        return raw or None
    if isinstance(value, dict):
        for key in ("name", "text", "value"):
            if key in value:
                return _plain_text(value[key])
        return None
    if isinstance(value, (list, tuple)):
        names = [p for p in (_plain_text(item) for item in value) if p]
        return ",".join(names) if names else None
    return str(value)


def _order_cargo_text(opp: Any) -> str:
    """订单「货物数据」：`件数/毛重/体积`，换行接尺寸；毛重或体积缺了就不拼（Java 口径）。"""
    if opp.get("gross_weight_kg") in (None, "") or opp.get("volume_m3") in (None, ""):
        return ""
    cargo = "/".join(
        (
            _num_text(opp.get("piece_count")),
            _num_text(opp.get("gross_weight_kg")),
            _num_text(opp.get("volume_m3")),
        )
    )
    size = str(opp.get("size_length_width_height_quantity") or "").strip()
    return f"{cargo}\n{size}" if size else cargo


def _date_part(value: Any) -> str | None:
    """日期只到日（商机「货好时间」→ 订单日期列的上海当天 0 点）。"""
    if value in (None, ""):
        return None
    return str(value)[:10]


async def _ensure_order(session: AsyncSession, opp: Any) -> int | None:
    """确认拿到货时按商机生成一条订单（幂等），字段映射照 Java 引擎的「确认订单」契约。

    数据来源：商机本体 + 商机「最终对客方案」→ 方案关联的那条航线报价。
    首次方案/报价都不存在时仍然建单（只带商机这一半），不阻断业务 —— Java 那边会
    直接报错「商机必须关联一条最终对客方案」，这里有最终方案才抄报价那几列。
    """
    existing = (
        await session.execute(
            text(
                "SELECT to_id FROM biz_link WHERE from_table = 'opportunity' "
                "AND from_id = :oid AND to_table = 'order' AND field = '订单表' LIMIT 1"
            ),
            {"oid": opp["id"]},
        )
    ).first()
    if existing:
        return int(existing[0])
    by_no = None
    if opp.get("opportunity_no"):
        by_no = (
            await session.execute(
                text(
                    "SELECT id FROM biz_order WHERE is_deleted = 0 "
                    "AND opportunity_no = :no ORDER BY id DESC LIMIT 1"
                ),
                {"no": opp["opportunity_no"]},
            )
        ).first()
    if by_no:
        order_id = int(by_no[0])
        await session.execute(
            text(
                "INSERT INTO biz_link (field, from_table, from_id, to_table, to_id) "
                "VALUES ('订单表', 'opportunity', :oid, 'order', :order_id) "
                "ON DUPLICATE KEY UPDATE id = id"
            ),
            {"oid": opp["id"], "order_id": order_id},
        )
        await publish_event(
            table="biz_order", record_id=order_id, fields=["opportunity_no"], actor=None
        )
        return order_id
    pricing = None
    plan_no = None
    if opp.get("final_plan_id"):
        plan = (
            await session.execute(
                text("SELECT * FROM biz_plan WHERE id = :id AND is_deleted = 0"),
                {"id": opp["final_plan_id"]},
            )
        ).mappings().first()
        if plan:
            plan_no = plan.get("scheme_no")
        if plan and plan.get("pricing_id"):
            pricing = (
                await session.execute(
                    text("SELECT * FROM biz_pricing WHERE id = :id AND is_deleted = 0"),
                    {"id": plan["pricing_id"]},
                )
            ).mappings().first()
    # 字段映射照 Java 引擎（wfs）的「确认订单」契约（OrderConfirmReader#assemble）：
    #   货物数据 = 件数/毛重/体积（缺一项就不拼）+ 换行 + 尺寸
    #   货物品名（货物特殊属性）← 商机「货物品名(货物特殊属性)」
    #   货好时间 ← 商机「货好时间」（只到日）
    #   起运地/目的地/航班/供应商/等级、比例要求/成本金额/成本币种 ← 最终方案的航线报价
    #   国内操作 不自动分配（与 Java 一致，留空由业务填）
    goods = _order_cargo_text(opp)
    result = await session.execute(
        text(
            "INSERT INTO biz_order "
            "(client_ref_no, opportunity_no, plan_no, customer_name, foreign_agent_name, "
            " airline, cost_amount, cost_currency, "
            " level_ratio_req, customer_core_concern, goods_special_attr, supplier, "
            " departure_location, destination, flight, goods_data, goods_name_special_attr, "
            " goods_ready_time, create_booking) "
            "VALUES (:client_ref, :opportunity_no, :plan_no, :customer, :foreign_agent, "
            " :airline, :cost, :currency, :level, "
            " :concern, :special, :supplier, :departure, :destination, :flight, :goods, :goods_name, "
            " :goods_ready, 0)"
        ),
        {
            "client_ref": opp.get("client_ref_no"),
            "opportunity_no": opp.get("opportunity_no"),
            "plan_no": plan_no,
            # 客户 / 国外代理来自客服那条商机记录（都是客商档案里的枚举名，界面按选项选）
            "customer": _plain_text(opp.get("customer_name")),
            "foreign_agent": _plain_text(opp.get("foreign_agent_name")),
            "airline": (pricing or {}).get("airline"),
            "cost": (pricing or {}).get("cost_amount"),
            "currency": (pricing or {}).get("cost_currency"),
            "level": (pricing or {}).get("level_ratio_requirement"),
            "concern": opp.get("customer_core_concern"),
            "special": opp.get("goods_special_attr"),
            "supplier": _plain_text((pricing or {}).get("supplier")) or _plain_text(opp.get("supplier")),
            "departure": (pricing or {}).get("departure_port"),
            "destination": (pricing or {}).get("destination_port"),
            "flight": (pricing or {}).get("earliest_flight_time"),
            "goods": goods,
            "goods_name": opp.get("goods_name_special_attr"),
            "goods_ready": _date_part(opp.get("goods_ready_time")),
        },
    )
    order_id = int(result.lastrowid)
    await session.execute(
        text(
            "INSERT INTO biz_link (field, from_table, from_id, to_table, to_id) "
            "VALUES ('订单表', 'opportunity', :oid, 'order', :order_id) "
            "ON DUPLICATE KEY UPDATE id = id"
        ),
        {"oid": opp["id"], "order_id": order_id},
    )
    await publish_event(
        table="biz_order", record_id=order_id, fields=["opportunity_no"], actor=None
    )
    return order_id


# 商机（客服台）与订单（操作台）共用的字段：一边改了，另一边同秒跟着变。
# 列名不一样的两对：起运地 departure_port ↔ departure_location、目的地 destination_port ↔ destination。
SHARED_FIELDS: tuple[tuple[str, str], ...] = (
    ("client_ref_no", "client_ref_no"),
    ("customer_name", "customer_name"),
    ("foreign_agent_name", "foreign_agent_name"),
    ("goods_name_special_attr", "goods_name_special_attr"),
    ("goods_data", "goods_data"),
    ("goods_special_attr", "goods_special_attr"),
    ("customer_core_concern", "customer_core_concern"),
    ("transport_clause", "transport_clause"),
    ("goods_ready_time", "goods_ready_time"),
    ("request_arrival_time", "request_arrival_time"),
    ("departure_port", "departure_location"),
    ("destination_port", "destination"),
)


async def _propagate_shared(
    session: AsyncSession, table: str, record_id: int, payload: dict[str, Any]
) -> list[tuple[str, int, list[str]]]:
    """把改动同步到对端表（商机↔订单），返回 [(目标表, 记录id, 变更列)] 供发事件。"""
    if table == "biz_opportunity":
        target = "biz_order"
        column_map = {a: b for a, b in SHARED_FIELDS}
        link_sql = (
            "SELECT to_id FROM biz_link WHERE from_table='opportunity' "
            "AND to_table='order' AND field='订单表' AND from_id=:id"
        )
    elif table == "biz_order":
        target = "biz_opportunity"
        column_map = {b: a for a, b in SHARED_FIELDS}
        link_sql = (
            "SELECT from_id FROM biz_link WHERE from_table='opportunity' "
            "AND to_table='order' AND field='订单表' AND to_id=:id"
        )
    else:
        return []
    ids = [
        int(r[0]) for r in (await session.execute(text(link_sql), {"id": record_id})).all()
    ]
    if not ids:
        return []
    sets: list[str] = []
    params: dict[str, Any] = {}
    for col, value in payload.items():
        target_col = column_map.get(col)
        if not target_col:
            continue
        sets.append(f"`{target_col}` = :{target_col}")
        params[target_col] = value
    if not sets:
        return []
    await session.execute(
        text(
            f"UPDATE `{target}` SET {', '.join(sets)}, update_time = NOW(3) "
            "WHERE id IN :ids AND is_deleted = 0"
        ).bindparams(bindparam("ids", expanding=True)),
        {**params, "ids": ids},
    )
    return [(target, rid, list(params)) for rid in ids]

async def update_record(
    wb: WorkbenchDef,
    record_id: int,
    fields: dict[str, Any],
    expected_update_time: str | None = None,
    actor: int | None = None,
) -> dict[str, Any] | None:
    async with async_db_session() as session:
        meta = await get_table_meta(session, wb.table)
        allowed = editable_columns(wb, set(meta))
        payload = {k: v for k, v in fields.items() if k in allowed}
        if not payload:
            raise HTTPException(status_code=422, detail="没有可更新的字段")
        for col in payload:
            if col not in meta:
                raise HTTPException(status_code=422, detail=f"字段不存在：{col}")
        _normalize_json_fields(meta, payload)
        _normalize_date_fields(meta, payload)
        if (
            wb.table == "biz_opportunity"
            and ("gross_weight_kg" in payload or "volume_m3" in payload)
            and "chargeable_weight" not in payload
        ):
            current = (
                await session.execute(
                    text(
                        "SELECT gross_weight_kg, volume_m3 FROM biz_opportunity "
                        "WHERE id = :id AND is_deleted = 0"
                    ),
                    {"id": record_id},
                )
            ).mappings().first()
            if current:
                gross = payload.get("gross_weight_kg", current["gross_weight_kg"])
                volume = payload.get("volume_m3", current["volume_m3"])
                calc = _calc_chargeable(gross, volume)
                if calc is not None:
                    payload["chargeable_weight"] = calc
        sets = ", ".join(f"`{col}` = :{col}" for col in payload)
        params: dict[str, Any] = dict(payload)
        params["id"] = record_id
        where = "id = :id AND is_deleted = 0"
        if expected_update_time:
            # 前端拿到的是 ISO（2026-11-20T10:00:00.123），MySQL DATETIME 要空格
            where += " AND update_time = :expected"
            params["expected"] = expected_update_time.replace("T", " ")[:23]
        result = await session.execute(
            text(
                f"UPDATE `{wb.table}` SET {sets}, update_time = NOW(3) WHERE {where}"
            ),
            params,
        )
        if result.rowcount == 0:
            await session.commit()
            if expected_update_time:
                raise HTTPException(status_code=409, detail="记录已被他人修改，请刷新后重试")
            return None
        if wb.table == "biz_opportunity":
            current = (
                await session.execute(
                    text("SELECT * FROM biz_opportunity WHERE id = :id"),
                    {"id": record_id},
                )
            ).mappings().first()
            if current and current.get("is_goods_received") == "是":
                await _ensure_order(session, current)
        # 商机 ↔ 订单 共用字段：一边改了，对端同一次提交里跟着改（秒级生效）
        shared_hits = await _propagate_shared(session, wb.table, record_id, payload)
        await session.commit()
        await run_automations(wb.table, record_id, payload, actor)
    await publish_event(
        table=wb.table,
        record_id=record_id,
        fields=sorted(payload),
        actor=actor,
    )
    for target_table, target_id, changed in shared_hits:
        await publish_event(
            table=target_table, record_id=target_id, fields=changed, actor=actor
        )
    return await get_record(wb, record_id)


def _calc_chargeable(gross: Any, volume: Any) -> float | None:
    try:
        g = float(gross)
        v = float(volume)
    except (TypeError, ValueError):
        return None
    return round(max(g, v * 166.7), 4)


OPTION_COLUMNS = {
    ("biz_opportunity", "transport_clause"),
    ("biz_opportunity", "destination_port"),
    ("biz_opportunity", "goods_special_attr"),
    # iFreight 展开体上的选项字段（妙搭里都是单选/多选，不该是自由输入）
    ("biz_ifreight", "document_type"),
    ("biz_ifreight", "cargo_receiving_method"),
    ("biz_ifreight", "business_type"),
    ("biz_ifreight", "pp_currency"),
    ("biz_ifreight", "business_department"),
    ("biz_ifreight", "route"),
    ("biz_ifreight", "airline_company"),
    ("biz_ifreight", "freight_payment_method"),
    ("biz_ifreight", "surcharge_payment_method"),
    ("biz_ifreight", "piece_unit"),
}


REF_OPTION_KINDS = {
    ("biz_ifreight", "document_type"): "单据类型",
    ("biz_ifreight", "cargo_receiving_method"): "揽货方式",
    ("biz_ifreight", "business_type"): "业务类型",
    ("biz_ifreight", "business_department"): "业务所属部门",
    ("biz_ifreight", "route"): "航线",
    ("biz_ifreight", "airline_company"): "航空公司",
    ("biz_ifreight", "piece_unit"): "件数单位",
}


async def list_column_options(
    table: str, column: str, keyword: str | None = None, limit: int = 100
) -> list[str]:
    if (table, column) not in OPTION_COLUMNS:
        raise HTTPException(status_code=422, detail="不支持的选项字段")
    # 基础资料里有同步好的选项时优先用它（可维护）；没同步过再退回飞书/本地值
    kind = REF_OPTION_KINDS.get((table, column))
    if kind:
        try:
            from .option_sync import ref_options

            rows = await ref_options(kind)
            if rows:
                values = [row["value"] for row in rows if row.get("value")]
                if keyword:
                    values = [v for v in values if keyword.strip() in v]
                return values[:limit]
        except Exception:
            pass
    if table == "biz_opportunity" and column == "destination_port":
        where = "active = 1"
        params: dict[str, Any] = {"limit": limit}
        if keyword:
            where += " AND (city_name LIKE :kw OR city_code LIKE :kw)"
            params["kw"] = f"%{keyword.strip()}%"
        async with async_db_session() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT DISTINCT city_name FROM weida_destination_port "
                        f"WHERE {where} ORDER BY city_name LIMIT :limit"
                    ),
                    params,
                )
            ).scalars().all()
        return [str(v) for v in rows]
    async with async_db_session() as session:
        rows = (
            await session.execute(
                text(
                    f"SELECT DISTINCT `{column}` AS v FROM `{table}` "
                    f"WHERE `{column}` IS NOT NULL AND `{column}` <> '' "
                    "AND is_deleted = 0 ORDER BY v LIMIT :limit"
                ),
                {"limit": limit},
            )
        ).scalars().all()
        configured = (
            await session.execute(
                text(
                    "SELECT value FROM weida_option WHERE category = :c AND active = 1 "
                    "ORDER BY sort, id"
                ),
                {"c": column, "limit": limit},
            )
        ).scalars().all()
    merged = [str(v) for v in configured]
    for v in rows:
        if str(v) not in merged:
            merged.append(str(v))
    return merged


async def list_port_options(keyword: str | None = None, limit: int = 6000) -> list[dict[str, Any]]:
    where = "active = 1 AND city_code IS NOT NULL AND city_code <> ''"
    params: dict[str, Any] = {"limit": limit}
    if keyword:
        kw = keyword.strip()
        if len(kw) <= 3:
            where += " AND (city_code = :kw OR city_code LIKE :kw2)"
            params["kw"] = kw.upper()
            params["kw2"] = f"{kw.upper()}%"
        else:
            where += " AND (city_code LIKE :kw OR city_name LIKE :kw)"
            params["kw"] = f"%{kw.upper()}%"
    async with async_db_session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT city_code AS code, city_name AS name FROM weida_destination_port "
                    f"WHERE {where} GROUP BY city_code, city_name ORDER BY city_code LIMIT :limit"
                ),
                params,
            )
        ).mappings().all()
    return [dict(r) for r in rows]


async def list_vendor_options() -> list[dict[str, Any]]:
    async with async_db_session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id, "
                    "COALESCE(NULLIF(merchant_short_name, ''), merchant_full_name) AS name, "
                    "merchant_full_name AS full_name "
                    "FROM biz_vendor WHERE is_deleted = 0 "
                    "AND merchant_full_name IS NOT NULL AND merchant_full_name <> '' "
                    "ORDER BY name"
                )
            )
        ).mappings().all()
    return [dict(r) for r in rows]


async def _next_opportunity_no(session: AsyncSession) -> str:
    value = (
        await session.execute(
            text(
                "SELECT MAX(CAST(SUBSTRING(opportunity_no, 4) AS UNSIGNED)) "
                "FROM biz_opportunity WHERE opportunity_no REGEXP '^OP-[0-9]+$'"
            )
        )
    ).scalar_one()
    return f"OP-{int(value or 0) + 1:07d}"


async def create_record(
    wb: WorkbenchDef,
    fields: dict[str, Any],
    actor: int | None = None,
) -> dict[str, Any]:
    """按列表列 + 可编辑白名单新建一条记录。"""
    async with async_db_session() as session:
        meta = await get_table_meta(session, wb.table)
        allowed = set(wb.columns) | editable_columns(wb, set(meta))
        payload = {k: v for k, v in fields.items() if k in allowed}
        if not payload:
            raise HTTPException(status_code=422, detail="没有可写入的字段")
        for col in payload:
            if col not in meta:
                raise HTTPException(status_code=422, detail=f"字段不存在：{col}")
        _normalize_json_fields(meta, payload)
        if wb.table == "biz_opportunity":
            if not payload.get("opportunity_no"):
                payload["opportunity_no"] = await _next_opportunity_no(session)
            calc = _calc_chargeable(
                payload.get("gross_weight_kg"), payload.get("volume_m3")
            )
            if calc is not None:
                payload["chargeable_weight"] = calc
        cols = ", ".join(f"`{c}`" for c in payload)
        vals = ", ".join(f":{c}" for c in payload)
        params = dict(payload)
        if actor is not None and "create_by" in meta:
            cols += ", `create_by`"
            vals += ", :create_by"
            params["create_by"] = actor
        result = await session.execute(
            text(f"INSERT INTO `{wb.table}` ({cols}) VALUES ({vals})"), params
        )
        record_id = int(result.lastrowid or 0)
        # 与 Java 引擎「确认订单」同一规则：商机新建时就是「是否拿到货=是」的，
        # 当场建订单（订单表即操作工作台的数据来源），不然这票永远不会进操作那边。
        if wb.table == "biz_opportunity" and payload.get("is_goods_received") == "是":
            current = (
                await session.execute(
                    text("SELECT * FROM biz_opportunity WHERE id = :id"),
                    {"id": record_id},
                )
            ).mappings().first()
            if current:
                await _ensure_order(session, current)
        await session.commit()
    await publish_event(
        table=wb.table,
        record_id=record_id,
        fields=sorted(payload),
        actor=actor,
    )
    if wb.table == "biz_opportunity":
        names = _person_names(payload.get("responsible_route"))
        if names:
            await _notify_names(
                names,
                "新商机待报价",
                f"商机 {payload.get('opportunity_no') or record_id} 已新增，请及时报价",
                f"new-opportunity-{record_id}",
            )
    if wb.table == "biz_pricing":
        try:
            async with async_db_session() as session:
                row = (
                    await session.execute(
                        text(
                            "SELECT o.opportunity_person_in_charge FROM biz_link l "
                            "JOIN biz_opportunity o ON o.id=l.from_id "
                            "WHERE l.from_table='opportunity' AND l.to_table='pricing' "
                            "AND l.to_id=:pid AND l.field='航线报价' LIMIT 1"
                        ),
                        {"pid": record_id},
                    )
                ).mappings().first()
                if row:
                    people = await _people_map(session)
                    names = _person_names(_replace_people(row["opportunity_person_in_charge"], people))
                    if names:
                        await _notify_names(
                            names,
                            "航线新增报价",
                            f"商机 {payload.get('supplier_quote_no') or record_id} 已有新报价",
                            f"new-pricing-{record_id}",
                        )
        except Exception:
            pass
    if wb.table == "biz_pricing":
        try:
            async with async_db_session() as session:
                opp_id = (
                    await session.execute(
                        text(
                            "SELECT from_id FROM biz_link WHERE from_table='opportunity' "
                            "AND to_table='pricing' AND to_id=:pid AND field='航线报价' LIMIT 1"
                        ),
                        {"pid": record_id},
                    )
                ).scalar_one_or_none()
            if opp_id:
                await generate_plan_for_opportunity(int(opp_id), record_id, actor=actor)
        except Exception:
            pass
    record = await get_record(wb, record_id)
    if record is None:
        raise HTTPException(status_code=500, detail="创建后读取失败")
    return record


async def delete_record(
    wb: WorkbenchDef,
    record_id: int,
    actor: int | None = None,
) -> bool:
    """软删除一条记录。"""
    async with async_db_session() as session:
        result = await session.execute(
            text(
                f"UPDATE `{wb.table}` SET is_deleted = 1, update_time = NOW(3) "
                "WHERE id = :id AND is_deleted = 0"
            ),
            {"id": record_id},
        )
        await session.commit()
        if result.rowcount == 0:
            return False
    await publish_event(
        table=wb.table,
        record_id=record_id,
        fields=["is_deleted"],
        actor=actor,
    )
    return True


def _weight_text(value: float) -> str:
    if abs(value) >= 10000:
        return f"{value / 1000:.1f} 吨"
    return f"{round(value):,} kg"


async def get_dashboard(
    role: str,
    wb: WorkbenchDef,
    period: str = "week",
    date_from: str | None = None,
    date_to: str | None = None,
    basis: str = "send_inquiry_time",
    group: str = "customer",
    trend_from: str | None = None,
    trend_to: str | None = None,
    owner: str | None = None,
) -> dict[str, Any]:
    """部门仪表盘：按妙搭截图给客户/负责人/趋势、按时率、操作人周月历史。"""
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    week_start = monday - timedelta(days=7)
    week_end = monday - timedelta(days=1)
    if period == "month":
        week_start = today.replace(day=1)
        week_end = today
    elif period == "range" and date_from and date_to:
        try:
            week_start = date.fromisoformat(date_from)
            week_end = date.fromisoformat(date_to)
        except ValueError:
            pass
    week_from = datetime.combine(week_start, datetime.min.time())
    week_to = datetime.combine(week_end, datetime.max.time())
    month_start = today.replace(day=1)
    month_from = datetime.combine(month_start, datetime.min.time())

    async with async_db_session() as session:
        if role in ("customer_service", "pricing"):
            rows = (
                await session.execute(text("SELECT * FROM biz_opportunity WHERE is_deleted=0"))
            ).mappings().all()
            people = await _people_map(session)

            def in_week(row: Any) -> bool:
                value = row.get("send_inquiry_time") or row.get("create_time")
                return isinstance(value, datetime) and week_from <= value <= week_to
            period = [r for r in rows if in_week(r)] or rows
            total = len(period)
            won_rows = [r for r in period if str(r.get("is_goods_received") or "").strip() == "是"]
            won = len(won_rows)
            lost = sum(1 for r in period if str(r.get("is_goods_received") or "").strip() == "否")
            undetermined = total - won - lost

            def weight(row: Any) -> float:
                for key in ("chargeable_weight", "gross_weight_kg"):
                    try:
                        v = float(row.get(key) or 0)
                    except (TypeError, ValueError):
                        v = 0
                    if v:
                        return v
                return 0.0
            won_weight = sum(weight(r) for r in won_rows)
            total_weight = sum(weight(r) for r in period)
            customers: dict[str, dict[str, Any]] = {}
            owners: dict[str, dict[str, Any]] = {}
            for row in period:
                is_won = str(row.get("is_goods_received") or "").strip() == "是"
                cname = str(row.get("customer_name") or "未填").strip() or "未填"
                oname_list = _person_names(_replace_people(row.get("opportunity_person_in_charge"), people))
                oname = oname_list[0] if oname_list else "未分配"
                for mapping, key in ((customers, cname), (owners, oname)):
                    item = mapping.setdefault(key, {"name": key, "created": 0, "won": 0, "wonWeight": 0.0, "weight": 0.0})
                    item["created"] += 1
                    item["weight"] += weight(row)
                    if is_won:
                        item["won"] += 1
                        item["wonWeight"] += weight(row)

            def rows_out(mapping: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
                out = []
                for item in sorted(mapping.values(), key=lambda x: -x["created"]):
                    created = int(item["created"])
                    item["caption"] = f"{item['won']}/{created} · {(item['won'] / created * 100 if created else 0):.1f}%"
                    item["subCaption"] = f"{_weight_text(item['wonWeight'])} / {_weight_text(item['weight'])}"
                    out.append(item)
                return out
            customers_out = rows_out(customers)
            owners_out = rows_out(owners)
            summary = {
                "rate": f"{(won / total * 100):.1f}%" if total else "—",
                "won": won,
                "lost": lost,
                "undetermined": undetermined,
                "created": total,
                "wonWeight": _weight_text(won_weight),
                "createdWeight": _weight_text(total_weight),
                "weightRate": f"{(won_weight / total_weight * 100):.1f}%" if total_weight else "—",
            }
            trend = []
            try:
                if trend_from:
                    trend_start = date.fromisoformat(trend_from)
                else:
                    trend_start = date.fromisocalendar(today.year, 41, 1)
                if trend_to:
                    trend_end = date.fromisoformat(trend_to)
                else:
                    trend_end = monday + timedelta(days=6)
                trend_start -= timedelta(days=trend_start.weekday())
                trend_end += timedelta(days=6 - trend_end.weekday())
            except ValueError:
                trend_start = monday - timedelta(days=7 * 8)
                trend_end = monday + timedelta(days=6)
            cursor = trend_start
            while cursor <= trend_end:
                start = cursor
                end = start + timedelta(days=6)
                cursor = end + timedelta(days=1)
                wk_rows = [
                    r for r in rows
                    if isinstance(r.get("send_inquiry_time") or r.get("create_time"), datetime)
                    and start <= (r.get("send_inquiry_time") or r.get("create_time")).date() <= end
                    and (
                        not owner
                        or str(r.get("opportunity_person_in_charge") or "").strip().strip('"') == owner
                    )
                ]
                cw = len(wk_rows)
                ww = sum(1 for r in wk_rows if str(r.get("is_goods_received") or "").strip() == "是")
                trend.append({
                    "name": f"W{start.isocalendar().week}",
                    "value": round(ww / cw * 100, 1) if cw else None,
                    "detail": f"{start:%Y-%m-%d} ~ {end:%m-%d} · {ww}/{cw} 票",
                })
            cards = [
                {"label": "拿到率（全量）", "value": summary["rate"]},
                {"label": "拿到货物票数", "value": won},
                {"label": "新建商机票数", "value": total},
                {"label": "拿到货物计费重", "value": f"{summary['wonWeight']} / {summary['createdWeight']} · {summary['weightRate']}"},
            ]
            sections = [
                {"key": "customers", "title": "基于每一个客户", "caption": "这一周该客户发出询价的商机，拿到了多少票", "type": "stacked", "data": customers_out},
                {"key": "owners", "title": "基于每一个商机负责人", "caption": "这一周每个负责人发出询价的商机，拿到货的比例", "type": "stacked", "data": owners_out},
                {"key": "trend", "title": "周拿货率趋势", "caption": "按发送询价时间归周，拿到货票数 ÷ 发出询价票数", "type": "line", "data": trend},
            ]
            if role == "pricing":
                opp_info = {
                    int(r["id"]): r
                    for r in (
                        await session.execute(
                            text("SELECT id,customer_name,latest_quote_deadline FROM biz_opportunity WHERE is_deleted=0")
                        )
                    ).mappings().all()
                }
                quote_links = (
                    await session.execute(
                        text(
                            "SELECT l.to_id AS pricing_id, l.from_id AS opportunity_id "
                            "FROM biz_link l WHERE l.from_table='opportunity' AND l.to_table='pricing' "
                            "AND l.field='航线报价'"
                        )
                    )
                ).mappings().all()
                opp_of = {int(r["pricing_id"]): int(r["opportunity_id"]) for r in quote_links}
                quotes = (
                    await session.execute(text("SELECT * FROM biz_pricing WHERE is_deleted=0"))
                ).mappings().all()
                by_customer: dict[str, dict[str, Any]] = {}
                on_time = late = undetermined = 0
                buckets = {"≤ 2 小时": 0, "2-24 小时": 0, "> 24 小时": 0}
                for quote in quotes:
                    raw_version = quote.get("quote_version")
                    created = quote.get("create_time")
                    if isinstance(raw_version, str) and raw_version.strip():
                        try:
                            created = datetime.fromisoformat(raw_version.strip().replace("Z", "+00:00"))
                        except ValueError:
                            pass
                    if not isinstance(created, datetime):
                        continue
                    opp = opp_info.get(opp_of.get(int(quote["id"]), 0), {})
                    customer = str(opp.get("customer_name") or "未填").strip() or "未填"
                    if group == "supplier":
                        customer = str(quote.get("supplier") or "未填").strip() or "未填"
                    item = by_customer.setdefault(customer, {"name": customer, "created": 0, "won": 0, "value2": 0})
                    item["created"] += 1
                    deadline = opp.get("latest_quote_deadline")
                    if not isinstance(deadline, datetime):
                        undetermined += 1
                        item["value2"] = item.get("value2", 0)
                        continue
                    if created <= deadline:
                        on_time += 1
                        item["won"] += 1
                        item["value2"] += 1
                    else:
                        late += 1
                        hours = (created - deadline).total_seconds() / 3600
                        if hours <= 2:
                            buckets["≤ 2 小时"] += 1
                        elif hours <= 24:
                            buckets["2-24 小时"] += 1
                        else:
                            buckets["> 24 小时"] += 1
                total_judged = on_time + late
                reason_counts: dict[str, int] = {}
                for quote in quotes:
                    opp = opp_info.get(opp_of.get(int(quote["id"]), 0), {})
                    if str(opp.get("is_goods_received") or "").strip() != "否":
                        continue
                    reason = str(opp.get("goods_not_received_remark") or "").strip() or "原因未填"
                    reason_counts[reason] = reason_counts.get(reason, 0) + 1
                summary2 = {
                    "rate": f"{(on_time / total_judged * 100):.1f}%" if total_judged else "—",
                    "onTime": on_time,
                    "late": late,
                    "undetermined": undetermined,
                }
                sections = [
                    {
                        "key": "pricing-customers",
                        "title": "基于每一个供应商" if group == "supplier" else "基于每一个客户",
                        "caption": "柱高是可判定报价数，深色是截止前完成",
                        "type": "stacked",
                        "data": [
                            {
                                "name": item["name"],
                                "value": item["created"],
                                "value2": item["value2"],
                                "caption": f"{item['value2']}/{item['created']} · {(item['value2'] / item['created'] * 100 if item['created'] else 0):.1f}%",
                            }
                            for item in sorted(by_customer.values(), key=lambda x: -x["created"])
                        ],
                    },
                    {
                        "key": "delay",
                        "title": "超时了多久",
                        "caption": "只看超过截止的报价，按延迟时长分三档",
                        "type": "bar",
                        "data": [{"name": k, "value": v, "caption": f"{v} 条"} for k, v in buckets.items()],
                    },
                ]
                cards = [
                    {"label": "拿到率（全量）", "value": summary["rate"]},
                    {"label": "本期商机", "value": summary["created"]},
                    {"label": "拿到", "value": summary["won"]},
                    {"label": "未拿到", "value": summary["lost"]},
                    {"label": "还没判定", "value": summary["undetermined"]},
                ]
                return {
                    "title": "航线价格仪表盘",
                    "subtitle": f"{week_start:%Y-%m-%d} ~ {week_end:%Y-%m-%d}",
                    "cards": cards,
                    "summary": summary,
                    "summary2": summary2,
                    "reasons": [
                        {"name": k, "value": v}
                        for k, v in sorted(reason_counts.items(), key=lambda x: -x[1])[:20]
                    ],
                    "sections": sections,
                }
            return {
                "title": "客服部门仪表盘",
                "subtitle": f"{week_start:%Y-%m-%d} ~ {week_end:%Y-%m-%d}",
                "cards": cards,
                "summary": summary,
                "sections": sections,
            }

        rows = (
            await session.execute(text("SELECT * FROM biz_order WHERE is_deleted=0"))
        ).mappings().all()
        pending_rows = [
            r for r in rows
            if r.get("send_forecast") not in (1, True)
        ]
        operators: dict[str, dict[str, Any]] = {}
        for row in pending_rows:
            name = str(row.get("domestic_operation") or "未分配").strip() or "未分配"
            item = operators.setdefault(name, {"name": name, "created": 0, "won": 0})
            item["created"] += 1
            if row.get("handover_domestic_operation") in (1, True):
                item["won"] += 1
        week_rows = [r for r in rows if isinstance(r.get("flight"), datetime) and week_from <= r["flight"] <= week_to]
        month_rows = [r for r in rows if isinstance(r.get("flight"), datetime) and r["flight"] >= month_from]

        def by_operator(items: list[Any]) -> list[dict[str, Any]]:
            mapping: dict[str, int] = {}
            for row in items:
                name = str(row.get("domestic_operation") or "未分配").strip() or "未分配"
                mapping[name] = mapping.get(name, 0) + 1
            return [{"name": k, "value": v, "caption": f"{v} 票"} for k, v in sorted(mapping.items(), key=lambda x: -x[1])]
        pending_chart = []
        for name, item in operators.items():
            pending_chart.append({
                "name": name,
                "value": item["created"],
                "value2": item["won"],
                "caption": f"{item['created']} 票",
            })
        week_dist = []
        for i in range(7):
            day = monday - timedelta(days=7) + timedelta(days=i)
            count = sum(
                1 for r in rows
                if isinstance(r.get("warehouse_in_operation_time"), datetime)
                and r["warehouse_in_operation_time"].date() == day
            )
            week_dist.append({"name": f"周{'一二三四五六日'[i]}", "value": count})
        history_week = []
        history_month = []
        for i in range(7, -1, -1):
            start = monday - timedelta(days=7 * (i + 1))
            end = start + timedelta(days=6)
            count = sum(
                1 for r in rows
                if isinstance(r.get("warehouse_in_operation_time"), datetime)
                and start <= r["warehouse_in_operation_time"].date() <= end
            )
            history_week.append({"name": start.strftime("%m-%d"), "value": count})
        for i in range(5, -1, -1):
            month = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
            for _ in range(i):
                month = (month - timedelta(days=1)).replace(day=1)
            count = sum(
                1 for r in rows
                if isinstance(r.get("warehouse_in_operation_time"), datetime)
                and r["warehouse_in_operation_time"].year == month.year
                and r["warehouse_in_operation_time"].month == month.month
            )
            history_month.append({"name": month.strftime("%Y-%m"), "value": count})
        operator_table = []
        for name in sorted({str(r.get("domestic_operation") or "未分配").strip() or "未分配" for r in rows}):
            current = [r for r in rows if (str(r.get("domestic_operation") or "未分配").strip() or "未分配") == name]
            week_count = sum(1 for r in current if isinstance(r.get("warehouse_in_operation_time"), datetime) and week_from <= r["warehouse_in_operation_time"] <= week_to)
            month_count = sum(1 for r in current if isinstance(r.get("warehouse_in_operation_time"), datetime) and r["warehouse_in_operation_time"] >= month_from)
            done = sum(1 for r in current if r.get("send_forecast") in (1, True))
            operator_table.append({
                "name": name,
                "week": week_count,
                "month": month_count,
                "pending": len(current) - done,
                "done": done,
            })
        cards = [
            {"label": "待操作", "value": len(pending_rows)},
            {"label": "本周操作", "value": len(week_rows)},
            {"label": "本月操作", "value": len(month_rows)},
            {"label": "已完成", "value": sum(1 for r in rows if r.get("send_forecast") in (1, True))},
        ]
        sections = [
            {"key": "pending", "title": "待操作货物", "caption": "深色已交接，浅色未交接；已完成不进图", "type": "stacked", "data": pending_chart},
            {"key": "week", "title": "周操作货物", "caption": f"{week_start:%Y-%m-%d} ~ {week_end:%m-%d}，按航班", "type": "bar", "data": by_operator(week_rows)},
            {"key": "month", "title": "月操作货物", "caption": f"{month_start:%Y-%m}，按航班", "type": "bar", "data": by_operator(month_rows)},
            {"key": "weekDist", "title": "本周按入仓时间分布", "caption": "按入仓操作时间", "type": "line", "data": week_dist},
            {"key": "historyWeek", "title": "历史周票数", "caption": "按入仓操作时间", "type": "line", "data": history_week},
            {"key": "historyMonth", "title": "历史月票数", "caption": "按入仓操作时间", "type": "line", "data": history_month},
        ]
        today_warehouse = [
            {
                "id": int(r["id"]),
                "title": str(r.get("order_no") or r.get("client_ref_no") or f"#{r['id']}"),
                "customer": str(r.get("customer_name") or ""),
                "operator": str(r.get("domestic_operation") or ""),
            }
            for r in rows
            if isinstance(r.get("warehouse_in_operation_time"), datetime)
            and r["warehouse_in_operation_time"].date() == today
        ]
        today_flights = [
            {
                "id": int(r["id"]),
                "title": str(r.get("order_no") or r.get("client_ref_no") or f"#{r['id']}"),
                "flight": r["flight"].strftime("%Y-%m-%d %H:%M") if isinstance(r.get("flight"), datetime) else "",
                "operator": str(r.get("domestic_operation") or ""),
            }
            for r in rows
            if isinstance(r.get("flight"), datetime) and r["flight"].date() == today
        ]
        return {
            "title": "待操作货物仪表盘",
            "subtitle": f"{week_start:%Y-%m-%d} ~ {week_end:%Y-%m-%d}",
            "cards": cards,
            "sections": sections,
            "operatorTable": operator_table,
            "todayWarehouse": today_warehouse,
            "todayFlights": today_flights,
        }


async def dashboard_detail(
    role: str,
    dimension: str,
    name: str,
    period: str = "week",
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    from datetime import date as _date
    from datetime import datetime as _dt
    from datetime import timedelta as _td

    today = _date.today()
    monday = today - _td(days=today.weekday())
    start = monday - _td(days=7)
    end = monday - _td(days=1)
    if period == "month":
        start, end = today.replace(day=1), today
    elif period == "range" and date_from and date_to:
        start, end = _date.fromisoformat(date_from), _date.fromisoformat(date_to)
    start_dt = _dt.combine(start, _dt.min.time())
    end_dt = _dt.combine(end, _dt.max.time())
    async with async_db_session() as session:
        if role in ("customer_service", "pricing"):
            if role == "pricing" and dimension == "supplier":
                quotes = (
                    await session.execute(
                        text("SELECT * FROM biz_pricing WHERE is_deleted=0 ORDER BY id DESC")
                    )
                ).mappings().all()
                items = []
                for quote in quotes:
                    created = quote.get("create_time")
                    if not isinstance(created, datetime) or not (start_dt <= created <= end_dt):
                        continue
                    if str(quote.get("supplier") or "未填").strip() != name:
                        continue
                    items.append({
                        "id": int(quote["id"]),
                        "title": str(quote.get("supplier_quote_no") or f"#{quote['id']}"),
                        "subtitle": str(quote.get("departure_port") or "") + "/" + str(quote.get("destination_port") or ""),
                        "status": str(quote.get("quotation_status") or ""),
                    })
                return {"items": items[:200]}
            rows = (
                await session.execute(
                    text("SELECT * FROM biz_opportunity WHERE is_deleted=0 ORDER BY id DESC")
                )
            ).mappings().all()
            people = await _people_map(session)
            items = []
            for row in rows:
                created = row.get("send_inquiry_time") or row.get("create_time")
                if not isinstance(created, datetime) or not (start_dt <= created <= end_dt):
                    continue
                if dimension == "customer":
                    if str(row.get("customer_name") or "未填").strip() != name:
                        continue
                elif dimension == "owner":
                    owners = _person_names(_replace_people(row.get("opportunity_person_in_charge"), people))
                    if (owners[0] if owners else "未分配") != name:
                        continue
                items.append(
                    {
                        "id": int(row["id"]),
                        "title": str(row.get("opportunity_no") or f"#{row['id']}"),
                        "subtitle": f"{row.get('customer_name') or ''} · {row.get('goods_name') or ''}",
                        "status": str(row.get("is_goods_received") or "未判定"),
                    }
                )
            return {"items": items[:200]}

        rows = (
            await session.execute(
                text("SELECT * FROM biz_order WHERE is_deleted=0 ORDER BY id DESC")
            )
        ).mappings().all()
        items = []
        for row in rows:
            if dimension == "operator":
                if (str(row.get("domestic_operation") or "未分配").strip() or "未分配") != name:
                    continue
            flight = row.get("flight")
            if isinstance(flight, datetime) and not (start_dt <= flight <= end_dt):
                continue
            items.append(
                {
                    "id": int(row["id"]),
                    "title": str(row.get("order_no") or row.get("client_ref_no") or f"#{row['id']}"),
                    "subtitle": f"{row.get('customer_name') or ''} · {row.get('goods_name_special_attr') or ''}",
                    "status": "已完成" if row.get("send_forecast") in (1, True) else (
                        "已交接" if row.get("handover_domestic_operation") in (1, True) else "未交接"
                    ),
                }
            )
        return {"items": items[:200]}


async def get_user_map() -> dict[str, Any]:
    """人员映射 + 可绑定的本地账号。"""
    async with async_db_session() as session:
        items = (
            await session.execute(
                text(
                    "SELECT m.id, m.name, m.role, m.suda_user_id, m.local_user_id, "
                    "u.username AS username, u.name AS local_name "
                    "FROM weida_user_map m "
                    "LEFT JOIN sys_user u ON u.id = m.local_user_id AND u.is_deleted = 0 "
                    "WHERE m.active = 1 ORDER BY m.role, m.id"
                )
            )
        ).mappings().all()
        users = (
            await session.execute(
                text(
                    "SELECT id, username, name FROM sys_user "
                    "WHERE is_deleted = 0 AND status = '0' ORDER BY id"
                )
            )
        ).mappings().all()
    return {
        "items": [dict(r) for r in items],
        "users": [dict(r) for r in users],
    }


async def bind_user(map_id: int, local_user_id: int | None) -> None:
    """把妙搭人员绑定到本地账号；一个本地账号只能绑一个人员。"""
    async with async_db_session() as session:
        row = (
            await session.execute(
                text("SELECT id FROM weida_user_map WHERE id = :id AND active = 1"),
                {"id": map_id},
            )
        ).first()
        if not row:
            raise HTTPException(status_code=404, detail="人员映射不存在")
        if local_user_id is not None:
            user = (
                await session.execute(
                    text(
                        "SELECT id FROM sys_user "
                        "WHERE id = :id AND is_deleted = 0 AND status = '0'"
                    ),
                    {"id": local_user_id},
                )
            ).first()
            if not user:
                raise HTTPException(status_code=422, detail="本地账号不存在或已停用")
            used = (
                await session.execute(
                    text(
                        "SELECT id FROM weida_user_map "
                        "WHERE local_user_id = :uid AND id <> :id"
                    ),
                    {"uid": local_user_id, "id": map_id},
                )
            ).first()
            if used:
                raise HTTPException(status_code=409, detail="该账号已绑定其他人员")
        await session.execute(
            text("UPDATE weida_user_map SET local_user_id = :uid WHERE id = :id"),
            {"uid": local_user_id, "id": map_id},
        )
        await session.commit()


async def list_staff_admin() -> dict[str, Any]:
    async with async_db_session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT m.id, m.name, m.role, m.roles, m.suda_user_id, m.local_user_id, m.active, "
                    "u.username, u.name AS local_name, u.mobile, u.email, u.status "
                    "FROM weida_user_map m "
                    "LEFT JOIN sys_user u ON u.id = m.local_user_id AND u.is_deleted = 0 "
                    "ORDER BY m.role, m.id"
                )
            )
        ).mappings().all()
    return {"items": [dict(r) for r in rows]}


async def get_staff_options() -> list[dict[str, Any]]:
    async with async_db_session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id, name, role, roles, suda_user_id, local_user_id "
                    "FROM weida_user_map WHERE active = 1 ORDER BY role, id"
                )
            )
        ).mappings().all()
    return [dict(r) for r in rows]


async def create_staff(
    name: str, role: str, roles: list[str] | None = None
) -> dict[str, Any]:
    name = name.strip()
    role = role.strip()
    roles = [r for r in (roles or [role]) if r]
    if not name or not role:
        raise HTTPException(status_code=422, detail="姓名和身份必填")
    async with async_db_session() as session:
        result = await session.execute(
            text(
                "INSERT INTO weida_user_map (name, role, roles, active) "
                "VALUES (:name, :role, :roles, 1)"
            ),
            {"name": name, "role": role, "roles": json.dumps(roles)},
        )
        await session.commit()
        map_id = int(result.lastrowid)
    return {"id": map_id, "name": name, "role": role}


async def update_staff(map_id: int, values: dict[str, Any]) -> None:
    allowed = {"name", "role", "roles", "active", "mobile", "email"}
    clean = {k: v for k, v in values.items() if k in allowed}
    if not clean:
        raise HTTPException(status_code=422, detail="没有可更新的人员字段")
    async with async_db_session() as session:
        row = (
            await session.execute(
                text("SELECT * FROM weida_user_map WHERE id = :id"), {"id": map_id}
            )
        ).mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="人员不存在")
        if "roles" in clean:
            roles = [r for r in (clean.get("roles") or []) if r]
            if roles:
                clean["role"] = roles[0]
                clean["roles"] = json.dumps(roles)
        map_sets = []
        map_params: dict[str, Any] = {"id": map_id}
        for key in ("name", "role", "roles", "active"):
            if key in clean:
                map_sets.append(f"`{key}` = :{key}")
                map_params[key] = clean[key]
        if map_sets:
            await session.execute(
                text(
                    f"UPDATE weida_user_map SET {', '.join(map_sets)} "
                    "WHERE id = :id"
                ),
                map_params,
            )
        if row["local_user_id"]:
            user_sets = []
            user_params: dict[str, Any] = {"uid": row["local_user_id"]}
            for key in ("name", "mobile", "email"):
                if key in clean:
                    user_sets.append(f"`{key}` = :{key}")
                    user_params[key] = clean[key]
            if user_sets:
                await session.execute(
                    text(
                        f"UPDATE sys_user SET {', '.join(user_sets)}, updated_time = NOW() "
                        "WHERE id = :uid AND is_deleted = 0"
                    ),
                    user_params,
                )
        await session.commit()


async def create_staff_account(map_id: int, username: str, password: str) -> None:
    username = username.strip()
    if len(username) < 3:
        raise HTTPException(status_code=422, detail="账号至少 3 个字符")
    if len(password) < 6:
        raise HTTPException(status_code=422, detail="密码至少 6 位")
    async with async_db_session() as session:
        row = (
            await session.execute(
                text("SELECT * FROM weida_user_map WHERE id = :id AND active = 1"),
                {"id": map_id},
            )
        ).mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="人员不存在")
        if row["local_user_id"]:
            raise HTTPException(status_code=409, detail="该人员已开通账号")
        exists = (
            await session.execute(
                text("SELECT id FROM sys_user WHERE username = :u"), {"u": username}
            )
        ).first()
        if exists:
            raise HTTPException(status_code=409, detail="账号已存在")
        result = await session.execute(
            text(
                "INSERT INTO sys_user "
                "(username, password, name, dept_id, status, is_superuser, tenant_id, uuid, "
                " created_time, updated_time, is_deleted) "
                "VALUES (:username, :password, :name, 1, '0', 0, 1, :uuid, NOW(), NOW(), 0)"
            ),
            {
                "username": username,
                "password": PwdUtil.set_password_hash(password),
                "name": row["name"],
                "uuid": uuid.uuid4().hex,
            },
        )
        user_id = int(result.lastrowid)
        role_code = {
            "customer_service": "CUSTOMER_SERVICE",
            "pricing": "PRICING",
            "domestic_ops": "DOMESTIC_OPS",
            "admin": "ADMIN",
            "super_admin": "ADMIN",
        }.get(str(row.get("role") or ""), "WEIDA")
        role = (
            await session.execute(
                text("SELECT id FROM sys_role WHERE code = :code AND is_deleted = 0"),
                {"code": role_code},
            )
        ).first()
        if not role:
            raise HTTPException(status_code=500, detail="缺少威达员工角色")
        await session.execute(
            text("INSERT INTO sys_user_roles (user_id, role_id) VALUES (:uid, :rid)"),
            {"uid": user_id, "rid": int(role[0])},
        )
        await session.execute(
            text("UPDATE weida_user_map SET local_user_id = :uid WHERE id = :id"),
            {"uid": user_id, "id": map_id},
        )
        await session.commit()


async def change_my_password(user_id: int, old_password: str, new_password: str) -> None:
    if len(new_password) < 6:
        raise HTTPException(status_code=422, detail="新密码至少 6 位")
    async with async_db_session() as session:
        row = (
            await session.execute(
                text("SELECT password FROM sys_user WHERE id=:id AND is_deleted=0"),
                {"id": user_id},
            )
        ).mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="账号不存在")
        if not PwdUtil.verify_password(old_password, row["password"]):
            raise HTTPException(status_code=422, detail="原密码不正确")
        await session.execute(
            text("UPDATE sys_user SET password=:pwd, updated_time=NOW() WHERE id=:id"),
            {"pwd": PwdUtil.set_password_hash(new_password), "id": user_id},
        )
        await session.commit()


async def reset_staff_password(map_id: int, password: str) -> None:
    if len(password) < 6:
        raise HTTPException(status_code=422, detail="密码至少 6 位")
    async with async_db_session() as session:
        row = (
            await session.execute(
                text("SELECT local_user_id FROM weida_user_map WHERE id = :id"),
                {"id": map_id},
            )
        ).mappings().first()
        if not row or not row["local_user_id"]:
            raise HTTPException(status_code=404, detail="该人员还没有账号")
        await session.execute(
            text(
                "UPDATE sys_user SET password = :password, updated_time = NOW() "
                "WHERE id = :uid AND is_deleted = 0"
            ),
            {
                "password": PwdUtil.set_password_hash(password),
                "uid": row["local_user_id"],
            },
        )
        await session.commit()


VENDOR_FIELDS = (
    "merchant_no",
    "merchant_full_name",
    "merchant_short_name",
    "merchant_status",
    "merchant_role",
    "default_currency",
    "merchant_person_in_charge",
    "merchant_address",
    "special_requirements_risk_warning",
    "account_period_cooperation_terms",
    "tax_point",
    "invoice_requirement",
)


async def list_vendors_admin(
    keyword: str | None = None, page: int = 1, page_size: int = 50
) -> dict[str, Any]:
    where = "is_deleted = 0"
    params: dict[str, Any] = {}
    if keyword:
        where += (
            " AND (merchant_no LIKE :kw OR merchant_full_name LIKE :kw "
            "OR merchant_short_name LIKE :kw)"
        )
        params["kw"] = f"%{keyword}%"
    async with async_db_session() as session:
        meta = await get_table_meta(session, "biz_vendor")
        labels = await get_labels(session, "biz_vendor")
        people = await _people_map(session)
        total = (
            await session.execute(text(f"SELECT COUNT(*) FROM biz_vendor WHERE {where}"), params)
        ).scalar_one()
        rows = (
            await session.execute(
                text(
                    f"SELECT * FROM biz_vendor WHERE {where} ORDER BY id DESC "
                    "LIMIT :limit OFFSET :offset"
                ),
                {**params, "limit": page_size, "offset": (page - 1) * page_size},
            )
        ).mappings().all()
    hidden = {"id", "feishu_record_id", "create_time", "update_time", "create_by", "update_by", "is_deleted"}
    items = [dict(r) for r in rows]
    for item in items:
        if "merchant_person_in_charge" in item:
            item["merchant_person_in_charge"] = _replace_people(
                item["merchant_person_in_charge"], people
            )
    return {
        "items": items,
        "total": int(total),
        "columns": [
            {"column": col, "label": labels.get(col, col), "type": meta[col]["type"]}
            for col in meta
            if col not in hidden
        ],
    }


async def save_vendor(vendor_id: int | None, values: dict[str, Any]) -> dict[str, Any]:
    clean = {k: v for k, v in values.items() if k in VENDOR_FIELDS}
    if not clean:
        raise HTTPException(status_code=422, detail="没有可保存的客商字段")
    async with async_db_session() as session:
        if vendor_id:
            sets = ", ".join(f"`{k}` = :{k}" for k in clean)
            result = await session.execute(
                text(
                    f"UPDATE biz_vendor SET {sets}, update_time = NOW(3) "
                    "WHERE id = :id AND is_deleted = 0"
                ),
                {**clean, "id": vendor_id},
            )
            await session.commit()
            if result.rowcount == 0:
                raise HTTPException(status_code=404, detail="客商不存在")
            return await _record_by_table("biz_vendor", vendor_id)
        cols = ", ".join(f"`{k}`" for k in clean)
        placeholders = ", ".join(f":{k}" for k in clean)
        result = await session.execute(
            text(f"INSERT INTO biz_vendor ({cols}) VALUES ({placeholders})"), clean
        )
        await session.commit()
        return await _record_by_table("biz_vendor", int(result.lastrowid))


async def list_options_admin(category: str | None = None) -> dict[str, Any]:
    where = "1 = 1"
    params: dict[str, Any] = {}
    if category:
        where += " AND category = :c"
        params["c"] = category
    async with async_db_session() as session:
        rows = (
            await session.execute(
                text(
                    f"SELECT * FROM weida_option WHERE {where} "
                    "ORDER BY category, sort, id"
                ),
                params,
            )
        ).mappings().all()
        categories = (
            await session.execute(
                text("SELECT DISTINCT category FROM weida_option ORDER BY category")
            )
        ).scalars().all()
    return {
        "items": [dict(r) for r in rows],
        "categories": [str(c) for c in categories],
    }


async def save_option(option_id: int | None, values: dict[str, Any]) -> dict[str, Any]:
    allowed = {"category", "value", "sort", "active"}
    clean = {k: v for k, v in values.items() if k in allowed}
    if not clean:
        raise HTTPException(status_code=422, detail="没有可保存的选项字段")
    if not option_id:
        for key in ("category", "value"):
            if not clean.get(key):
                raise HTTPException(status_code=422, detail="分类和值必填")
    async with async_db_session() as session:
        if option_id:
            sets = ", ".join(f"`{k}` = :{k}" for k in clean)
            await session.execute(
                text(
                    f"UPDATE weida_option SET {sets}, update_time = NOW(3) "
                    "WHERE id = :id"
                ),
                {**clean, "id": option_id},
            )
            await session.commit()
        else:
            cols = ", ".join(f"`{k}`" for k in clean)
            placeholders = ", ".join(f":{k}" for k in clean)
            await session.execute(
                text(f"INSERT INTO weida_option ({cols}) VALUES ({placeholders}) "
                     "ON DUPLICATE KEY UPDATE sort = VALUES(sort), active = 1"),
                clean,
            )
            await session.commit()
    return {"ok": True}


async def delete_option(option_id: int) -> None:
    async with async_db_session() as session:
        await session.execute(
            text("DELETE FROM weida_option WHERE id = :id"), {"id": option_id}
        )
        await session.commit()


PORT_FIELDS = ("city_code", "city_name", "country_code", "dep", "export_port", "operation_requirement", "active")


async def list_destination_ports(
    keyword: str | None = None, page: int = 1, page_size: int = 50
) -> dict[str, Any]:
    where = "1 = 1"
    params: dict[str, Any] = {}
    if keyword:
        where += " AND (city_name LIKE :kw OR city_code LIKE :kw OR country_code LIKE :kw)"
        params["kw"] = f"%{keyword}%"
    async with async_db_session() as session:
        total = (
            await session.execute(
                text(f"SELECT COUNT(*) FROM weida_destination_port WHERE {where}"), params
            )
        ).scalar_one()
        rows = (
            await session.execute(
                text(
                    f"SELECT * FROM weida_destination_port WHERE {where} "
                    "ORDER BY city_name LIMIT :limit OFFSET :offset"
                ),
                {**params, "limit": page_size, "offset": (page - 1) * page_size},
            )
        ).mappings().all()
    return {"items": [dict(r) for r in rows], "total": int(total)}


async def save_destination_port(
    port_id: int, values: dict[str, Any]
) -> dict[str, Any]:
    clean = {k: v for k, v in values.items() if k in PORT_FIELDS}
    if not clean:
        raise HTTPException(status_code=422, detail="没有可保存的目的港字段")
    sets = ", ".join(f"`{k}` = :{k}" for k in clean)
    async with async_db_session() as session:
        result = await session.execute(
            text(
                f"UPDATE weida_destination_port SET {sets}, update_time = NOW(3) "
                "WHERE id = :id"
            ),
            {**clean, "id": port_id},
        )
        await session.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="目的港不存在")
        row = (
            await session.execute(
                text("SELECT * FROM weida_destination_port WHERE id = :id"), {"id": port_id}
            )
        ).mappings().first()
    return dict(row) if row else {}


TODO_TIME_FIELDS = {
    "create_booking": "booking_note_create_time",
    "send_forecast": "forecast_sent_time",
}


async def update_todo(
    wb: WorkbenchDef,
    record_id: int,
    field: str,
    value: bool,
    actor: int | None = None,
) -> dict[str, Any] | None:
    """勾选/取消操作待办；勾选时顺手写对应时间字段。"""
    if field not in wb.todo_checkboxes:
        raise HTTPException(status_code=422, detail=f"不是待办字段：{field}")
    fields: dict[str, Any] = {field: 1 if value else 0}
    time_col = TODO_TIME_FIELDS.get(field)
    if value and time_col:
        fields[time_col] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return await update_record(wb, record_id, fields, actor=actor)


async def _record_by_table(table: str, record_id: int) -> dict[str, Any] | None:
    """按物理表取一条记录，返回详情结构（不依赖 WorkbenchDef）。"""
    async with async_db_session() as session:
        labels = await get_labels(session, table)
        row = (
            await session.execute(
                text(f"SELECT * FROM `{table}` WHERE id = :id AND is_deleted = 0"),
                {"id": record_id},
            )
        ).mappings().first()
    return _row_to_item(row, labels) if row else None


FEE_FIELDS = (
    "detail_no",
    "fee_category",
    "group",
    "billing_unit",
    "currency",
    "amount",
    "remark",
)


PLAN_EDITABLE = {
    "customer_quote",
    "quote_currency",
    "quote_valid_until",
    "app_scheme_name",
    "departure_port",
    "destination_port",
    "earliest_flight_time",
    "scheme_no",
}


async def update_plan_record(plan_id: int, values: dict[str, Any]) -> dict[str, Any]:
    async with async_db_session() as session:
        meta = await get_table_meta(session, "biz_plan")
        clean = {k: v for k, v in values.items() if k in PLAN_EDITABLE and k in meta}
        if not clean:
            raise HTTPException(status_code=422, detail="没有可更新的方案字段")
        for key, value in list(clean.items()):
            if isinstance(value, (list, dict)):
                clean[key] = json.dumps(value, ensure_ascii=False)
        _normalize_date_fields(meta, clean)
        _normalize_numeric_fields(meta, clean)
        sets = ", ".join(f"`{k}`=:{k}" for k in clean)
        params = dict(clean)
        params["id"] = plan_id
        result = await session.execute(
            text(f"UPDATE biz_plan SET {sets}, update_time=NOW(3) WHERE id=:id AND is_deleted=0"),
            params,
        )
        await session.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="对客方案不存在")
    await publish_event(table="biz_plan", record_id=plan_id, fields=sorted(clean), actor=None)
    return {"id": plan_id, "fields": clean}


async def get_planfee_options() -> dict[str, list[str]]:
    """费用明细四个下拉的候选值：现有明细 + AI 费用字典。"""
    fields = {
        "fee_categories": ("biz_planfee", "fee_category", "biz_aifee", "fee_category"),
        "groups": ("biz_planfee", "group", None, None),
        "billing_units": ("biz_planfee", "billing_unit", "biz_aifee", "billing_unit"),
        "currencies": ("biz_planfee", "currency", "biz_aifee", "currency"),
    }
    out: dict[str, list[str]] = {}
    async with async_db_session() as session:
        for key, (t1, c1, t2, c2) in fields.items():
            sql = (
                f"SELECT DISTINCT `{c1}` AS v FROM `{t1}` "
                f"WHERE `{c1}` IS NOT NULL AND `{c1}` <> '' AND is_deleted = 0"
            )
            if t2 and c2:
                sql += (
                    f" UNION SELECT DISTINCT `{c2}` AS v FROM `{t2}` "
                    f"WHERE `{c2}` IS NOT NULL AND `{c2}` <> '' AND is_deleted = 0"
                )
            values = (await session.execute(text(sql))).scalars().all()
            out[key] = sorted({str(v) for v in values}, key=lambda x: x.lower())
    return out


async def list_plan_fees(plan_id: int) -> dict[str, Any]:
    async with async_db_session() as session:
        labels = await get_labels(session, "biz_planfee")
        rows = (
            await session.execute(
                text(
                    "SELECT * FROM biz_planfee WHERE plan_id = :pid AND is_deleted = 0 "
                    "ORDER BY id"
                ),
                {"pid": plan_id},
            )
        ).mappings().all()
    return {
        "items": [
            {
                "id": int(r["id"]),
                "opportunity_id": int(r["opportunity_id"]) if r["opportunity_id"] else None,
                "goods": {
                    "piece_count": r["opp_piece_count"],
                    "gross_weight_kg": r["opp_gross_weight_kg"],
                    "volume_m3": r["opp_volume_m3"],
                    "size": r["opp_size"],
                },
                "fields": _row_to_item(r, labels)["fields"],
            }
            for r in rows
        ]
    }


async def add_plan_fee(plan_id: int, values: dict[str, Any], actor: int | None = None) -> dict[str, Any]:
    clean = {k: values.get(k) for k in FEE_FIELDS if k in values}
    async with async_db_session() as session:
        plan = (
            await session.execute(
                text("SELECT id FROM biz_plan WHERE id = :id AND is_deleted = 0"),
                {"id": plan_id},
            )
        ).first()
        if not plan:
            raise HTTPException(status_code=404, detail="对客方案不存在")
        cols = ["plan_id", *clean.keys()]
        params = [plan_id, *clean.values()]
        placeholders = ", ".join(f":p{i}" for i in range(len(params)))
        result = await session.execute(
            text(
                f"INSERT INTO biz_planfee ({', '.join('`' + c + '`' for c in cols)}) "
                f"VALUES ({placeholders})"
            ),
            {f"p{i}": v for i, v in enumerate(params)},
        )
        fee_id = int(result.lastrowid)
        await session.commit()
    return await _record_by_table("biz_planfee", fee_id)


async def update_plan_fee(
    plan_id: int, fee_id: int, values: dict[str, Any]
) -> dict[str, Any]:
    clean = {k: values[k] for k in FEE_FIELDS if k in values}
    if not clean:
        raise HTTPException(status_code=422, detail="没有可更新的费用字段")
    sets = ", ".join(f"`{k}` = :{k}" for k in clean)
    params = dict(clean)
    params["id"] = fee_id
    params["pid"] = plan_id
    async with async_db_session() as session:
        result = await session.execute(
            text(
                f"UPDATE biz_planfee SET {sets} "
                "WHERE id = :id AND plan_id = :pid AND is_deleted = 0"
            ),
            params,
        )
        await session.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="费用明细不存在")
    return await _record_by_table("biz_planfee", fee_id)


async def delete_plan_fee(plan_id: int, fee_id: int) -> None:
    async with async_db_session() as session:
        await session.execute(
            text(
                "UPDATE biz_planfee SET is_deleted = 1 "
                "WHERE id = :id AND plan_id = :pid AND is_deleted = 0"
            ),
            {"id": fee_id, "pid": plan_id},
        )
        await session.commit()


UPLOAD_DIR = Path(os.getenv("WEIDA_UPLOAD_DIR", "/home/ubuntu/weida/uploads"))
QUOTE_FIELD = "报价单"


def _attachment_item(row: Any) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "field": row["field"],
        "file_name": row["file_name"],
        "file_size": int(row["file_size"] or 0),
        "mime": row["mime"] or "",
        "create_time": (
            row["create_time"].isoformat(sep=" ", timespec="seconds")
            if row.get("create_time")
            else None
        ),
    }


async def list_attachments(
    owner_table: str, owner_id: int, field: str | None = None
) -> dict[str, Any]:
    sql = (
        "SELECT id, field, file_name, file_size, mime, create_time, storage_key "
        "FROM biz_attachment WHERE owner_table = :t AND owner_id = :oid"
    )
    params: dict[str, Any] = {"t": owner_table, "oid": owner_id}
    if field:
        sql += " AND field = :field"
        params["field"] = field
    sql += " ORDER BY id DESC"
    async with async_db_session() as session:
        rows = (await session.execute(text(sql), params)).mappings().all()
    return {"items": [_attachment_item(r) for r in rows]}


async def save_attachment(
    owner_table: str,
    owner_id: int,
    field: str,
    file_name: str,
    content: bytes,
    mime: str | None = None,
    replace: bool = False,
) -> dict[str, Any]:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file_name).name or "file"
    storage_key = f"{uuid.uuid4().hex}{Path(safe_name).suffix.lower()}"
    (UPLOAD_DIR / storage_key).write_bytes(content)
    mime = mime or mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
    async with async_db_session() as session:
        if replace:
            old = (
                await session.execute(
                    text(
                        "SELECT id, storage_key FROM biz_attachment "
                        "WHERE owner_table = :t AND owner_id = :oid AND field = :f"
                    ),
                    {"t": owner_table, "oid": owner_id, "f": field},
                )
            ).mappings().all()
            for item in old:
                try:
                    (UPLOAD_DIR / item["storage_key"]).unlink(missing_ok=True)
                except OSError:
                    pass
            await session.execute(
                text(
                    "DELETE FROM biz_attachment "
                    "WHERE owner_table = :t AND owner_id = :oid AND field = :f"
                ),
                {"t": owner_table, "oid": owner_id, "f": field},
            )
        result = await session.execute(
            text(
                "INSERT INTO biz_attachment "
                "(owner_table, owner_id, field, file_name, file_size, mime, storage_key) "
                "VALUES (:t, :oid, :f, :name, :size, :mime, :key)"
            ),
            {
                "t": owner_table,
                "oid": owner_id,
                "f": field,
                "name": safe_name,
                "size": len(content),
                "mime": mime,
                "key": storage_key,
            },
        )
        attachment_id = int(result.lastrowid)
        await session.commit()
        row = (
            await session.execute(
                text(
                    "SELECT id, field, file_name, file_size, mime, create_time, storage_key "
                    "FROM biz_attachment WHERE id = :id"
                ),
                {"id": attachment_id},
            )
        ).mappings().first()
    return _attachment_item(row)


async def get_attachment(attachment_id: int) -> tuple[dict[str, Any], Path]:
    async with async_db_session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT id, field, file_name, file_size, mime, storage_key "
                    "FROM biz_attachment WHERE id = :id"
                ),
                {"id": attachment_id},
            )
        ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="附件不存在")
    path = UPLOAD_DIR / row["storage_key"]
    if not path.exists():
        raise HTTPException(status_code=404, detail="附件文件不存在")
    return _attachment_item(row), path


async def delete_attachment(attachment_id: int) -> None:
    async with async_db_session() as session:
        row = (
            await session.execute(
                text("SELECT storage_key FROM biz_attachment WHERE id = :id"),
                {"id": attachment_id},
            )
        ).first()
        if not row:
            raise HTTPException(status_code=404, detail="附件不存在")
        await session.execute(
            text("DELETE FROM biz_attachment WHERE id = :id"), {"id": attachment_id}
        )
        await session.commit()
    try:
        (UPLOAD_DIR / row[0]).unlink(missing_ok=True)
    except OSError:
        pass


def _quotation_data(
    plan: Any, opp: Any, pricing: Any | None, fees: list[Any]
) -> dict[str, Any]:
    chargeable = opp.get("chargeable_weight")
    if chargeable is None or float(chargeable) <= 0:
        raise HTTPException(status_code=422, detail="商机缺少「计费重」，无法生成报价单")
    groups = {"AIR CHARGE": [], "LOCAL Description": [], "DEST Description": []}
    for fee in fees:
        group = (fee.get("group") or "").strip()
        if group not in groups:
            raise HTTPException(
                status_code=422, detail=f"费用明细分组无法对应报价单三段：{group}"
            )
        amount = fee.get("amount")
        if amount is None or not fee.get("fee_category") or not fee.get("currency") or not fee.get("billing_unit"):
            raise HTTPException(status_code=422, detail="费用明细缺少类别、币种、计费单位或金额")
        groups[group].append(
            {
                "name": fee["fee_category"],
                "currency": str(fee["currency"]).upper(),
                "amount": float(amount),
                "uom": fee["billing_unit"],
                "remark": fee.get("remark"),
            }
        )
    pieces = "" if opp.get("piece_count") is None else str(opp["piece_count"])
    gross = opp.get("gross_weight_kg") or 0
    volume = opp.get("volume_m3") or 0
    cargo = f"{pieces}/{gross}/{volume}"
    if opp.get("size_length_width_height_quantity"):
        cargo += "\n" + str(opp["size_length_width_height_quantity"])
    return {
        "customer": opp.get("customer_name") or "",
        "pickup": opp.get("source_location") or "",
        "delivery": opp.get("delivery_address") or "",
        "incoterm": opp.get("transport_clause") or "",
        "pol": (pricing or {}).get("departure_port") or "",
        "pod": (pricing or {}).get("destination_port") or "",
        "cargo_details": cargo,
        "gross_weight": float(gross),
        "volume": float(volume),
        "chargeable_weight": float(chargeable),
        "air": groups["AIR CHARGE"],
        "local": groups["LOCAL Description"],
        "dest": groups["DEST Description"],
        "remark": opp.get("remark") or "",
        "scheme_no": plan.get("scheme_no") or "",
        "opportunity_no": opp.get("opportunity_no") or "",
    }


BOOKING_FIELD = "订舱单"


def _booking_order_no(order: Any) -> str:
    return str(order.get("client_ref_no") or order.get("opportunity_no") or f"PO-{order['id']}")


def _fmt_route(order: Any) -> str:
    dep = str(order.get("departure_location") or "").strip()
    dest = str(order.get("destination") or "").strip()
    if dep and dest:
        return f"{dep}/{dest}"
    return ""


async def generate_booking(order_id: int, actor: int | None = None) -> dict[str, Any]:
    """按订单生成订舱单，覆盖订单附件并写生成时间。"""
    async with async_db_session() as session:
        order = (
            await session.execute(
                text("SELECT * FROM biz_order WHERE id = :id AND is_deleted = 0"),
                {"id": order_id},
            )
        ).mappings().first()
        if not order:
            raise HTTPException(status_code=404, detail="订单不存在")
        opp = (
            await session.execute(
                text(
                    "SELECT o.* FROM biz_link l JOIN biz_opportunity o ON o.id = l.from_id "
                    "WHERE l.to_table = 'order' AND l.to_id = :oid "
                    "AND l.from_table = 'opportunity' AND l.field = '订单表' "
                    "AND o.is_deleted = 0 LIMIT 1"
                ),
                {"oid": order_id},
            )
        ).mappings().first()
        pricing = None
        if order.get("plan_no"):
            pricing = (
                await session.execute(
                    text(
                        "SELECT p.* FROM biz_plan pl JOIN biz_pricing p ON p.id = pl.pricing_id "
                        "WHERE pl.scheme_no = :no AND pl.is_deleted = 0 AND p.is_deleted = 0 "
                        "LIMIT 1"
                    ),
                    {"no": order["plan_no"]},
                )
            ).mappings().first()

    order_no = _booking_order_no(order)
    route = _fmt_route(order)
    if not route and pricing:
        dep = str(pricing.get("departure_port") or "").strip()
        dest = str(pricing.get("destination_port") or "").strip()
        if dep and dest:
            route = f"{dep}/{dest}"
    destination = str(order.get("destination") or "").strip()
    if not destination and "/" in route:
        destination = route.split("/", 1)[1].strip()

    cargo = str(order.get("goods_data") or "").strip()
    if not cargo and opp:
        bits = [
            str(opp.get("piece_count") or "").strip(),
            str(opp.get("gross_weight_kg") or "").strip(),
            str(opp.get("volume_m3") or "").strip(),
        ]
        cargo = "/".join([b for b in bits if b])
        if opp.get("size_length_width_height_quantity"):
            cargo = f"{cargo}\n{opp['size_length_width_height_quantity']}" if cargo else str(
                opp["size_length_width_height_quantity"]
            )
    if "\n" not in cargo and " " in cargo:
        head, tail = cargo.split(" ", 1)
        if "/" in head:
            cargo = f"{head}\n{tail}"
    cargo_name = str(order.get("goods_name_special_attr") or "").strip()
    if not cargo_name and opp:
        cargo_name = str(opp.get("goods_name") or "").strip()
    if not cargo:
        raise HTTPException(status_code=422, detail="订单缺少货物数据，无法生成订舱单")
    if not cargo_name:
        raise HTTPException(status_code=422, detail="订单缺少货物品名，无法生成订舱单")
    if not route:
        raise HTTPException(status_code=422, detail="订单缺少起运地/目的地，无法生成订舱单")

    data = {
        "agent": order.get("supplier") or "",
        "cargo": cargo,
        "cargo_name": cargo_name,
        "warehouse_time": order.get("warehouse_in_operation_time"),
        "route": route,
        "airline": order.get("airline") or "",
        "cost_amount": order.get("cost_amount"),
        "level_ratio": order.get("level_ratio_req") or "",
        "flight": order.get("flight") or (pricing or {}).get("earliest_flight_time"),
        "instructions": order.get("operation_requirement") or "",
        "operator": order.get("domestic_operation") or "",
        "notes": order.get("special_note") or "",
    }
    content = render_booking(data)
    safe_no = order_no.replace("/", "-").replace("\\", "-")
    file_name = f"{safe_no}-{destination}.xlsx" if destination else f"{safe_no}.xlsx"
    attachment = await save_attachment(
        "order",
        order_id,
        BOOKING_FIELD,
        file_name,
        content,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        replace=True,
    )
    async with async_db_session() as session:
        await session.execute(
            text(
                "UPDATE biz_order SET create_booking = 1, booking_note_create_time = NOW(3), "
                "update_time = NOW(3) WHERE id = :id"
            ),
            {"id": order_id},
        )
        await session.commit()
    await publish_event(
        table="biz_order", record_id=order_id, fields=["create_booking"], actor=actor
    )
    return {
        "created": True,
        "order": await _record_by_table("biz_order", order_id),
        "attachment": attachment,
        "file_name": file_name,
    }


async def generate_quote(plan_id: int, actor: int | None = None) -> dict[str, Any]:
    async with async_db_session() as session:
        plan = (
            await session.execute(
                text("SELECT * FROM biz_plan WHERE id = :id AND is_deleted = 0"),
                {"id": plan_id},
            )
        ).mappings().first()
        if not plan:
            raise HTTPException(status_code=404, detail="对客方案不存在")
        opp = (
            await session.execute(
                text(
                    "SELECT o.* FROM biz_link l JOIN biz_opportunity o ON o.id = l.from_id "
                    "WHERE l.to_table = 'plan' AND l.to_id = :pid AND l.from_table = 'opportunity' "
                    "AND l.field = '对客方案' AND o.is_deleted = 0 LIMIT 1"
                ),
                {"pid": plan_id},
            )
        ).mappings().first()
        if not opp:
            raise HTTPException(status_code=422, detail="对客方案未关联商机")
        pricing = None
        if plan.get("pricing_id"):
            pricing = (
                await session.execute(
                    text("SELECT * FROM biz_pricing WHERE id = :id AND is_deleted = 0"),
                    {"id": plan["pricing_id"]},
                )
            ).mappings().first()
        fees = (
            await session.execute(
                text(
                    "SELECT * FROM biz_planfee WHERE plan_id = :pid AND is_deleted = 0 "
                    "ORDER BY COALESCE(detail_no, ''), id"
                ),
                {"pid": plan_id},
            )
        ).mappings().all()
    if not fees:
        raise HTTPException(
            status_code=422,
            detail="费用明细还是空的，先点「添加费用明细」录进去，才能生成报价单",
        )
    data = _quotation_data(plan, opp, pricing, list(fees))
    content = render_quotation(data)
    file_name = f"{data['opportunity_no'] or 'OP'}-{data['scheme_no'] or plan_id}.xlsx"
    attachment = await save_attachment(
        "plan",
        plan_id,
        QUOTE_FIELD,
        file_name,
        content,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        replace=True,
    )
    async with async_db_session() as session:
        await session.execute(
            text(
                "UPDATE biz_plan SET generate_quote = 1, quote_generate_time = NOW(3), "
                "update_time = NOW(3) WHERE id = :id"
            ),
            {"id": plan_id},
        )
        await session.commit()
    await publish_event(
        table="biz_plan", record_id=plan_id, fields=["generate_quote"], actor=actor
    )
    return {
        "created": True,
        "plan": await _record_by_table("biz_plan", plan_id),
        "attachment": attachment,
    }


async def generate_plan(
    pricing_id: int,
    actor: int | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """航线报价 → 对客方案，单事务生成初稿。

    幂等：同一报价已有方案时直接返回已有方案，不重复建单。
    """
    created = False
    plan_id: int | None = None
    async with async_db_session() as session:
        async with session.begin():
            pricing = (
                await session.execute(
                    text(
                        "SELECT * FROM biz_pricing "
                        "WHERE id = :id AND is_deleted = 0 FOR UPDATE"
                    ),
                    {"id": pricing_id},
                )
            ).mappings().first()
            if not pricing:
                raise HTTPException(status_code=404, detail="航线报价不存在")

            existing = None
            if not force:
                existing = (
                    await session.execute(
                        text(
                            "SELECT to_id FROM biz_link "
                            "WHERE from_table = 'pricing' AND from_id = :pid "
                            "AND to_table = 'plan' AND field = '被方案采用' "
                            "ORDER BY id DESC LIMIT 1"
                        ),
                        {"pid": pricing_id},
                    )
                ).first()
            if existing:
                plan_id = int(existing[0])
                await session.execute(
                    text(
                        "UPDATE biz_plan SET pricing_id = :pid, "
                        "customer_quote = COALESCE(customer_quote, :quote) "
                        "WHERE id = :plan_id AND (pricing_id IS NULL OR customer_quote IS NULL)"
                    ),
                    {"pid": pricing_id, "plan_id": plan_id, "quote": pricing.get("cost_amount")},
                )
            else:
                dest = pricing.get("destination_port") or "未填"
                name = f"对客方案-{dest}-{pricing_id}"
                note = (
                    pricing.get("route_plan_suggestion")
                    or pricing.get("flight_schedule")
                    or ""
                )
                result = await session.execute(
                    text(
                        "INSERT INTO biz_plan "
                        "(pricing_id, app_scheme_name, route_efficiency_note, "
                        " quote_currency, quote_valid_until, scheme_status, "
                        " departure_port, destination_port, earliest_flight_time, "
                        " quote_generate_time, generate_quote, customer_quote) "
                        "VALUES (:pid, :name, :note, :currency, :valid, '草稿', "
                        " :departure, :destination, :flight, NOW(3), 0, :client_quote)"
                    ),
                    {
                        "pid": pricing_id,
                        "name": name,
                        "note": note,
                        "currency": pricing.get("cost_currency"),
                        "valid": pricing.get("quotation_validity_period"),
                        "departure": pricing.get("departure_port"),
                        "destination": pricing.get("destination_port"),
                        "flight": pricing.get("earliest_flight_time"),
                        "client_quote": pricing.get("cost_amount"),
                    },
                )
                plan_id = int(result.lastrowid)
                await session.execute(
                    text("UPDATE biz_plan SET scheme_no = :no WHERE id = :id"),
                    {"no": f"NO.{plan_id}", "id": plan_id},
                )
                await session.execute(
                    text(
                        "INSERT INTO biz_link "
                        "(field, from_table, from_id, to_table, to_id) "
                        "VALUES ('被方案采用', 'pricing', :pid, 'plan', :plan_id) "
                        "ON DUPLICATE KEY UPDATE id = id"
                    ),
                    {"pid": pricing_id, "plan_id": plan_id},
                )
                await session.execute(
                    text(
                        "UPDATE biz_pricing SET customer_plan_draft = 1, "
                        "update_time = NOW(3) WHERE id = :pid"
                    ),
                    {"pid": pricing_id},
                )
                created = True

    if plan_id is None:
        raise HTTPException(status_code=500, detail="生成对客方案失败")
    await publish_event(
        table="biz_plan", record_id=plan_id, fields=["app_scheme_name"], actor=actor
    )
    if created:
        await publish_event(
            table="biz_pricing",
            record_id=pricing_id,
            fields=["customer_plan_draft"],
            actor=actor,
        )
    return {
        "created": created,
        "pricing_id": pricing_id,
        "plan": await _record_by_table("biz_plan", plan_id),
    }


async def get_opportunity_pricing(opportunity_id: int) -> dict[str, Any]:
    """客服详情：某商机关联的航线报价 + 是否已生成对客方案。"""
    async with async_db_session() as session:
        opp = (
            await session.execute(
                text(
                    "SELECT id FROM biz_opportunity "
                    "WHERE id = :id AND is_deleted = 0"
                ),
                {"id": opportunity_id},
            )
        ).first()
        if not opp:
            raise HTTPException(status_code=404, detail="商机不存在")
        labels = await get_labels(session, "biz_pricing")
        rows = (
            await session.execute(
                text(
                    "SELECT p.*, op.opportunity_no AS parent_opportunity_no, "
                    "op.latest_quote_deadline AS parent_quote_deadline, ("
                    "  SELECT l2.to_id FROM biz_link l2 "
                    "  WHERE l2.from_table = 'pricing' AND l2.from_id = p.id "
                    "    AND l2.to_table = 'plan' AND l2.field = '被方案采用' "
                    "  ORDER BY l2.id DESC LIMIT 1"
                    ") AS plan_id, ("
                    "  SELECT COUNT(*) FROM biz_link l3 "
                    "  WHERE l3.from_table = 'pricing' AND l3.from_id = p.id "
                    "    AND l3.to_table = 'plan' AND l3.field = '被方案采用'"
                    ") AS adopted_count "
                    "FROM biz_link l "
                    "JOIN biz_pricing p ON p.id = l.to_id AND p.is_deleted = 0 "
                    "JOIN biz_opportunity op ON op.id = l.from_id AND op.is_deleted = 0 "
                    "WHERE l.from_table = 'opportunity' AND l.from_id = :oid "
                    "  AND l.to_table = 'pricing' AND l.field = '航线报价' "
                    "ORDER BY p.update_time DESC, p.id DESC"
                ),
                {"oid": opportunity_id},
            )
        ).mappings().all()
    async with async_db_session() as session2:
        vendor_rows = (
            await session2.execute(
                text(
                    "SELECT merchant_full_name, merchant_short_name FROM biz_vendor "
                    "WHERE is_deleted = 0 AND merchant_full_name IS NOT NULL "
                    "AND merchant_full_name <> '' AND merchant_short_name IS NOT NULL "
                    "AND merchant_short_name <> ''"
                )
            )
        ).all()
    vendor_short = dict(vendor_rows)

    items = []
    for r in rows:
        item = _row_to_item(r, labels)
        supplier = item["fields"].get("supplier", {}).get("value")
        if supplier in vendor_short:
            item["fields"]["supplier"]["value"] = vendor_short[supplier]
        opp_no = r.get("parent_opportunity_no") or ""
        item["fields"]["opportunity_no"] = {"label": "商机编号", "value": opp_no}
        item["fields"]["related_opportunity_no"] = {"label": "关联商机", "value": opp_no}
        item["fields"]["latest_quote_deadline"] = {
            "label": "报价截止时间",
            "value": normalize(r.get("parent_quote_deadline")),
        }
        adopted = int(r.get("adopted_count") or 0)
        item["fields"]["adopted_plan_count"] = {
            "label": "被方案采用",
            "value": f"{adopted} 条关联" if adopted else "",
        }
        item["fields"]["deal_status"] = {"label": "是否达成", "value": ""}
        created = r.get("create_time")
        week = ""
        if created:
            try:
                week = created.strftime("%Y-W%W")
            except Exception:
                week = ""
        item["fields"]["quote_create_week"] = {"label": "报价创建周", "value": week}
        items.append(
            {
                "id": int(r["id"]),
                "plan_id": int(r["plan_id"]) if r.get("plan_id") else None,
                "fields": item["fields"],
            }
        )
    return {"items": items}


PRICING_QUOTE_FIELDS = {
    "supplier",
    "supplier_quote_no",
    "supplier_quote_info",
    "cost_amount",
    "cost_currency",
    "level_ratio_requirement",
    "airline",
    "departure_port",
    "destination_port",
    "transit_point",
    "estimated_transit_days",
    "quotation_validity_period",
    "earliest_flight_time",
    "latest_arrival_time",
    "latest_confirmation_time",
    "cabin_status",
    "risk_reminder",
    "route_plan_suggestion",
    "flight_schedule",
    "origin_port_special_fee",
    "destination_port_demurrage_fee",
}


async def create_pricing_quote(opportunity_id: int, values: dict[str, Any]) -> dict[str, Any]:
    async with async_db_session() as session:
        meta = await get_table_meta(session, "biz_pricing")
        clean = {k: v for k, v in values.items() if k in PRICING_QUOTE_FIELDS and k in meta}
        for key, value in list(clean.items()):
            if isinstance(value, (list, dict)):
                clean[key] = json.dumps(value, ensure_ascii=False)
        _normalize_json_fields(meta, clean)
        _normalize_date_fields(meta, clean)
        _normalize_numeric_fields(meta, clean)
        cols = sorted(clean)
        placeholders = ", ".join(f":{col}" for col in cols)
        result = await session.execute(
            text(
                f"INSERT INTO biz_pricing ({', '.join(f'`{c}`' for c in cols)}) "
                f"VALUES ({placeholders})"
            ),
            clean,
        )
        quote_id = int(result.lastrowid)
        await session.execute(
            text(
                "INSERT INTO biz_link (field, from_table, from_id, to_table, to_id) "
                "VALUES ('航线报价', 'opportunity', :oid, 'pricing', :pid)"
            ),
            {"oid": opportunity_id, "pid": quote_id},
        )
        await session.commit()
    await publish_event(table="biz_pricing", record_id=quote_id, fields=cols, actor=None)
    try:
        async with async_db_session() as notify_session:
            opp = (
                await notify_session.execute(
                    text(
                        "SELECT opportunity_no,customer_name FROM biz_opportunity "
                        "WHERE id=:id AND is_deleted=0"
                    ),
                    {"id": opportunity_id},
                )
            ).mappings().first()
        label = f"{opp.get('opportunity_no') or opportunity_id} / {opp.get('customer_name') or ''}"
        await _notify_opportunity_owner(
            opportunity_id,
            "新增航线报价",
            f"{label} 已新增一条航线报价，请查看。",
            "/weida/customer_service",
            f"pricing-new:{opportunity_id}:{quote_id}",
        )
    except Exception:
        pass
    return await _record_by_table("biz_pricing", quote_id) or {}


async def update_pricing_quote(quote_id: int, values: dict[str, Any]) -> dict[str, Any]:
    async with async_db_session() as session:
        meta = await get_table_meta(session, "biz_pricing")
        clean = {k: v for k, v in values.items() if k in PRICING_QUOTE_FIELDS and k in meta}
        for key, value in list(clean.items()):
            if isinstance(value, (list, dict)):
                clean[key] = json.dumps(value, ensure_ascii=False)
        if not clean:
            raise HTTPException(status_code=422, detail="没有可更新的报价字段")
        _normalize_json_fields(meta, clean)
        _normalize_date_fields(meta, clean)
        _normalize_numeric_fields(meta, clean)
        sets = ", ".join(f"`{k}` = :{k}" for k in clean)
        params = dict(clean)
        params["id"] = quote_id
        result = await session.execute(
            text(
                f"UPDATE biz_pricing SET {sets}, update_time = NOW(3) "
                "WHERE id = :id AND is_deleted = 0"
            ),
            params,
        )
        await session.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="航线报价不存在")
    await publish_event(table="biz_pricing", record_id=quote_id, fields=sorted(clean), actor=None)
    return await _record_by_table("biz_pricing", quote_id) or {}


async def approve_pricing(opportunity_id: int) -> dict[str, Any]:
    """主管核价：把关联订仓单标记为已核价。"""
    async with async_db_session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT o.id FROM biz_link l JOIN biz_order o "
                    "ON o.id = l.to_id AND o.is_deleted = 0 "
                    "WHERE l.from_table = 'opportunity' AND l.from_id = :oid "
                    "AND l.to_table = 'order' AND l.field = '订单表' "
                    "ORDER BY o.id DESC LIMIT 1"
                ),
                {"oid": opportunity_id},
            )
        ).first()
        if not row:
            raise HTTPException(status_code=422, detail="尚未生成订仓单，无法核价")
        order_id = int(row[0])
        await session.execute(
            text(
                "UPDATE biz_order SET route_manager_priced = '已核价', update_time = NOW(3) "
                "WHERE id = :id AND is_deleted = 0"
            ),
            {"id": order_id},
        )
        await session.commit()
    await publish_event(
        table="biz_order", record_id=order_id, fields=["route_manager_priced"], actor=None
    )
    return {"order_id": order_id, "route_manager_priced": "已核价"}


async def get_opportunity_orders(opportunity_id: int) -> dict[str, Any]:
    async with async_db_session() as session:
        labels = await get_labels(session, "biz_order")
        rows = (
            await session.execute(
                text(
                    "SELECT l.from_id AS opportunity_id, o.*, "
                    "op.piece_count AS opp_piece_count, op.gross_weight_kg AS opp_gross_weight_kg, "
                    "op.volume_m3 AS opp_volume_m3, "
                    "op.size_length_width_height_quantity AS opp_size "
                    "FROM biz_link l JOIN biz_order o ON o.id = l.to_id AND o.is_deleted = 0 "
                    "LEFT JOIN biz_opportunity op ON op.id = l.from_id "
                    "WHERE l.from_table = 'opportunity' AND l.from_id = :oid "
                    "AND l.to_table = 'order' AND l.field = '订单表' ORDER BY o.id DESC"
                ),
                {"oid": opportunity_id},
            )
        ).mappings().all()
    return {
        "items": [
            {
                "id": int(r["id"]),
                "opportunity_id": int(r["opportunity_id"]) if r["opportunity_id"] else None,
                "goods": {
                    "piece_count": r["opp_piece_count"],
                    "gross_weight_kg": r["opp_gross_weight_kg"],
                    "volume_m3": r["opp_volume_m3"],
                    "size": r["opp_size"],
                },
                "fields": _row_to_item(r, labels)["fields"],
            }
            for r in rows
        ]
    }


ORDER_READONLY = {
    "id",
    "opportunity_no",
    "plan_no",
    "feishu_record_id",
    "create_time",
    "update_time",
    "create_by",
    "update_by",
    "is_deleted",
}


async def update_order(order_id: int, values: dict[str, Any]) -> dict[str, Any]:
    async with async_db_session() as session:
        meta = await get_table_meta(session, "biz_order")
        clean = {
            k: v
            for k, v in values.items()
            if k in meta and k not in ORDER_READONLY
        }
        if not clean:
            raise HTTPException(status_code=422, detail="没有可更新的订单字段")
        _normalize_json_fields(meta, clean)
        _normalize_date_fields(meta, clean)
        _normalize_numeric_fields(meta, clean)
        sets = ", ".join(f"`{k}` = :{k}" for k in clean)
        params = dict(clean)
        params["id"] = order_id
        result = await session.execute(
            text(
                f"UPDATE biz_order SET {sets}, update_time = NOW(3) "
                "WHERE id = :id AND is_deleted = 0"
            ),
            params,
        )
        await session.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="订仓单不存在")
    return await _record_by_table("biz_order", order_id)


def _fmt_volume(value: Any) -> str:
    if value is None or value == "":
        return ""
    text = f"{float(value):.4f}".rstrip("0").rstrip(".")
    return text or "0"


async def update_order_goods(order_id: int, values: dict[str, Any]) -> dict[str, Any]:
    """订仓单货物数据四项：件数、毛重、体积、尺码，同步回联商机。"""
    async with async_db_session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT l.from_id AS opportunity_id FROM biz_link l "
                    "WHERE l.from_table = 'opportunity' AND l.to_table = 'order' "
                    "AND l.to_id = :oid AND l.field = '订单表' LIMIT 1"
                ),
                {"oid": order_id},
            )
        ).mappings().first()
        if not row:
            raise HTTPException(status_code=422, detail="订单未关联商机")
        opp_id = int(row["opportunity_id"])
        piece = values.get("piece_count")
        gross = values.get("gross_weight_kg")
        volume = values.get("volume_m3")
        size = str(values.get("size") or "").strip()
        try:
            piece_text = str(int(float(piece))) if piece not in (None, "") else ""
        except (TypeError, ValueError):
            piece_text = ""
        try:
            gross_text = f"{float(gross):.2f}" if gross not in (None, "") else ""
        except (TypeError, ValueError):
            gross_text = ""
        volume_text = _fmt_volume(volume)
        goods = "/".join([x for x in (piece_text, gross_text, volume_text) if x])
        if size:
            goods = f"{goods} {size}" if goods else size
        chargeable = _calc_chargeable(
            float(gross) if gross not in (None, "") else None,
            float(volume) if volume not in (None, "") else None,
        )
        await session.execute(
            text(
                "UPDATE biz_opportunity SET piece_count = :piece, gross_weight_kg = :gross, "
                "volume_m3 = :volume, size_length_width_height_quantity = :size, "
                "chargeable_weight = :chargeable, update_time = NOW(3) "
                "WHERE id = :oid"
            ),
            {
                "piece": int(float(piece)) if piece_text else None,
                "gross": float(gross) if gross_text else None,
                "volume": float(volume) if volume_text else None,
                "size": size or None,
                "chargeable": chargeable,
                "oid": opp_id,
            },
        )
        await session.execute(
            text(
                "UPDATE biz_order SET goods_data = :goods, update_time = NOW(3) "
                "WHERE id = :id AND is_deleted = 0"
            ),
            {"goods": goods, "id": order_id},
        )
        await session.commit()
    await publish_event(
        table="biz_order", record_id=order_id, fields=["goods_data"], actor=None
    )
    return await _record_by_table("biz_order", order_id) or {}


async def set_bill_status(order_id: int, step: int | None) -> dict[str, Any]:
    if step is not None and step not in range(1, len(BILL_STEPS)):
        raise HTTPException(status_code=422, detail="无效的账单状态")
    async with async_db_session() as session:
        result = await session.execute(
            text(
                "UPDATE biz_order SET bill_status_step = :step, update_time = NOW(3) "
                "WHERE id = :id AND is_deleted = 0"
            ),
            {"step": step, "id": order_id},
        )
        if not result.rowcount:
            raise HTTPException(status_code=404, detail="订单不存在")
        await session.commit()
    await publish_event(table="biz_order", record_id=order_id, fields=["bill_status_step"], actor=None)
    return {"step": step, "label": BILL_STEPS[step] if step else ""}


ACTION_MAIL_SUBJECTS = {
    "pickup": "头程提货通知",
    "forecast": "国外预报",
    "invoice": "开票申请",
}
ACTION_MAIL_FIELDS = {
    "pickup": "订舱单",
    "forecast": "到货预报表",
    "invoice": "发票",
}


async def send_invoice_to_customer(order_id: int) -> dict[str, Any]:
    from .mailer import send_mail

    async with async_db_session() as session:
        order = (
            await session.execute(
                text("SELECT * FROM biz_order WHERE id=:id AND is_deleted=0"), {"id": order_id}
            )
        ).mappings().first()
        if not order:
            raise HTTPException(status_code=404, detail="订单不存在")
        files = (
            await session.execute(
                text(
                    "SELECT file_name, storage_key FROM biz_attachment "
                    "WHERE owner_table='order' AND owner_id=:id AND field='发票' ORDER BY id"
                ),
                {"id": order_id},
            )
        ).mappings().all()
    attachments = []
    for item in files:
        path = UPLOAD_DIR / item["storage_key"]
        if path.exists():
            attachments.append((item["file_name"], path.read_bytes(), "application/pdf"))
    subject = f"发票-{order.get('master_no') or order.get('order_no') or order_id}"
    body = "\n".join(
        [
            f"订单编号：{order.get('order_no') or ''}",
            f"商机编号：{order.get('opportunity_no') or ''}",
            f"客户：{order.get('customer_name') or ''}",
            f"主单号：{order.get('master_no') or ''}",
            f"成本金额：{order.get('cost_amount') or ''} {order.get('cost_currency') or ''}",
            "",
            "发票见附件。",
        ]
    )
    await run_in_threadpool(
        send_mail,
        "weida-tao@foxmail.com",
        subject,
        body,
        profile="default",
        attachments=attachments,
    )
    async with async_db_session() as session:
        await session.execute(
            text(
                "UPDATE biz_order SET invoice_sent_to_customer='是', "
                "invoice_sent_time=NOW(3), update_time=NOW(3) WHERE id=:id"
            ),
            {"id": order_id},
        )
        await session.commit()
    await publish_event(table="biz_order", record_id=order_id, fields=["invoice_sent_to_customer"], actor=None)
    return {"to": "weida-tao@foxmail.com", "attachments": len(attachments)}


async def send_action_mail(order_id: int, kind: str) -> dict[str, Any]:
    from .mailer import send_mail

    if kind not in ACTION_MAIL_SUBJECTS:
        raise HTTPException(status_code=422, detail="未知邮件类型")
    async with async_db_session() as session:
        order = (
            await session.execute(
                text("SELECT * FROM biz_order WHERE id = :id AND is_deleted = 0"),
                {"id": order_id},
            )
        ).mappings().first()
        if not order:
            raise HTTPException(status_code=404, detail="订单不存在")
        ifreights = (
            await session.execute(
                text(
                    "SELECT i.* FROM biz_link l JOIN biz_ifreight i ON i.id = l.to_id "
                    "AND i.is_deleted = 0 WHERE l.from_table = 'order' AND l.to_table = 'ifreight' "
                    "AND l.field = '所属订单' AND l.from_id = :id ORDER BY i.id"
                ),
                {"id": order_id},
            )
        ).mappings().all()
        attachments = (
            await session.execute(
                text(
                    "SELECT file_name, storage_key FROM biz_attachment "
                    "WHERE owner_table = 'order' AND owner_id = :id AND field = :field "
                    "ORDER BY id DESC"
                ),
                {"id": order_id, "field": ACTION_MAIL_FIELDS[kind]},
            )
        ).mappings().all()

    lines = [
        f"订单编号：{order.get('order_no') or ''}",
        f"商机编号：{order.get('opportunity_no') or ''}",
        f"客户：{order.get('customer_name') or ''}",
        f"客户 REF NO.：{order.get('client_ref_no') or ''}",
        f"主单号：{order.get('master_no') or ''}",
        f"航线：{order.get('departure_location') or ''} - {order.get('destination') or ''}",
        f"航班：{_text_date(order.get('flight'))}",
        f"货物：{order.get('goods_data') or order.get('goods_name_special_attr') or ''}",
    ]
    if kind == "pickup":
        lines += [
            f"提货车队：{order.get('pickup_fleet') or ''}",
            f"提货信息：{order.get('pickup_info') or ''}",
            f"货好时间：{_text_date(order.get('goods_ready_time'))}",
            f"司机信息：{order.get('driver_info') or ''}",
        ]
    elif kind == "forecast":
        lines += [
            f"预计到港：{_text_date(order.get('arrival_time') or order.get('latest_port_arrival_time'))}",
            f"代理操作要求：{order.get('agent_operation_requirement') or ''}",
        ]
        for row in ifreights:
            lines.append(f"分单 {row.get('house_bl_no') or ''}：{row.get('quantity') or ''} 件 / {row.get('gross_weight') or ''} KG / {row.get('volume') or ''} CBM")
    else:
        lines += [
            f"成本金额：{order.get('cost_amount') or ''} {order.get('cost_currency') or ''}",
            f"开票备注：{order.get('invoice_note') or ''}",
        ]
    lines += ["", "本邮件由威达 Fast 系统自动发送。"]

    files = []
    for item in attachments:
        path = UPLOAD_DIR / item["storage_key"]
        if path.exists():
            files.append((item["file_name"], path.read_bytes(), "application/octet-stream"))
    await run_in_threadpool(
        send_mail,
        "weida-tao@foxmail.com",
        f"{ACTION_MAIL_SUBJECTS[kind]}-{order.get('master_no') or order.get('order_no') or order_id}",
        "\n".join(lines),
        profile="default",
        attachments=files,
    )

    async with async_db_session() as session:
        if kind == "pickup":
            await session.execute(
                text("UPDATE biz_order SET send_pickup_email = 1, update_time = NOW(3) WHERE id = :id"),
                {"id": order_id},
            )
        elif kind == "forecast":
            await session.execute(
                text("UPDATE biz_order SET send_forecast = 1, forecast_sent_time = NOW(3), update_time = NOW(3) WHERE id = :id"),
                {"id": order_id},
            )
        else:
            await session.execute(
                text("UPDATE biz_order SET invoice_issuance = 1, update_time = NOW(3) WHERE id = :id"),
                {"id": order_id},
            )
        await session.commit()
    await publish_event(table="biz_order", record_id=order_id, fields=["send_pickup_email"], actor=None)
    return {"kind": kind, "to": "weida-tao@foxmail.com", "attachments": len(files)}


def _text_date(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    return str(value)


AI_FEE_EDITABLE = {
    "fee_category",
    "non_standard_fee_category",
    "quantity",
    "unit_price",
    "currency",
    "tax_rate",
    "tax_calc_type",
    "billing_unit",
    "separate_open",
    "fee_entry_status",
}


async def update_ai_fee(fee_id: int, values: dict[str, Any]) -> dict[str, Any]:
    clean = {k: v for k, v in values.items() if k in AI_FEE_EDITABLE}
    if not clean:
        raise HTTPException(status_code=422, detail="没有可更新字段")
    params = {"id": fee_id}
    sets = []
    for key, value in clean.items():
        if key in {"quantity", "unit_price"}:
            try:
                value = float(value) if value not in (None, "") else None
            except (TypeError, ValueError):
                raise HTTPException(status_code=422, detail=f"{key} 必须是数字")
        params[key] = value
        sets.append(f"`{key}` = :{key}")
    sets.append("update_time = NOW(3)")
    async with async_db_session() as session:
        result = await session.execute(
            text(f"UPDATE biz_aifee SET {', '.join(sets)} WHERE id=:id AND is_deleted=0"),
            params,
        )
        if not result.rowcount:
            raise HTTPException(status_code=404, detail="费用行不存在")
        await session.commit()
    return {"id": fee_id, **clean}


async def confirm_ai_fees(order_id: int) -> dict[str, Any]:
    async with async_db_session() as session:
        result = await session.execute(
            text(
                "UPDATE biz_aifee f JOIN biz_link l ON l.to_table='ifreight' AND l.to_id=f.ifreight_id "
                "AND l.from_table='order' AND l.from_id=:oid AND l.field='所属订单' "
                "SET f.fee_entry_status='已确认', f.update_time=NOW(3) "
                "WHERE f.is_deleted=0 AND f.ifreight_id IS NOT NULL "
                "AND (f.fee_entry_status IS NULL OR f.fee_entry_status<>'已确认')"
            ),
            {"oid": order_id},
        )
        fees = (
            await session.execute(
                text(
                    "SELECT f.* FROM biz_aifee f "
                    "JOIN biz_link l ON l.to_table='ifreight' AND l.to_id=f.ifreight_id "
                    "AND l.from_table='order' AND l.from_id=:oid AND l.field='所属订单' "
                    "WHERE f.is_deleted=0 AND f.ifreight_id IS NOT NULL"
                ),
                {"oid": order_id},
            )
        ).mappings().all()
        for fee in fees:
            await session.execute(
                text(
                    "INSERT INTO biz_extrafee (order_id, ifreight_id, source_ai_fee_id, "
                    "domestic_abroad, fee_category, currency, billing_unit, app_quantity, "
                    "unit_price, tax_point, tax_calc_type, fee_entry_status, extra_fee_entry) "
                    "VALUES (:oid, :iid, :fid, :label, :cat, :currency, :unit, :qty, :price, "
                    ":tax, :tax_type, '已确认', 1) ON DUPLICATE KEY UPDATE "
                    "fee_category=VALUES(fee_category), currency=VALUES(currency), "
                    "billing_unit=VALUES(billing_unit), app_quantity=VALUES(app_quantity), "
                    "unit_price=VALUES(unit_price), tax_point=VALUES(tax_point), "
                    "tax_calc_type=VALUES(tax_calc_type), fee_entry_status='已确认', extra_fee_entry=1"
                ),
                {
                    "oid": order_id,
                    "iid": int(fee["ifreight_id"]),
                    "fid": int(fee["id"]),
                    "label": fee["domestic_abroad"],
                    "cat": fee["fee_category"],
                    "currency": fee["currency"],
                    "unit": fee["billing_unit"],
                    "qty": fee["quantity"],
                    "price": fee["unit_price"],
                    "tax": fee["tax_rate"],
                    "tax_type": fee["tax_calc_type"],
                },
            )
        groups = (
            await session.execute(
                text(
                    "SELECT DISTINCT f.ifreight_id, f.domestic_abroad FROM biz_aifee f "
                    "JOIN biz_link l ON l.to_table='ifreight' AND l.to_id=f.ifreight_id "
                    "AND l.from_table='order' AND l.from_id=:oid AND l.field='所属订单' "
                    "WHERE f.is_deleted=0 AND f.ifreight_id IS NOT NULL"
                ),
                {"oid": order_id},
            )
        ).all()
        await recalc_order_cost(session, order_id)
        for iid, label in groups:
            column = "domestic_bill_entry" if str(label) == "国内" else "foreign_bill_entry"
            await session.execute(
                text(f"UPDATE biz_ifreight SET {column}=1, update_time=NOW(3) WHERE id=:id"),
                {"id": int(iid)},
            )
        await session.commit()
    return {"updated": int(result.rowcount or 0), "ifreights": len(groups), "fees": len(fees)}


async def list_order_extrafees(order_id: int) -> dict[str, Any]:
    async with async_db_session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT e.*, i.house_bl_no FROM biz_extrafee e "
                    "LEFT JOIN biz_ifreight i ON i.id=e.ifreight_id "
                    "WHERE e.order_id=:id AND e.is_deleted=0 ORDER BY e.id"
                ),
                {"id": order_id},
            )
        ).mappings().all()
    totals: dict[str, float] = {}
    for row in rows:
        currency = str(row.get("currency") or "")
        qty = float(row.get("app_quantity") or 0)
        price = float(row.get("unit_price") or 0)
        totals[currency] = totals.get(currency, 0) + qty * price
    return {"items": [dict(r) for r in rows], "totals": totals}


async def recalc_order_cost(session: AsyncSession, order_id: int) -> None:
    rows = (
        await session.execute(
            text(
                "SELECT currency, SUM(IFNULL(app_quantity,0)*IFNULL(unit_price,0)) AS amount "
                "FROM biz_extrafee WHERE order_id=:id AND is_deleted=0 GROUP BY currency"
            ),
            {"id": order_id},
        )
    ).all()
    totals = {str(r[0] or ""): float(r[1] or 0) for r in rows}
    if len([k for k, v in totals.items() if v]) == 1:
        currency, amount = next((k, v) for k, v in totals.items() if v)
        await session.execute(
            text(
                "UPDATE biz_order SET cost_amount=:amount, cost_currency=:currency, "
                "update_time=NOW(3) WHERE id=:id"
            ),
            {"amount": amount, "currency": currency, "id": order_id},
        )


async def create_extrafee(order_id: int, values: dict[str, Any]) -> dict[str, Any]:
    clean = {
        "order_id": order_id,
        "ifreight_id": values.get("ifreight_id") or None,
        "domestic_abroad": values.get("domestic_abroad") or "国内",
        "fee_category": values.get("fee_category") or "保费",
        "currency": values.get("currency") or "CNY",
        "billing_unit": values.get("billing_unit") or "PER SHIPMENT",
        "app_quantity": values.get("app_quantity") or 1,
        "unit_price": values.get("unit_price") or 0,
        "fee_entry_status": values.get("fee_entry_status") or "",
        "tax_point": values.get("tax_point") or None,
        "tax_calc_type": values.get("tax_calc_type") or None,
        "extra_fee_entry": 1,
    }
    async with async_db_session() as session:
        columns = ", ".join(f"`{k}`" for k in clean)
        params = ", ".join(f":{k}" for k in clean)
        result = await session.execute(
            text(f"INSERT INTO biz_extrafee ({columns}) VALUES ({params})"), clean
        )
        await session.commit()
        fee_id = int(result.lastrowid)
    return {"id": fee_id}


async def update_extrafee(fee_id: int, values: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "domestic_abroad", "fee_category", "currency", "billing_unit",
        "app_quantity", "unit_price", "tax_point", "tax_calc_type", "fee_entry_status",
        "extra_fee_entry",
    }
    clean = {k: v for k, v in values.items() if k in allowed}
    if not clean:
        raise HTTPException(status_code=422, detail="没有可更新字段")
    params: dict[str, Any] = {"id": fee_id}
    sets = []
    for key, value in clean.items():
        if key in {"app_quantity", "unit_price"}:
            try:
                value = float(value) if value not in (None, "") else None
            except (TypeError, ValueError):
                raise HTTPException(status_code=422, detail=f"{key} 必须是数字")
        params[key] = value
        sets.append(f"`{key}` = :{key}")
    sets.append("update_time = NOW(3)")
    async with async_db_session() as session:
        result = await session.execute(
            text(f"UPDATE biz_extrafee SET {', '.join(sets)} WHERE id=:id AND is_deleted=0"),
            params,
        )
        order_id = (
            await session.execute(
                text("SELECT order_id FROM biz_extrafee WHERE id=:id"), {"id": fee_id}
            )
        ).scalar_one_or_none()
        if not result.rowcount:
            raise HTTPException(status_code=404, detail="费用不存在")
        if order_id:
            await recalc_order_cost(session, int(order_id))
        await session.commit()
    return {"id": fee_id, **clean}


async def delete_extrafee(fee_id: int) -> dict[str, Any]:
    async with async_db_session() as session:
        result = await session.execute(
            text("UPDATE biz_extrafee SET is_deleted=1, update_time=NOW(3) WHERE id=:id"),
            {"id": fee_id},
        )
        order_id = (
            await session.execute(
                text("SELECT order_id FROM biz_extrafee WHERE id=:id"), {"id": fee_id}
            )
        ).scalar_one_or_none()
        if not result.rowcount:
            raise HTTPException(status_code=404, detail="费用不存在")
        if order_id:
            await recalc_order_cost(session, int(order_id))
        await session.commit()
    return {"id": fee_id}


async def get_user_prefs(user_id: int, role: str) -> dict[str, Any]:
    async with async_db_session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT pref_key, value FROM weida_user_pref "
                    "WHERE user_id=:uid AND role=:role"
                ),
                {"uid": user_id, "role": role},
            )
        ).all()
    return {str(k): (json.loads(v) if v else None) for k, v in rows}


async def save_user_pref(user_id: int, role: str, key: str, value: Any) -> None:
    async with async_db_session() as session:
        await session.execute(
            text(
                "INSERT INTO weida_user_pref (user_id, role, pref_key, value) "
                "VALUES (:uid, :role, :key, :value) ON DUPLICATE KEY UPDATE "
                "value=VALUES(value), update_time=NOW(3)"
            ),
            {"uid": user_id, "role": role, "key": key, "value": json.dumps(value, ensure_ascii=False)},
        )
        await session.commit()


async def get_read_state(user_id: int, role: str) -> list[int]:
    async with async_db_session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT record_id FROM weida_read_state WHERE user_id=:uid AND role=:role"
                ),
                {"uid": user_id, "role": role},
            )
        ).all()
    return [int(r[0]) for r in rows]


async def mark_read_state(user_id: int, role: str, ids: list[int]) -> None:
    if not ids:
        return
    async with async_db_session() as session:
        await session.execute(
            text(
                "INSERT IGNORE INTO weida_read_state (user_id, role, record_id) "
                "VALUES (:uid, :role, :rid)"
            ),
            [{"uid": user_id, "role": role, "rid": int(rid)} for rid in ids],
        )
        await session.commit()


async def mail_monitor_status() -> dict[str, Any]:
    names = {
        ("weida-tao@foxmail.com", "INBOX"): "客户账单确认",
        ("weida-tao@foxmail.com", "INBOX#invoice"): "发票回传",
        ("weida-tao@foxmail.com", "INBOX#overseas"): "国外账单",
        ("weida-tao@foxmail.com", "INBOX#agency-taow"): "代理账单（weida-tao）",
        ("taoair@foxmail.com", "INBOX#agency"): "代理账单（taoair）",
        ("weida-tao@foxmail.com", "INBOX#instruction"): "托书",
    }
    async with async_db_session() as session:
        rows = (
            await session.execute(
                text("SELECT account, folder, last_uid, last_scan_at FROM biz_mail_cursor")
            )
        ).mappings().all()
        counts = {}
        for field in ("确认账单文件", "发票", "国外账单附件", "账单附件", "委托书"):
            counts[field] = int(
                (await session.execute(
                    text("SELECT COUNT(*) FROM biz_attachment WHERE field=:f"), {"f": field}
                )).scalar_one()
            )
    now = datetime.now()
    items = []
    for row in rows:
        last = row["last_scan_at"]
        stale = not last or (now - last).total_seconds() > 900
        items.append(
            {
                "account": row["account"],
                "folder": row["folder"],
                "name": names.get((row["account"], row["folder"]), row["folder"]),
                "last_uid": int(row["last_uid"] or 0),
                "last_scan_at": last.isoformat(sep=" ", timespec="seconds") if last else None,
                "stale": stale,
            }
        )
    return {"items": items, "attachment_counts": counts}


async def create_approval(
    biz_type: str, biz_id: int, snapshot: dict[str, Any], applicant_id: int | None, applicant_name: str
) -> dict[str, Any]:
    async with async_db_session() as session:
        existing = (
            await session.execute(
                text(
                    "SELECT id FROM weida_approval WHERE biz_type=:t AND biz_id=:id "
                    "AND status='审批中' ORDER BY id DESC LIMIT 1"
                ),
                {"t": biz_type, "id": biz_id},
            )
        ).scalar_one_or_none()
        if existing:
            return {"id": int(existing), "status": "审批中", "existing": True}
        result = await session.execute(
            text(
                "INSERT INTO weida_approval (biz_type, biz_id, status, applicant_id, applicant_name, snapshot) "
                "VALUES (:t, :id, '审批中', :aid, :aname, :snap)"
            ),
            {
                "t": biz_type,
                "id": biz_id,
                "aid": applicant_id,
                "aname": applicant_name,
                "snap": json.dumps(snapshot, ensure_ascii=False, default=str),
            },
        )
        approval_id = int(result.lastrowid)
        if biz_type == "opportunity":
            await session.execute(
                text(
                    "UPDATE biz_opportunity SET initiate_approval=1, approval_status='审批中', "
                    "update_time=NOW(3) WHERE id=:id"
                ),
                {"id": biz_id},
            )
        await session.commit()
    await publish_event(table="biz_opportunity", record_id=biz_id, fields=["approval_status"], actor=applicant_id)
    return {"id": approval_id, "status": "审批中", "existing": False}


async def list_approvals(biz_type: str, biz_id: int) -> dict[str, Any]:
    async with async_db_session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id,biz_type,biz_id,status,applicant_name,approver_name,snapshot,comment,"
                    "create_time,decide_time FROM weida_approval WHERE biz_type=:t AND biz_id=:id "
                    "ORDER BY id DESC"
                ),
                {"t": biz_type, "id": biz_id},
            )
        ).mappings().all()
    items = []
    for row in rows:
        item = dict(row)
        try:
            item["snapshot"] = json.loads(item.get("snapshot") or "{}")
        except (TypeError, json.JSONDecodeError):
            item["snapshot"] = {}
        items.append(item)
    return {"items": items}


async def decide_approval(
    approval_id: int, approve: bool, approver_id: int | None, approver_name: str, comment: str | None
) -> dict[str, Any]:
    status = "已通过" if approve else "已驳回"
    async with async_db_session() as session:
        row = (
            await session.execute(
                text("SELECT biz_type,biz_id,status FROM weida_approval WHERE id=:id"), {"id": approval_id}
            )
        ).mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="审批不存在")
        if row["status"] != "审批中":
            raise HTTPException(status_code=422, detail="审批已处理")
        await session.execute(
            text(
                "UPDATE weida_approval SET status=:status, approver_id=:aid, approver_name=:aname, "
                "comment=:comment, decide_time=NOW(3) WHERE id=:id"
            ),
            {"status": status, "aid": approver_id, "aname": approver_name, "comment": comment, "id": approval_id},
        )
        if row["biz_type"] == "opportunity":
            await session.execute(
                text(
                    "UPDATE biz_opportunity SET approval_status=:status, update_time=NOW(3) WHERE id=:id"
                ),
                {"status": status, "id": row["biz_id"]},
            )
        await session.commit()
    await publish_event(table="biz_opportunity", record_id=int(row["biz_id"]), fields=["approval_status"], actor=approver_id)
    return {"id": approval_id, "status": status}


# 飞书自动化迁移：字段变更 → 写时间字段 / 通知。
AUTO_TIMESTAMP_RULES: dict[str, list[tuple[str, str, str]]] = {
    "biz_order": [
        ("send_forecast", "forecast_sent_time", "发送预报"),
        ("invoice_sent_to_customer", "invoice_sent_time", "发票发送客户"),
        ("finance_invoice_issued", "finance_invoice_time", "财务开票"),
        ("leader_review_status", "leader_review_time", "领导审核"),
        ("invoice_issuance", "invoice_time", "开票"),
    ],
}


AUTOMATION_REGISTRY = [
    ("invoice_time", "勾选开票 → 写开票时间", "订单表", "字段变更 + 写字段", "已迁移"),
    ("direct_ifreight", "是否直单 → iFreight 改直走单", "订单表 → iFreight", "字段变更 + 写字段", "已迁移"),
    ("leader_review_time", "领导审核通过/驳回 → 写审核时间", "订单表", "字段变更 + 写字段", "已迁移"),
    ("invoice_sent_time", "发票已发送客户 → 写发送时间", "订单表", "字段变更 + 写字段", "已迁移"),
    ("finance_invoice_time", "财务已开票 → 写财务开票时间", "订单表", "字段变更 + 写字段", "已迁移"),
    ("forecast_time", "发送预报 → 写发送时间", "订单表", "字段变更 + 写字段", "已迁移"),
    ("forecast_notify", "勾选发送预报 → 通知妙搭识别报价单", "订单表", "调外部接口", "已迁移（系统通知 + 飞书 IM）"),
    ("invoice_notify", "勾选开票 → 发开票邮件", "订单表", "调外部接口", "已迁移（开票命令）"),
    ("xanda_sync", "云港先达入青岛仓表同步", "订单表", "调外部接口", "已迁移（本地车队表）"),
    ("ifreight_quote_ai", "iFreight 报价单 AI 识别", "iFreight", "调外部接口", "已迁移（ARK 识别）"),
    ("auto_operation", "自动从订单生成操作数据", "订单表", "调外部接口 + 写字段", "已迁移（新增分单）"),
    ("handover_sync", "交接国内操作同步-重勾兜底", "订单表", "写字段 + 通知", "已迁移"),
    ("ai_doc", "AI 识别单据工作流", "iFreight", "调外部接口", "已停用（原系统停用）"),
    ("manual_operation", "手动从订单生成操作数据", "订单表", "调外部接口", "已迁移（新增分单按钮）"),
    ("opportunity_order", "从商机生成订单", "商机主表", "调外部接口", "已迁移"),
    ("push_ifreight", "操作推送数据到 iFreight", "iFreight", "调外部接口", "已迁移（分单字段写回）"),
    ("plan_quote", "从对客方案生成报价单", "对客方案", "调外部接口", "已迁移（xlsx 生成）"),
    ("order_booking", "从订单生成订舱单", "订单表", "调外部接口", "已迁移（xlsx 生成）"),
    ("opportunity_approval", "从商机发起审批", "商机主表", "调外部接口", "已迁移（本地审批）"),
    ("route_plan_draft", "从航线生成对客方案初版", "航线报价 → 对客方案", "定时 + 建记录", "已迁移"),
    ("fleet_dispute", "存疑工作流", "车队账单", "条件分支 + 通知", "已迁移（车队编辑触发）"),
    ("fleet_plan_reminder", "定时下计划提醒", "车队计划", "定时提醒", "已迁移（cron）"),
    ("fleet_goods_notify", "货物数据工作流", "车队数据", "通知", "已迁移（车队编辑触发）"),
    ("fleet_plan_notify", "下计划工作流", "车队计划", "通知 + 写字段", "已迁移（车队编辑触发）"),
    ("fleet_sync", "同步记录工作流", "车队计划 → 车队账单", "新增记录 + 建记录", "已停用（原系统停用）"),
]


FEISHU_APPROVAL_CODE = "75DFFA41-EB4B-46EE-9A30-CF413DFD2632"
APPROVAL_FIELDS = {
    "opportunityNo": "wfs_opportunityNo",
    "customer": "wfs_customer",
    "customerReference": "wfs_customerReference",
    "owner": "wfs_owner",
    "businessType": "wfs_businessType",
    "cargoName": "wfs_cargoName",
    "pieces": "wfs_pieces",
    "grossWeight": "wfs_grossWeight",
    "volume": "wfs_volume",
    "size": "wfs_size",
    "transportTerms": "wfs_transportTerms",
    "cargoReady": "wfs_cargoReady",
    "arrivalRequired": "wfs_arrivalRequired",
    "routeRequirements": "wfs_routeRequirements",
    "proposals": "wfs_proposals",
}
PROPOSAL_FIELDS = {
    "proposalNo": "wfs_proposalNo",
    "airline": "wfs_airline",
    "route": "wfs_route",
    "supplier": "wfs_supplier",
    "departure": "wfs_proposalDeparture",
    "destination": "wfs_proposalDestination",
    "routeDescription": "wfs_routeDescription",
    "priceCost": "wfs_price_sp",
    "price": "wfs_price",
    "currency": "wfs_currency",
    "earliestFlight": "wfs_earliestFlight",
    "validUntil": "wfs_validUntil",
}


def _approval_date(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    return None


async def create_feishu_approval(opportunity_id: int) -> dict[str, Any]:
    async with async_db_session() as session:
        opp = (
            await session.execute(
                text("SELECT * FROM biz_opportunity WHERE id=:id AND is_deleted=0"),
                {"id": opportunity_id},
            )
        ).mappings().first()
        if not opp:
            raise HTTPException(status_code=404, detail="商机不存在")
        plans = (
            await session.execute(
                text(
                    "SELECT p.* FROM biz_link l JOIN biz_plan p ON p.id=l.to_id AND p.is_deleted=0 "
                    "WHERE l.from_table='opportunity' AND l.from_id=:oid "
                    "AND l.to_table='plan' AND l.field='对客方案' ORDER BY p.id"
                ),
                {"oid": opportunity_id},
            )
        ).mappings().all()
        if not plans:
            raise HTTPException(status_code=422, detail="至少需要一条对客方案")
        pricings = {}
        for plan in plans:
            if plan.get("pricing_id"):
                row = (
                    await session.execute(
                        text("SELECT * FROM biz_pricing WHERE id=:id AND is_deleted=0"),
                        {"id": int(plan["pricing_id"])},
                    )
                ).mappings().first()
                if row:
                    pricings[int(plan["pricing_id"])] = row
        owner_name = str(opp.get("customer_name") or "").strip()
        people_map = await _people_map(session)
        owner_names = _person_names(_replace_people(opp.get("opportunity_person_in_charge"), people_map))
        resolved_names: list[str] = []
        for owner_candidate in owner_names:
            match = re.search(r"local-(\d+)", str(owner_candidate))
            if not match:
                resolved_names.append(owner_candidate)
                continue
            mapped = (
                await session.execute(
                    text("SELECT name FROM weida_user_map WHERE id=:id"),
                    {"id": int(match.group(1))},
                )
            ).scalar()
            if mapped:
                resolved_names.append(str(mapped))
        owner_name = resolved_names[0] if resolved_names else ""
        open_id = ""
        if owner_name:
            open_id = (
                await session.execute(
                    text("SELECT open_id FROM weida_feishu_user WHERE name=:name LIMIT 1"),
                    {"name": owner_name},
                )
            ).scalar_one_or_none() or ""
    if not owner_name:
        raise HTTPException(status_code=422, detail="商机负责人为空，无法发起审批")
    if not open_id:
        raise HTTPException(status_code=422, detail=f"找不到 {owner_name} 的飞书 open_id")
    controls: list[dict[str, Any]] = []

    def add(custom_id: str, type_name: str, value: Any, required: bool = False) -> None:
        if value in (None, "", []):
            if required:
                raise HTTPException(status_code=422, detail=f"审批必填字段为空：{custom_id}")
            return
        controls.append({"id": custom_id, "type": type_name, "value": value})

    add(APPROVAL_FIELDS["opportunityNo"], "input", opp.get("opportunity_no"), True)
    add(APPROVAL_FIELDS["customer"], "input", opp.get("customer_name"), True)
    add(APPROVAL_FIELDS["customerReference"], "input", opp.get("client_ref_no"))
    add(APPROVAL_FIELDS["owner"], "input", owner_name, True)
    add(APPROVAL_FIELDS["businessType"], "input", opp.get("business_type"))
    add(APPROVAL_FIELDS["cargoName"], "input", opp.get("goods_name"))
    add(APPROVAL_FIELDS["pieces"], "number", opp.get("piece_count"))
    add(APPROVAL_FIELDS["grossWeight"], "number", opp.get("gross_weight_kg"))
    add(APPROVAL_FIELDS["volume"], "number", opp.get("volume_m3"))
    add(APPROVAL_FIELDS["size"], "input", opp.get("size_length_width_height_quantity"))
    add(APPROVAL_FIELDS["transportTerms"], "input", opp.get("transport_clause"))
    add(APPROVAL_FIELDS["cargoReady"], "date", _approval_date(opp.get("goods_ready_time")))
    add(APPROVAL_FIELDS["arrivalRequired"], "date", _approval_date(opp.get("request_arrival_time")))
    add(APPROVAL_FIELDS["routeRequirements"], "textarea", opp.get("route_requirement"))
    rows = []
    for plan in plans:
        pricing = pricings.get(int(plan.get("pricing_id") or 0), {})
        row = []

        def radd(row: list[dict[str, Any]], key: str, type_name: str, value: Any, required: bool = False) -> None:
            if value in (None, "", []):
                if required:
                    raise HTTPException(status_code=422, detail=f"审批明细必填为空：{key}")
                return
            row.append({"id": PROPOSAL_FIELDS[key], "type": type_name, "value": value})
        radd(row, "proposalNo", "input", plan.get("scheme_no"), True)
        radd(row, "airline", "input", pricing.get("airline"))
        route = "/".join(
            x for x in (pricing.get("departure_port"), pricing.get("destination_port")) if x
        )
        radd(row, "route", "input", route)
        radd(row, "supplier", "input", pricing.get("supplier"))
        radd(row, "departure", "input", pricing.get("departure_port"))
        radd(row, "destination", "input", pricing.get("destination_port"))
        radd(row, "routeDescription", "textarea", plan.get("route_efficiency_note"))
        radd(row, "priceCost", "input", _num_text(pricing.get("cost_amount")), True)
        radd(row, "price", "input", _num_text(plan.get("customer_quote")), True)
        radd(row, "currency", "input", plan.get("quote_currency") or "CNY", True)
        radd(row, "earliestFlight", "date", _approval_date(plan.get("earliest_flight_time")))
        radd(row, "validUntil", "date", _approval_date(plan.get("quote_valid_until")))
        rows.append(row)
    if not rows:
        raise HTTPException(status_code=422, detail="至少需要一条对客方案")
    controls.append({"id": APPROVAL_FIELDS["proposals"], "type": "fieldList", "value": rows})
    token = await _feishu_token()
    if not token:
        raise HTTPException(status_code=503, detail="飞书应用凭据不可用")
    payload = {
        "approval_code": FEISHU_APPROVAL_CODE,
        "open_id": open_id,
        "form": json.dumps(controls, ensure_ascii=False, default=str),
        "uuid": uuid.uuid4().hex,
        "allow_resubmit": False,
        "allow_submit_again": False,
    }

    def call() -> dict[str, Any]:
        import urllib.error
        import urllib.request

        req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/approval/v4/instances",
            data=json.dumps(payload, ensure_ascii=False, default=str).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            return json.load(urllib.request.urlopen(req, timeout=40))
        except urllib.error.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"飞书审批创建失败：{exc.read().decode()[:300]}")

    result = await run_in_threadpool(call)
    instance_code = str((result.get("data") or {}).get("instance_code") or "")
    if result.get("code") != 0 or not instance_code:
        raise HTTPException(status_code=502, detail=f"飞书审批创建失败：{result.get('msg')}")
    async with async_db_session() as session:
        await session.execute(
            text(
                "UPDATE biz_opportunity SET initiate_approval=1, approval_status='审批中', "
                "feishu_approval_code=:code, update_time=NOW(3) WHERE id=:id"
            ),
            {"code": instance_code, "id": opportunity_id},
        )
        await session.commit()
    await publish_event(table="biz_opportunity", record_id=opportunity_id, fields=["approval_status"])
    return {"instance_code": instance_code, "owner": owner_name, "proposals": len(rows)}


def _person_names(value: Any) -> list[str]:
    data = clean_value(value)
    if isinstance(data, list):
        out = []
        for item in data:
            if isinstance(item, dict):
                name = item.get("name") or item.get("text")
                if name:
                    out.append(str(name))
            elif item is not None:
                out.append(str(item))
        return [n for n in out if n]
    if isinstance(data, dict):
        name = data.get("name") or data.get("text")
        return [str(name)] if name else []
    if data is None:
        return []
    text_value = str(data).strip().strip("[]{}")
    return [part.strip().strip('"') for part in text_value.split(",") if part.strip().strip('"')]


async def _notify_names(
    names: list[str], title: str, body: str, dedupe_key: str | None = None
) -> None:
    names = [n for n in names if n]
    if not names:
        return
    token = await _feishu_token()
    async with async_db_session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT u.name, u.roles, f.open_id FROM weida_user_map u "
                    "LEFT JOIN weida_feishu_user f ON f.name=u.name WHERE u.name IN :names"
                ).bindparams(bindparam("names", expanding=True)),
                {"names": names},
            )
        ).mappings().all()
        for row in rows:
            if token and row.get("open_id"):
                await _feishu_send_text(token, str(row["open_id"]), f"{title}\n{body}")
            try:
                roles = json.loads(row.get("roles") or "[]")
            except (TypeError, json.JSONDecodeError):
                roles = []
            role = roles[0] if isinstance(roles, list) and roles else None
            await session.execute(
                text(
                    "INSERT IGNORE INTO weida_notification (user_id, role, title, body, link, dedupe_key) "
                    "VALUES (NULL, :role, :title, :body, NULL, :key)"
                ),
                {
                    "role": role,
                    "title": title,
                    "body": body,
                    "key": f"{dedupe_key}:{row['name']}" if dedupe_key else None,
                },
            )
        await session.commit()


async def _feishu_send_text(token: str, open_id: str, text_value: str) -> None:
    def call() -> None:
        import urllib.request

        body = json.dumps(
            {
                "receive_id": open_id,
                "msg_type": "text",
                "content": json.dumps({"text": text_value}, ensure_ascii=False),
            }
        ).encode()
        req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id",
            data=body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=15).read()

    try:
        await run_in_threadpool(call)
    except Exception:
        return


async def _feishu_send_card(
    token: str, open_id: str, title: str, body: str, link: str | None = None
) -> None:
    def call() -> None:
        import urllib.request

        elements: list[dict[str, Any]] = [
            {"tag": "div", "text": {"tag": "lark_md", "content": body}}
        ]
        if link:
            elements.append(
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "打开工作台"},
                            "type": "primary",
                            "url": f"https://www.weidatao.site/web/{link.lstrip('/')}",
                        }
                    ],
                }
            )
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": "blue",
            },
            "elements": elements,
        }
        payload = json.dumps(
            {
                "receive_id": open_id,
                "msg_type": "interactive",
                "content": json.dumps(card, ensure_ascii=False),
            }
        ).encode()
        req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id",
            data=payload,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=15).read()

    try:
        await run_in_threadpool(call)
    except Exception:
        await _feishu_send_text(token, open_id, f"{title}\n{body}")


async def _notify_opportunity_owner(
    opportunity_id: int, title: str, body: str, link: str, dedupe_key: str | None = None
) -> None:
    """给商机负责人发飞书卡片；负责人字段是 `(local-12)` 这种本地映射 id。"""
    import re

    async with async_db_session() as session:
        opp = (
            await session.execute(
                text(
                    "SELECT opportunity_person_in_charge FROM biz_opportunity "
                    "WHERE id=:id AND is_deleted=0"
                ),
                {"id": opportunity_id},
            )
        ).mappings().first()
        if not opp:
            return
        raw = str(opp.get("opportunity_person_in_charge") or "").strip().strip('"').strip()
        match = re.search(r"local-(\d+)", raw)
        if match:
            person = (
                await session.execute(
                    text("SELECT name FROM weida_user_map WHERE id=:id"),
                    {"id": int(match.group(1))},
                )
            ).scalar()
        else:
            person = raw.strip("{}()").strip().strip('"').strip()
        if not person:
            return
        row = (
            await session.execute(
                text(
                    "SELECT u.id AS local_id, f.open_id FROM weida_user_map m "
                    "LEFT JOIN sys_user u ON u.name=m.name AND u.is_deleted=0 "
                    "LEFT JOIN weida_feishu_user f ON f.name=m.name WHERE m.name=:name LIMIT 1"
                ),
                {"name": person},
            )
        ).mappings().first()
    token = await _feishu_token()
    if token and row and row.get("open_id"):
        await _feishu_send_card(token, str(row["open_id"]), title, body, link)
    async with async_db_session() as session:
        await session.execute(
            text(
                "INSERT IGNORE INTO weida_notification (user_id, role, title, body, link, dedupe_key) "
                "VALUES (:uid, NULL, :title, :body, :link, :key)"
            ),
            {
                "uid": int(row["local_id"]) if row and row.get("local_id") else None,
                "title": title,
                "body": body,
                "link": link,
                "key": dedupe_key,
            },
        )
        await session.commit()


async def _notify(
    role: str | None,
    user_id: int | None,
    title: str,
    body: str,
    link: str | None = None,
    dedupe_key: str | None = None,
) -> None:
    if role:
        try:
            token = await _feishu_token()
            if token:
                async with async_db_session() as session:
                    recipients = (
                        await session.execute(
                            text(
                                "SELECT f.open_id FROM weida_user_map u "
                                "JOIN weida_feishu_user f ON f.name=u.name "
                                "WHERE JSON_CONTAINS(u.roles, JSON_QUOTE(:role))"
                            ),
                            {"role": role},
                        )
                    ).scalars().all()
                for open_id in recipients:
                    await _feishu_send_card(token, str(open_id), title, body, link)
        except Exception:
            pass
    async with async_db_session() as session:
        await session.execute(
            text(
                "INSERT IGNORE INTO weida_notification (user_id, role, title, body, link, dedupe_key) "
                "VALUES (:uid, :role, :title, :body, :link, :key)"
            ),
            {
                "uid": user_id,
                "role": role,
                "title": title,
                "body": body,
                "link": link,
                "key": dedupe_key,
            },
        )
        await session.commit()


async def run_automations(
    table: str, record_id: int, payload: dict[str, Any], actor: int | None = None
) -> None:
    """订单字段变更后的自动化。失败只记日志，不影响保存。"""
    if table == "biz_opportunity" and payload.get("send_inquiry") in (1, True, "1"):
        try:
            async with async_db_session() as notify_session:
                opp = (
                    await notify_session.execute(
                        text(
                            "SELECT opportunity_no,customer_name,destination FROM biz_opportunity "
                            "WHERE id=:id AND is_deleted=0"
                        ),
                        {"id": record_id},
                    )
                ).mappings().first()
            label = f"{opp.get('opportunity_no') or record_id} / {opp.get('customer_name') or ''}"
            await _notify(
                "pricing",
                None,
                "新的航线询价",
                f"{label} 已发送航线询价，目的港 {opp.get('destination') or '—'}，请报价。",
                "/weida/pricing",
                f"inquiry:{record_id}",
            )
        except Exception:
            pass
    rules = AUTO_TIMESTAMP_RULES.get(table, [])
    for field, target, label in rules:
        if field not in payload:
            continue
        value = payload.get(field)
        triggered = value in (1, True, "1", "是", "已通过", "已驳回")
        if not triggered:
            continue
        try:
            async with async_db_session() as session:
                await session.execute(
                    text(
                        f"UPDATE `{table}` SET `{target}` = COALESCE(`{target}`, NOW(3)) "
                        "WHERE id = :id AND is_deleted = 0"
                    ),
                    {"id": record_id},
                )
                await session.execute(
                    text(
                        "INSERT INTO weida_automation_log "
                        "(rule_key, table_name, record_id, action, status, message) "
                        "VALUES (:rule, :table, :id, :action, 'ok', :msg)"
                    ),
                    {
                        "rule": f"{table}.{field}->{target}",
                        "table": table,
                        "id": record_id,
                        "action": label,
                        "msg": f"{label}，已写 {target}",
                    },
                )
                await session.commit()
            await _notify(
                "domestic_ops" if field in ("send_forecast", "handover_domestic_operation") else None,
                None,
                f"{label}：记录 #{record_id}",
                f"{table} 的 {label} 已执行",
                "/weida/domestic_ops",
            )
        except Exception:
            continue
    if table == "biz_order":
        if "send_pickup_email" in payload or "pickup_fleet" in payload:
            try:
                await xanda_sync_order(record_id)
            except Exception:
                pass
        try:
            async with async_db_session() as session:
                if "is_direct" in payload and payload.get("is_direct") in (1, True, "1", "是"):
                    await session.execute(
                        text(
                            "UPDATE biz_ifreight i JOIN biz_link l ON l.to_table='ifreight' AND l.to_id=i.id "
                            "SET i.is_direct_order=1, i.document_type='直走单' "
                            "WHERE l.from_table='order' AND l.from_id=:id"
                        ),
                        {"id": record_id},
                    )
                    await session.execute(
                        text(
                            "INSERT INTO weida_automation_log (rule_key,table_name,record_id,action,status,message) "
                            "VALUES ('biz_order.is_direct->ifreight','biz_order',:id,'直单同步','ok','已改 iFreight 直走单')"
                        ),
                        {"id": record_id},
                    )
                    await session.commit()
                if "handover_domestic_operation" in payload and payload.get("handover_domestic_operation") in (1, True, "1"):
                    linked = (
                        await session.execute(
                            text(
                                "SELECT 1 FROM biz_link WHERE from_table='order' AND to_table='ifreight' "
                                "AND from_id=:id LIMIT 1"
                            ),
                            {"id": record_id},
                        )
                    ).first()
                    if not linked:
                        await session.commit()
                        await create_order_ifreight(record_id)
                    await session.execute(
                        text(
                            "INSERT INTO weida_automation_log (rule_key,table_name,record_id,action,status,message) "
                            "VALUES ('biz_order.handover->ifreight','biz_order',:id,'交接同步','ok','交接后已确保有分单')"
                        ),
                        {"id": record_id},
                    )
                    await session.commit()
        except Exception:
            pass


def automation_registry() -> list[dict[str, str]]:
    return [
        {"key": k, "name": name, "scope": scope, "trigger": trigger, "status": status}
        for k, name, scope, trigger, status in AUTOMATION_REGISTRY
    ]


async def list_automation_logs(limit: int = 50, table: str | None = None) -> dict[str, Any]:
    async with async_db_session() as session:
        where = "WHERE table_name = :table" if table else ""
        rows = (
            await session.execute(
                text(
                    f"SELECT * FROM weida_automation_log {where} ORDER BY id DESC LIMIT :limit"
                ),
                {"table": table, "limit": limit},
            )
        ).mappings().all()
    return {"items": [dict(r) for r in rows]}


_FEISHU_CACHE: dict[str, Any] = {"token": "", "exp": 0.0}


def _feishu_creds() -> tuple[str, str, str]:
    env_path = Path("/home/ubuntu/weida/.secrets/mail.env")
    values: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    return (
        values.get("FEISHU_APP_ID") or os.getenv("FEISHU_APP_ID", ""),
        values.get("FEISHU_APP_SECRET") or os.getenv("FEISHU_APP_SECRET", ""),
        values.get("BASE_FLEET") or os.getenv("BASE_FLEET", ""),
    )


async def _feishu_token() -> str:
    now = time.time()
    if _FEISHU_CACHE["token"] and _FEISHU_CACHE["exp"] > now + 60:
        return str(_FEISHU_CACHE["token"])
    app_id, secret, _ = _feishu_creds()
    if not app_id or not secret:
        return ""

    def fetch() -> dict[str, Any]:
        import urllib.request

        body = json.dumps({"app_id": app_id, "app_secret": secret}).encode()
        req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        return json.load(urllib.request.urlopen(req, timeout=20))

    data = await run_in_threadpool(fetch)
    token = str(data.get("tenant_access_token") or "")
    _FEISHU_CACHE["token"] = token
    _FEISHU_CACHE["exp"] = now + int(data.get("expire") or 7200)
    return token


async def _xanda_write_external(fields: dict[str, Any]) -> None:
    """把先达同步结果写回飞书「云港先达入青岛仓表」，失败不影响本地。"""
    token = await _feishu_token()
    _, _, base = _feishu_creds()
    if not token or not base:
        return
    table = "tbl5K5TvKk4x4iDs"
    ledger = str(fields.get("对账分单号") or "").strip()
    if not ledger:
        return
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {k: v for k, v in fields.items() if v not in (None, "", [], {})}

    def call() -> Any:
        import urllib.request

        search_body = json.dumps(
            {
                "filter": {
                    "conjunction": "and",
                    "conditions": [
                        {"field_name": "对账分单号", "operator": "is", "value": [ledger]}
                    ],
                },
                "page_size": 1,
            }
        ).encode()
        search = urllib.request.Request(
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{base}/tables/{table}/records/search",
            data=search_body,
            headers=headers,
            method="POST",
        )
        items = json.load(urllib.request.urlopen(search, timeout=30)).get("data", {}).get("items", [])
        if items:
            record_id = items[0]["record_id"]
            body = json.dumps({"fields": payload}).encode()
            req = urllib.request.Request(
                f"https://open.feishu.cn/open-apis/bitable/v1/apps/{base}/tables/{table}/records/{record_id}",
                data=body,
                headers=headers,
                method="PUT",
            )
        else:
            body = json.dumps({"fields": payload}).encode()
            req = urllib.request.Request(
                f"https://open.feishu.cn/open-apis/bitable/v1/apps/{base}/tables/{table}/records",
                data=body,
                headers=headers,
                method="POST",
            )
        return json.load(urllib.request.urlopen(req, timeout=30))

    try:
        await run_in_threadpool(call)
    except Exception:
        return


AIRPORT_CITY = {
    "SHA": "上海", "PVG": "上海", "PEK": "北京", "BJS": "北京", "NAY": "北京",
    "PKX": "大兴", "CGO": "郑州", "TNA": "济南", "NKG": "南京", "TSN": "天津",
    "XIY": "西安", "NGB": "宁波", "WEH": "威海", "TAO": "青岛",
}


async def xanda_sync_order(order_id: int) -> dict[str, Any]:
    """订单勾发提货邮件且车队含云港先达 → 写入本地车队计划。"""
    async with async_db_session() as session:
        order = (
            await session.execute(
                text("SELECT * FROM biz_order WHERE id=:id AND is_deleted=0"), {"id": order_id}
            )
        ).mappings().first()
    if not order:
        return {"action": "skipped", "reason": "订单不存在"}
    if order.get("send_pickup_email") not in (1, True):
        return {"action": "skipped", "reason": "未勾发提货邮件"}
    if "云港先达" not in str(order.get("pickup_fleet") or ""):
        return {"action": "skipped", "reason": "车队不含云港先达"}
    ledger = str(order.get("master_no") or order.get("client_ref_no") or "").strip()
    if not ledger:
        return {"action": "skipped", "reason": "缺少分单号"}
    operator = str(order.get("domestic_operation") or "").strip()
    city = AIRPORT_CITY.get(str(order.get("departure_location") or "").strip().upper())
    data = {
        "对账分单号": ledger,
        "车队": [{"id": "1880793107353639", "name": "用户368800"}],
        "货物数据": str(order.get("goods_data") or "").strip(),
        "备注": str(order.get("pickup_info") or "").strip(),
        "发送计划": False,
    }
    if operator:
        data["操作"] = [{"name": operator}]
    if city:
        data["起运港"] = city
    for key, col in (("要求配送时效", "warehouse_in_operation_time"), ("入仓日期", "goods_ready_time")):
        raw = order.get(col)
        if raw:
            try:
                data[key] = int(raw.timestamp() * 1000)
            except AttributeError:
                pass
    async with async_db_session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT id,data FROM fleet_plan WHERE "
                    'JSON_UNQUOTE(JSON_EXTRACT(data, \'$."对账分单号"\'))=:ledger LIMIT 1'
                ),
                {"ledger": ledger},
            )
        ).first()
        if row:
            old = row[1]
            if isinstance(old, str):
                old = json.loads(old)
            merged = dict(old or {})
            for key, value in data.items():
                if merged.get(key) in (None, "", [], {}):
                    merged[key] = value
            await session.execute(
                text("UPDATE fleet_plan SET data=:data WHERE id=:id"),
                {"data": json.dumps(merged, ensure_ascii=False), "id": row[0]},
            )
            action = "filled"
            target_id = int(row[0])
        else:
            data["发送计划"] = False
            result = await session.execute(
                text("INSERT INTO fleet_plan (feishu_record_id,data) VALUES (:rid,:data)"),
                {"rid": f"local-{order_id}", "data": json.dumps(data, ensure_ascii=False)},
            )
            target_id = int(result.lastrowid)
            action = "created"
        await session.execute(
            text(
                "INSERT INTO weida_automation_log (rule_key,table_name,record_id,action,status,message) "
                "VALUES ('xanda.sync','fleet_plan',:id,'先达同步','ok',:msg)"
            ),
            {"id": target_id, "msg": f"{action}，对账分单号 {ledger}"},
        )
        await session.commit()
    external_fields: dict[str, Any] = {
        "对账分单号": ledger,
            "货物数据": data.get("货物数据") or None,
            "起运港": data.get("起运港") or None,
            "备注": data.get("备注") or None,
        "入仓日期": data.get("入仓日期"),
        "要求配送时效": data.get("要求配送时效"),
        "车队": [{"id": "ou_d73e534ce4d8cfcb424aa5ff6b39b8df"}],
    }
    if operator:
        async with async_db_session() as session:
            open_id = (
                await session.execute(
                    text("SELECT open_id FROM weida_feishu_user WHERE name=:name LIMIT 1"),
                    {"name": operator},
                )
            ).scalar_one_or_none()
        if open_id:
            external_fields["操作"] = [{"id": str(open_id)}]
    await _xanda_write_external(external_fields)
    await _notify("domestic_ops", None, "云港先达已同步", f"订单 #{order_id} → 车队计划 #{target_id}", None)
    return {"action": action, "target_id": target_id}


async def fleet_plan_reminders() -> int:
    """定时下计划提醒：计划未发送且入仓日期在 24 小时内。"""
    now_ms = int(time.time() * 1000)
    sent = 0
    async with async_db_session() as session:
        rows = (
            await session.execute(
                text("SELECT id,data FROM fleet_plan ORDER BY id DESC LIMIT 2000")
            )
        ).all()
    for row in rows:
        data = row[1]
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                continue
        if not isinstance(data, dict):
            continue
        if str(data.get("计划发送") or "").strip() or str(data.get("发送计划") or "").strip():
            continue
        raw = data.get("入仓日期")
        if isinstance(raw, list) and raw:
            raw = raw[0]
        try:
            due = int(raw)
        except (TypeError, ValueError):
            continue
        if not (0 <= due - now_ms <= 24 * 3600 * 1000):
            continue
        key = f"fleet-plan-{row[0]}-{due}"
        await _notify(
            "domestic_ops",
            None,
            "车队计划待发送",
            f"车队计划 #{row[0]} 明日入仓，计划尚未发送",
            "/weida/domestic_ops",
            key,
        )
        sent += 1
    return sent


async def list_notifications(user_id: int | None, role: str, limit: int = 30) -> dict[str, Any]:
    async with async_db_session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT * FROM weida_notification WHERE (user_id=:uid OR (user_id IS NULL AND role=:role)) "
                    "ORDER BY id DESC LIMIT :limit"
                ),
                {"uid": user_id, "role": role, "limit": limit},
            )
        ).mappings().all()
        unread = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM weida_notification WHERE read_at IS NULL "
                    "AND (user_id=:uid OR (user_id IS NULL AND role=:role))"
                ),
                {"uid": user_id, "role": role},
            )
        ).scalar_one()
    return {"items": [dict(r) for r in rows], "unread": int(unread)}


async def mark_notifications_read(user_id: int | None, role: str) -> None:
    async with async_db_session() as session:
        await session.execute(
            text(
                "UPDATE weida_notification SET read_at=NOW(3) WHERE read_at IS NULL "
                "AND (user_id=:uid OR (user_id IS NULL AND role=:role))"
            ),
            {"uid": user_id, "role": role},
        )
        await session.commit()


FLEET_TABLES = {
    "plan": "fleet_plan",
    "data": "fleet_data",
    "bill": "fleet_bill",
    "review": "fleet_quote_review",
}


async def list_fleet(kind: str, keyword: str | None = None, page: int = 1, page_size: int = 100) -> dict[str, Any]:
    table = FLEET_TABLES.get(kind)
    if not table:
        raise HTTPException(status_code=404, detail="车队数据不存在")
    where = "1=1"
    params: dict[str, Any] = {"limit": min(page_size, 500), "offset": (page - 1) * page_size}
    if keyword:
        where = "CAST(data AS CHAR) LIKE :kw"
        params["kw"] = f"%{keyword.strip()}%"
    async with async_db_session() as session:
        total = (
            await session.execute(text(f"SELECT COUNT(*) FROM {table} WHERE {where}"), params)
        ).scalar_one()
        rows = (
            await session.execute(
                text(f"SELECT id,feishu_record_id,data FROM {table} WHERE {where} ORDER BY id DESC LIMIT :limit OFFSET :offset"),
                params,
            )
        ).mappings().all()
    columns: list[str] = []
    for r in rows:
        data = r["data"]
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                data = {}
        for k in (data or {}):
            if k not in columns:
                columns.append(k)
    items = []
    for r in rows:
        data = r["data"]
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                data = {}
        items.append({"id": r["id"], "feishu_record_id": r["feishu_record_id"], "data": data or {}})
    return {"items": items, "columns": columns, "total": int(total)}


def _fleet_value_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("name") or item.get("id") or ""))
            else:
                parts.append(str(item))
        return ", ".join(p for p in parts if p)
    if isinstance(value, dict):
        return str(value.get("text") or value.get("name") or value.get("id") or "")
    return str(value)


async def update_fleet(kind: str, record_id: int, fields: dict[str, Any]) -> dict[str, Any]:
    table = FLEET_TABLES.get(kind)
    if not table:
        raise HTTPException(status_code=404, detail="车队数据不存在")
    async with async_db_session() as session:
        row = (
            await session.execute(text(f"SELECT data FROM {table} WHERE id=:id"), {"id": record_id})
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="记录不存在")
        data = json.loads(row) if isinstance(row, str) else (row or {})
        data.update(fields)
        await session.execute(
            text(f"UPDATE {table} SET data=:data WHERE id=:id"),
            {"data": json.dumps(data, ensure_ascii=False), "id": record_id},
        )
        await session.commit()
    rule = None
    if table == "fleet_plan" and ({"发送计划", "计划发送"} & set(fields)):
        rule = "下计划工作流"
    elif table == "fleet_data" and "操作确认数据" in fields:
        rule = "货物数据工作流"
    elif table == "fleet_bill" and ({"操作确认", "做账"} & set(fields)):
        rule = "存疑工作流"
    if rule:
        async with async_db_session() as session:
            await session.execute(
                text(
                    "INSERT INTO weida_automation_log (rule_key,table_name,record_id,action,status,message) "
                    "VALUES (:rule,:table,:id,:action,'ok',:msg)"
                ),
                {
                    "rule": f"{table}.{rule}",
                    "table": table,
                    "id": record_id,
                    "action": rule,
                    "msg": f"{rule}已执行",
                },
            )
            await session.commit()
    await _notify("domestic_ops", None, f"车队数据已更新：{table} #{record_id}", rule or "车队数据已保存", None)
    return {"id": record_id, "data": data}


async def list_ai_fees(order_id: int) -> dict[str, Any]:
    async with async_db_session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT f.*, i.house_bl_no, o.master_no FROM biz_link l "
                    "JOIN biz_ifreight i ON i.id=l.to_id AND i.is_deleted=0 "
                    "JOIN biz_order o ON o.id=l.from_id AND o.is_deleted=0 "
                    "JOIN biz_aifee f ON f.ifreight_id=i.id AND f.is_deleted=0 "
                    "WHERE l.from_table='order' AND l.to_table='ifreight' "
                    "AND l.field='所属订单' AND l.from_id=:id "
                    "ORDER BY i.id, f.id"
                ),
                {"id": order_id},
            )
        ).mappings().all()
    return {"items": [dict(r) for r in rows]}


async def get_arrival_report(order_id: int) -> tuple[bytes, str]:
    from .arrival import render

    async with async_db_session() as session:
        order = (
            await session.execute(
                text("SELECT * FROM biz_order WHERE id = :id AND is_deleted = 0"),
                {"id": order_id},
            )
        ).mappings().first()
        if not order:
            raise HTTPException(status_code=404, detail="订单不存在")
        ifreights = (
            await session.execute(
                text(
                    "SELECT i.* FROM biz_link l JOIN biz_ifreight i ON i.id = l.to_id "
                    "AND i.is_deleted = 0 WHERE l.from_table = 'order' AND l.to_table = 'ifreight' "
                    "AND l.field = '所属订单' AND l.from_id = :id ORDER BY i.id"
                ),
                {"id": order_id},
            )
        ).mappings().all()
        vendor = None
        code = order.get("foreign_agent_code")
        name = order.get("foreign_agent_name")
        if code or name:
            vendor = (
                await session.execute(
                    text(
                        "SELECT * FROM biz_vendor WHERE is_deleted = 0 AND ("
                        " merchant_no = :code OR merchant_short_name = :name "
                        " OR merchant_full_name = :name) ORDER BY id LIMIT 1"
                    ),
                    {"code": code, "name": name},
                )
            ).mappings().first()
        data = render(dict(order), [dict(x) for x in ifreights], dict(vendor) if vendor else None)
        master = str(order.get("master_no") or order.get("order_no") or f"order-{order_id}").strip()
        filename = f"到货预报表-{master}.xlsx"
    await save_attachment(
        "order",
        order_id,
        "到货预报表",
        filename,
        data,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        replace=True,
    )
    return data, filename


def _parse_goods_data(text_value: Any) -> dict[str, Any]:
    """把订单货物数据 `件数/毛重/体积 尺寸` 拆成 iFreight 四列。"""
    raw = str(text_value or "").replace("\\n", "\n").strip()
    if not raw:
        return {}
    lines = [line.strip() for line in raw.split("\n") if line.strip()]
    head = lines[0] if lines else ""
    parts = [p.strip() for p in head.split("/")]
    out: dict[str, Any] = {}
    if len(parts) >= 3:
        for key, value in zip(("quantity", "gross_weight", "volume"), parts[:3], strict=False):
            try:
                out[key] = float(value)
            except ValueError:
                out[key] = None
    dimension = "\n".join(lines[1:]) if len(lines) > 1 else ""
    if dimension:
        out["dimension"] = dimension
    return out


async def create_order_ifreight(order_id: int) -> dict[str, Any]:
    async with async_db_session() as session:
        order = (
            await session.execute(
                text("SELECT * FROM biz_order WHERE id = :id AND is_deleted = 0"),
                {"id": order_id},
            )
        ).mappings().first()
        if not order:
            raise HTTPException(status_code=404, detail="订单不存在")
        result = await session.execute(
            text(
                "INSERT INTO biz_ifreight "
                "(customer_ref_no, domestic_operation, business_type, document_type, "
                " cargo_receiving_method, customs_declaration_goods_name, goods_description, "
                " transport_mode, client_name, airline_company, transport_clause, is_direct_order, "
                " business_department, remark, quantity, gross_weight, volume, dimension) "
                "VALUES (:ref, :domestic, :biz, :doctype, :cargo_method, :goods_name, :goods_desc, "
                " :transport, :client, :airline, :clause, :direct, :dept, :remark, "
                " :quantity, :gross_weight, :volume, :dimension)"
            ),
            {
                "ref": order.get("client_ref_no"),
                "domestic": order.get("domestic_operation"),
                "biz": "直客单（Direct client）",
                # 飞书「国内操作选项设置」的 BlType 选项是 主单(M)/分单(H)/直走单(D)；
                # 「是否直单=是」对应直接出主单（M），否则按分单（H）
                "doctype": "主单" if str(order.get("is_direct") or "") in ("是", "1") else "分单",
                "cargo_method": "自揽货（Local）",
                "goods_name": order.get("goods_name_special_attr"),
                "goods_desc": order.get("goods_data") or order.get("goods_name_special_attr"),
                "transport": order.get("customs_declaration_method"),
                "client": order.get("customer_name"),
                "airline": order.get("airline"),
                "clause": order.get("level_ratio_req"),
                "direct": 1 if str(order.get("is_direct") or "") in ("是", "1") else 0,
                "dept": "空运出口",
                "remark": order.get("special_note") or order.get("operation_requirement"),
                **_parse_goods_data(order.get("goods_data")),
            },
        )
        new_id = int(result.lastrowid)
        await session.execute(
            text(
                "INSERT INTO biz_link (field, from_table, from_id, to_table, to_id) "
                "VALUES ('所属订单', 'order', :oid, 'ifreight', :iid)"
            ),
            {"oid": order_id, "iid": new_id},
        )
        await session.execute(
            text(
                "UPDATE biz_order SET create_ifreight_data = 1, update_time = NOW(3) "
                "WHERE id = :id AND is_deleted = 0"
            ),
            {"id": order_id},
        )
        await session.commit()
    await publish_event(table="biz_ifreight", record_id=new_id, fields=["house_bl_no"], actor=None)
    return {"id": new_id}


def _calc_volume_from_dimension(text_value: Any) -> float:
    total = 0.0
    raw = str(text_value or "").replace("\n", " ").replace("X", "*").replace("x", "*")
    for part in raw.replace(",", " ").split():
        nums = [x for x in part.replace("×", "*").split("*") if x.strip()]
        if len(nums) < 3:
            continue
        try:
            length_cm, width_cm, height_cm = (float(nums[0]), float(nums[1]), float(nums[2]))
            qty = float(nums[3]) if len(nums) > 3 else 1.0
        except ValueError:
            continue
        total += length_cm * width_cm * height_cm * qty / 1_000_000
    return round(total, 4)


async def list_billlib_options(keyword: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    where = "is_deleted=0"
    params: dict[str, Any] = {"limit": limit}
    if keyword:
        where += " AND (copy_bill_no LIKE :kw OR shipper_name LIKE :kw OR consignee_name LIKE :kw)"
        params["kw"] = f"%{keyword.strip()}%"
    async with async_db_session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id,copy_bill_no,shipper_name,consignee_name FROM biz_billlib "
                    f"WHERE {where} ORDER BY id DESC LIMIT :limit"
                ),
                params,
            )
        ).mappings().all()
    return [dict(r) for r in rows]


async def copy_bill_to_ifreight(ifreight_id: int, billlib_id: int) -> dict[str, Any]:
    async with async_db_session() as session:
        bill = (
            await session.execute(
                text("SELECT * FROM biz_billlib WHERE id=:id AND is_deleted=0"), {"id": billlib_id}
            )
        ).mappings().first()
        if not bill:
            raise HTTPException(status_code=404, detail="提单库记录不存在")
        await session.execute(
            text(
                "UPDATE biz_ifreight SET copy_bl_no=:no, copy_bl=:url, shipper_name=:shipper, "
                "shipper_address=:saddr, consignee_name=:consignee, consignee_address=:caddr, "
                "notify_party_name=:notify, notify_party_address=:naddr, "
                "port_of_departure=:pol, dest_city_code=:pod, update_time=NOW(3) WHERE id=:id"
            ),
            {
                "no": bill.get("copy_bill_no"),
                "url": bill.get("copy_bill_no"),
                "shipper": bill.get("shipper_name"),
                "saddr": bill.get("shipper_address"),
                "consignee": bill.get("consignee_name"),
                "caddr": bill.get("consignee_address"),
                "notify": bill.get("notifier_name"),
                "naddr": bill.get("notifier_address"),
                "pol": bill.get("port_of_departure"),
                "pod": bill.get("port_of_destination"),
                "id": ifreight_id,
            },
        )
        await session.commit()
    await publish_event(table="biz_ifreight", record_id=ifreight_id, fields=["copy_bl_no"])
    return {"id": ifreight_id, "copy_bl_no": bill.get("copy_bill_no")}


async def calc_ifreight_volume(ifreight_id: int) -> dict[str, Any]:
    async with async_db_session() as session:
        row = (
            await session.execute(
                text("SELECT id,dimension,gross_weight FROM biz_ifreight WHERE id=:id AND is_deleted=0"),
                {"id": ifreight_id},
            )
        ).mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="分单不存在")
        volume = _calc_volume_from_dimension(row.get("dimension"))
        if volume <= 0:
            raise HTTPException(status_code=422, detail="尺寸格式无法识别")
        volume_weight = math.ceil(volume * 167 / 0.5) * 0.5
        gross = float(row.get("gross_weight") or 0)
        charge = max(gross, volume_weight)
        await session.execute(
            text(
                "UPDATE biz_ifreight SET volume=:volume, volume_weight=:vw, "
                "customer_billing_weight=:cw, update_time=NOW(3) WHERE id=:id"
            ),
            {"volume": volume, "vw": volume_weight, "cw": charge, "id": ifreight_id},
        )
        await session.commit()
    await publish_event(table="biz_ifreight", record_id=ifreight_id, fields=["volume"])
    return {
        "id": ifreight_id,
        "volume": volume,
        "volume_weight": volume_weight,
        "customer_billing_weight": charge,
    }


async def get_related_records(wb: WorkbenchDef, record_id: int, key: str) -> dict[str, Any]:
    rb = next((x for x in wb.related_blocks if x.key == key), None)
    if rb is None:
        raise HTTPException(status_code=404, detail="关联分区不存在")
    async with async_db_session() as session:
        labels = await get_labels(session, rb.table)
        rows = (
            await session.execute(
                text(
                    f"SELECT t.* FROM biz_link l JOIN `{rb.table}` t "
                    "ON t.id = l.to_id AND t.is_deleted = 0 "
                    "WHERE l.from_table = :ft AND l.from_id = :fid "
                    "AND l.to_table = :tt AND l.field = :field "
                    "ORDER BY t.id DESC"
                ).bindparams(),
                {
                    "ft": wb.table.removeprefix("biz_"),
                    "fid": record_id,
                    "tt": rb.table.removeprefix("biz_"),
                    "field": rb.link_field,
                },
            )
        ).mappings().all()
    return {"items": [{"id": int(r["id"]), "fields": _row_to_item(r, labels)["fields"]} for r in rows]}


async def update_related_record(
    wb: WorkbenchDef, key: str, row_id: int, values: dict[str, Any]
) -> dict[str, Any]:
    rb = next((x for x in wb.related_blocks if x.key == key), None)
    if rb is None:
        raise HTTPException(status_code=404, detail="关联分区不存在")
    async with async_db_session() as session:
        meta = await get_table_meta(session, rb.table)
        clean = {k: v for k, v in values.items() if k in meta and k not in wb.hidden}
        if not clean:
            raise HTTPException(status_code=422, detail="没有可更新的字段")
        _normalize_json_fields(meta, clean)
        _normalize_date_fields(meta, clean)
        _normalize_numeric_fields(meta, clean)
        sets = ", ".join(f"`{k}` = :{k}" for k in clean)
        params = dict(clean)
        params["id"] = row_id
        await session.execute(
            text(f"UPDATE `{rb.table}` SET {sets} WHERE id = :id AND is_deleted = 0"),
            params,
        )
        if rb.table == "biz_ifreight" and "house_bl_no" in clean:
            await session.execute(
                text(
                    "UPDATE biz_order o JOIN biz_link l ON l.from_id=o.id "
                    "SET o.house_bl_no=:no WHERE l.to_table='ifreight' AND l.to_id=:iid "
                    "AND l.from_table='order' AND l.field='所属订单'"
                ),
                {"no": clean["house_bl_no"], "iid": row_id},
            )
        await session.commit()
    await publish_event(table=rb.table, record_id=row_id, fields=sorted(clean), actor=None)
    return {"id": row_id, "fields": {k: {"value": clean[k]} for k in clean}}


async def get_opportunity_plans(opportunity_id: int) -> dict[str, Any]:
    """客服详情：某商机关联的对客方案。"""
    async with async_db_session() as session:
        labels = await get_labels(session, "biz_plan")
        rows = (
            await session.execute(
                text(
                    "SELECT p.* FROM biz_link l "
                    "JOIN biz_plan p ON p.id = l.to_id AND p.is_deleted = 0 "
                    "WHERE l.from_table = 'opportunity' AND l.from_id = :oid "
                    "  AND l.to_table = 'plan' AND l.field = '对客方案' "
                    "ORDER BY p.id DESC"
                ),
                {"oid": opportunity_id},
            )
        ).mappings().all()
    return {
        "items": [
            {"id": int(r["id"]), "fields": _row_to_item(r, labels)["fields"]}
            for r in rows
        ]
    }


async def generate_plan_for_opportunity(
    opportunity_id: int,
    pricing_id: int,
    actor: int | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """客服点「生成」：校验报价确实挂在该商机下，再生成方案并回写商机→方案关系。"""
    async with async_db_session() as session:
        linked = (
            await session.execute(
                text(
                    "SELECT id FROM biz_link "
                    "WHERE from_table = 'opportunity' AND from_id = :oid "
                    "AND to_table = 'pricing' AND to_id = :pid "
                    "AND field = '航线报价' LIMIT 1"
                ),
                {"oid": opportunity_id, "pid": pricing_id},
            )
        ).first()
        if not linked:
            raise HTTPException(status_code=422, detail="该报价不属于这个商机")

    result = await generate_plan(pricing_id, actor=actor, force=force)
    plan_id = int(result["plan"]["id"])
    async with async_db_session() as session:
        await session.execute(
            text(
                "INSERT INTO biz_link (field, from_table, from_id, to_table, to_id) "
                "VALUES ('对客方案', 'opportunity', :oid, 'plan', :plan_id) "
                "ON DUPLICATE KEY UPDATE id = id"
            ),
            {"oid": opportunity_id, "plan_id": plan_id},
        )
        await session.commit()
    result["opportunity_id"] = opportunity_id
    return result
