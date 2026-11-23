"""到货预报表：复用妙搭样例模板，一条 iFreight 一行。"""

from __future__ import annotations

import io
import re
from copy import copy
from datetime import date, datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

TEMPLATE = Path(__file__).with_name("arrival-template.xlsx")
FIRST_ROW = 3
LAST_COL = 23
CONTACT_WORD = re.compile(
    r"(ATTN|CONTACT|TEL|FAX|FON|FONO|PH|EMAIL|E-?MAIL|MOBILE|MOB|PHONE)\s*[:：]", re.I
)
MAWB_GOODS = "CONSOL SHIPMENT"
MAWB_NOTIFY = "SAME AS CNEE"
WEIDA_SHIPPER = (
    "WEIDA FREIGHT SYSTEM CO., LTD.\n"
    "6B, UNITED MANSION, NO.9 NANJING\n"
    "RD,QINGDAO,SHANDONG,CHINA\n"
    "TEL:+86-532-85734810 FAX:+86-532-85734111"
)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y/%m/%d")
    if isinstance(value, date):
        return value.strftime("%Y/%m/%d")
    return str(value).strip()


def _date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = _text(value)
    match = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})", raw)
    if not match:
        return None
    return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _number(value: Any) -> float | str:
    if value is None or value == "":
        return ""
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value).replace(",", "").strip()
    if not raw:
        return ""
    try:
        return float(raw)
    except ValueError:
        return raw


def _strip_contacts(raw: str) -> str:
    out: list[str] = []
    for raw_line in re.sub(r"\[([^\]]+)\]\((?:https?|mailto):[^)]*\)", r"\1", raw).split("\n"):
        line = re.sub(r"^\s*(?:mailto:|https?://)\s*", "", raw_line, flags=re.I)
        if not line.strip():
            continue
        hit = CONTACT_WORD.search(line)
        if hit:
            line = line[: hit.start()]
        line = line.rstrip(" \t,;/-")
        if not line.strip() or "@" in line or re.search(r"https?://", line, re.I):
            continue
        if re.match(r"^(I\.\s?A\.|MR\.?|MRS\.?|MS\.?)\s", line.strip(), re.I):
            continue
        out.append(line.strip())
    return "\n".join(out)


def _person(name: Any, address: Any) -> str:
    head = re.sub(r"\s+", " ", _text(name)).strip()
    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in _text(address).replace("\t", " ").split("\n")
        if line.strip()
    ]
    if head and lines and lines[0].upper() == head.upper():
        lines.pop(0)
    return "\n".join([head, *lines] if head else lines)


def _vendor_consignee(vendor: dict[str, Any] | None) -> str:
    if not vendor:
        return ""
    name = _text(vendor.get("merchant_full_name") or vendor.get("merchant_short_name"))
    address = _text(vendor.get("merchant_address"))
    lines = [name]
    for line in address.split("\n"):
        clean = re.sub(r"\[([^\]]+)\]\((?:https?|mailto):[^)]*\)", r"\1", line).strip()
        if not clean or "@" in clean or re.search(r"https?://", clean, re.I):
            continue
        lines.append(clean)
    return "\n".join(x for x in lines if x)


def render(
    order: dict[str, Any],
    ifreights: list[dict[str, Any]],
    vendor: dict[str, Any] | None,
) -> bytes:
    port = _text(order.get("destination"))
    mawb = _text(order.get("master_no")) or _text((ifreights[0] if ifreights else {}).get("master_no"))
    mawb_goods = _text(order.get("main_product_english_name")) or MAWB_GOODS
    mawb_notify = _text(order.get("main_notice_person")) or MAWB_NOTIFY
    mawb_consignee = _vendor_consignee(vendor)
    rows = ifreights or [{}]
    out = load_workbook(io.BytesIO(TEMPLATE.read_bytes()))
    ws = out.worksheets[0]
    styles = []
    for col in range(1, LAST_COL + 1):
        cell = ws.cell(FIRST_ROW, col)
        styles.append(
            {
                "font": copy(cell.font),
                "border": copy(cell.border),
                "fill": copy(cell.fill),
                "alignment": copy(cell.alignment),
                "number_format": cell.number_format,
                "protection": copy(cell.protection),
            }
        )
    for index, row in enumerate(rows):
        target = FIRST_ROW + index
        if index > 0:
            for col, style in enumerate(styles, 1):
                cell = ws.cell(target, col)
                for key, value in style.items():
                    setattr(cell, key, copy(value))
            ws.row_dimensions[target].height = ws.row_dimensions[FIRST_ROW].height
        notify_name = _text(row.get("notify_party_name"))
        notify = (
            _strip_contacts(_person(notify_name, row.get("notify_party_address")))
            if notify_name
            else MAWB_NOTIFY
        )
        values = [
            index + 1,
            _date(order.get("warehouse_in_operation_time")),
            _date(order.get("flight")),
            mawb,
            _text(row.get("house_bl_no")),
            port,
            port,
            _number(row.get("quantity")),
            _number(row.get("gross_weight")),
            _number(row.get("volume")),
            _text(row.get("dimension")),
            _text(row.get("customs_declaration_goods_name")),
            _text(order.get("driver_info")),
            _text(order.get("customs_declaration_method")),
            _text(order.get("agent_operation_requirement")),
            mawb_goods,
            WEIDA_SHIPPER,
            mawb_consignee,
            mawb_notify,
            _strip_contacts(_person(row.get("shipper_name"), row.get("shipper_address"))),
            _strip_contacts(_person(row.get("consignee_name"), row.get("consignee_address"))),
            notify,
            _text(row.get("goods_description")),
        ]
        for col, value in enumerate(values, 1):
            ws.cell(target, col).value = value
    buf = io.BytesIO()
    out.save(buf)
    return buf.getvalue()
