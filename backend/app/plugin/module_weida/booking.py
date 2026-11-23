"""订舱单生成：复制威达模板，按 A 列标签填写 B 列。"""

from __future__ import annotations

import io
from datetime import date, datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, OneCellAnchor
from openpyxl.drawing.xdr import XDRPositiveSize2D
from openpyxl.styles import Alignment
from openpyxl.worksheet.worksheet import Worksheet

TEMPLATE = Path(__file__).with_name("booking-template.xlsx")

LABELS = (
    "订舱代理",
    "货物数据",
    "货物品名（是否需要鉴定）",
    "入仓操作时间",
    "起运地/目的地",
    "航司/主单号",
    "成本（等级、比例要求）",
    "航班",
    "操作要求",
    "操作负责人",
    "特别注意",
)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%m/%d")
    if isinstance(value, date):
        return value.strftime("%m/%d")
    return str(value).strip()


def _month_day(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.strftime("%m/%d")
    return ""


def _find_row(sheet: Worksheet, label: str) -> int:
    for row in sheet.iter_rows(min_col=1, max_col=1):
        cell = row[0]
        if cell.value is not None and str(cell.value).strip() == label:
            return cell.row
    raise ValueError(f"订舱单模板缺少栏位：{label}")


def _center(cell: Any) -> None:
    align = cell.alignment
    cell.alignment = Alignment(
        horizontal=align.horizontal,
        vertical="center",
        wrap_text=align.wrap_text,
        text_rotation=align.text_rotation,
        shrink_to_fit=align.shrink_to_fit,
        indent=align.indent,
    )


def _cost_value(amount: Any, level: Any) -> str:
    amount_text = ""
    if amount is not None:
        amount_text = f"{float(amount):g}"
    level_text = _text(level)
    if amount_text and level_text:
        return f"{amount_text}\n{level_text}"
    return amount_text or level_text


def render(data: dict[str, Any]) -> bytes:
    wb = load_workbook(TEMPLATE)
    ws = wb.active

    values = {
        "订舱代理": _text(data.get("agent")),
        "货物数据": _text(data.get("cargo")),
        "货物品名（是否需要鉴定）": _text(data.get("cargo_name")),
        "入仓操作时间": _month_day(data.get("warehouse_time")),
        "起运地/目的地": _text(data.get("route")),
        "航司/主单号": _text(data.get("airline")),
        "成本（等级、比例要求）": _cost_value(data.get("cost_amount"), data.get("level_ratio")),
        "航班": _month_day(data.get("flight")),
        "操作要求": _text(data.get("instructions")),
        "操作负责人": _text(data.get("operator")),
        "特别注意": _text(data.get("notes")),
    }
    rows: dict[str, int] = {}
    for label in LABELS:
        row = _find_row(ws, label)
        rows[label] = row
        cell = ws.cell(row=row, column=2)
        cell.value = values[label]
        _center(ws.cell(row=row, column=1))
        _center(cell)

    cargo_cell = ws.cell(row=rows["货物数据"], column=2)
    cargo_cell.alignment = Alignment(
        horizontal=cargo_cell.alignment.horizontal,
        vertical="center",
        wrap_text=True,
    )

    cost_row = rows["成本（等级、比例要求）"]
    cost_cell = ws.cell(row=cost_row, column=2)
    cost_cell.alignment = Alignment(
        horizontal=cost_cell.alignment.horizontal,
        vertical="center",
        wrap_text=True,
    )
    if "\n" in values["成本（等级、比例要求）"]:
        lines = values["成本（等级、比例要求）"].count("\n") + 1
        ws.row_dimensions[cost_row].height = lines * 30

    operator_cell = ws.cell(row=rows["操作负责人"], column=2)
    if "@" in values["操作负责人"]:
        operator_cell.hyperlink = f"mailto:{values['操作负责人']}"
        operator_cell.style = "Hyperlink"

    for image in ws._images:
        image.anchor = OneCellAnchor(
            _from=AnchorMarker(col=2, row=8),
            ext=XDRPositiveSize2D(271 * 9525, 195 * 9525),
        )
        image.width = 271
        image.height = 195

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
