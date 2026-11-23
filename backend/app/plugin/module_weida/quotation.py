"""报价单 Excel 生成：Java QuotationWorkbook 的 Python 移植版。

只负责填模板，不读写飞书。数据由 service 从本地 MySQL 取好后传入。
"""

from __future__ import annotations

from copy import copy
from io import BytesIO
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.styles import Alignment

TEMPLATE = Path(__file__).with_name("quotation-template.xlsx")
AIR = "AIR CHARGE"
LOCAL = "LOCAL Description"
DEST = "DEST Description"
FX = {"CNY": 1, "USD": 7.2, "EUR": 7.9}


def _find_label(ws, label: str) -> int:
    for row in ws.iter_rows(min_col=1, max_col=6):
        cell = row[0]
        if cell.value and str(cell.value).strip().lower() == label.lower():
            return cell.row
    if label.upper() == "TOTAL RMB":
        for row in ws.iter_rows(min_col=6, max_col=6):
            cell = row[0]
            if cell.value and str(cell.value).strip().lower() == "total rmb":
                return cell.row
    raise ValueError(f"报价单模板缺少栏位：{label}")


def _find_total(ws, start: int) -> int:
    for r in range(start, ws.max_row + 1):
        value = ws.cell(row=r, column=6).value
        if value and "TOTAL CNY" in str(value).upper():
            return r
    raise ValueError("报价单模板缺少段合计行")


def _copy_row_style(ws, source_row: int, target_row: int) -> None:
    for col in range(1, 9):
        src = ws.cell(row=source_row, column=col)
        dst = ws.cell(row=target_row, column=col)
        dst.font = copy(src.font)
        dst.fill = copy(src.fill)
        dst.border = copy(src.border)
        dst.alignment = copy(src.alignment)
        dst.number_format = src.number_format
    ws.row_dimensions[target_row].height = ws.row_dimensions[source_row].height


def _fill_section(ws, header: str, lines: list[dict[str, Any]], chargeable_ref: str) -> int:
    header_row = _find_label(ws, header)
    total_row = _find_total(ws, header_row + 2)
    data_start = header_row + 2
    existing = total_row - data_start
    needed = max(1, len(lines))
    if needed > existing:
        insert_at = data_start + existing
        for _ in range(needed - existing):
            ws.insert_rows(insert_at)
            _copy_row_style(ws, data_start, insert_at)
            total_row += 1
            insert_at += 1
    for i in range(needed):
        row = data_start + i
        if i < len(lines):
            line = lines[i]
            ws.cell(row=row, column=1, value=line["name"])
            ws.cell(row=row, column=2, value=line["currency"])
            ws.cell(row=row, column=3, value=line["amount"])
            ws.cell(row=row, column=4, value=line["uom"])
            ws.cell(row=row, column=5).value = line.get("remark") or None
            amount = f"C{row}"
            unit = str(line["uom"]).strip().upper()
            if unit == "PER CHARGE WEIGHT":
                formula = f"{amount}*{chargeable_ref}"
            elif unit == "PER SHIPMENT":
                formula = amount
            elif unit == "PER GROSS WEIGHT":
                formula = f"{amount}*{line['gross_weight']}"
            elif unit == "PER CBM":
                formula = f"{amount}*{line['volume']}"
            else:
                raise ValueError(f"不支持的计费单位：{line['uom']}")
            ws.cell(row=row, column=7, value=f"={formula}")
        else:
            for col in (1, 2, 3, 4, 5, 7):
                ws.cell(row=row, column=col).value = None
    # 模板每段预留的行数可能比实际费用多（LOCAL 默认两行），
    # 只按 needed 清空会留下模板里的“提货费”等示例行。
    for i in range(needed, existing):
        row = data_start + i
        for col in (1, 2, 3, 4, 5, 7):
            ws.cell(row=row, column=col).value = None
    if not lines:
        ws.cell(row=total_row, column=7).value = "0"
        return total_row
    terms = []
    for i in range(needed):
        row = data_start + i
        nested = "NA()"
        for currency in ("EUR", "USD"):
            nested = f'IF(B{row}="{currency}",{FX[currency]},{nested})'
        terms.append(f'G{row}*IF(B{row}="CNY",1,{nested})')
    ws.cell(row=total_row, column=7, value="=" + ("+".join(terms) if terms else "0"))
    return total_row


def render(data: dict[str, Any]) -> bytes:
    wb = load_workbook(TEMPLATE)
    ws = wb.active
    ws.cell(row=_find_label(ws, "From"), column=2, value=data.get("customer") or "")
    ws.cell(row=_find_label(ws, "Pick up address"), column=2, value=data.get("pickup") or "")
    ws.cell(row=_find_label(ws, "Delivery address"), column=2, value=data.get("delivery") or "")
    ws.cell(row=_find_label(ws, "Incotmerm"), column=2, value=data.get("incoterm") or "")
    ws.cell(row=_find_label(ws, "POL"), column=2, value=data.get("pol") or "")
    ws.cell(row=_find_label(ws, "POD"), column=2, value=data.get("pod") or "")
    cargo_row = _find_label(ws, "Cargo details")
    ws.cell(row=cargo_row, column=2, value=data.get("cargo_details") or "")
    ws.cell(row=cargo_row, column=2).alignment = Alignment(wrap_text=True, vertical="top")
    cw_row = _find_label(ws, "Chargeable weight (KGS)")
    ws.cell(row=cw_row, column=2, value=data.get("chargeable_weight"))
    cw_ref = f"$B${cw_row}"

    data["air"] = [
        {**x, "gross_weight": data["gross_weight"], "volume": data["volume"]} for x in data["air"]
    ]
    data["local"] = [
        {**x, "gross_weight": data["gross_weight"], "volume": data["volume"]} for x in data["local"]
    ]
    data["dest"] = [
        {**x, "gross_weight": data["gross_weight"], "volume": data["volume"]} for x in data["dest"]
    ]

    dest_total = _fill_section(ws, DEST, data["dest"], cw_ref)
    local_total = _fill_section(ws, LOCAL, data["local"], cw_ref)
    air_total = _fill_section(ws, AIR, data["air"], cw_ref)
    grand = _find_label(ws, "TOTAL RMB")
    ws.cell(row=grand, column=7, value=f"=G{air_total}+G{local_total}+G{dest_total}")

    remark = (data.get("remark") or "").strip()
    remark_row = None
    for row in ws.iter_rows(min_col=1, max_col=1):
        cell = row[0]
        if cell.value and str(cell.value).strip().upper().startswith("REMARK"):
            remark_row = cell.row
            break
    if remark_row is None:
        raise ValueError("报价单模板缺少栏位：REMARK")
    ws.cell(row=remark_row, column=1).value = remark or None

    wb.calculation.fullCalcOnLoad = True
    wb.calculation.forceFullCalc = True
    out = BytesIO()
    wb.save(out)
    return out.getvalue()
