"""账单 AI 识别：火山方舟豆包，读取 iFreight 账单附件并落费用行。"""

from __future__ import annotations

import io
import json
import re
import urllib.request
from typing import Any

from pypdf import PdfReader
from sqlalchemy import text

from app.core.database import async_db_session

from .mailer import MAIL_ENV
from .service import UPLOAD_DIR

PROMPT = """你是国际空运账单识别助手。下面是账单文本，请抽取费用行。
只输出一个 JSON 对象，不要 markdown，不要解释：
{"fees":[{"fee_category":"","non_standard_fee_category":"","operation_no":"","quantity":0,"unit_price":0,"currency":"","tax_rate":"","tax_calc_type":"","billing_unit":"","separate_open":"","fee_entry_status":"待复核"}]}
规则：
- 一行费用一个对象；没有的字段写空字符串或 0。
- 币种写 USD/CNY/EUR 等三位代码。
- 数量、单价写数字，不要带币种和逗号。
- fee_category 尽量归到：空运费、燃油附加费、安全附加费、机场操作费、报关费、提货费、派送费、仓储费、其他。
- 不确定的保留原文到 non_standard_fee_category。
账单文本：
"""


def _cfg() -> dict[str, str]:
    out: dict[str, str] = {}
    if MAIL_ENV.exists():
        for line in MAIL_ENV.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def _pdf_text(content: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(content))
        return "\n".join((page.extract_text() or "") for page in reader.pages[:20])
    except Exception:
        return ""


def _call_ark(prompt: str, images: list[str] | None = None) -> str:
    cfg = _cfg()
    key = cfg.get("ARK_API_KEY", "")
    model = cfg.get("ARK_ENDPOINT", "")
    origin = cfg.get("ARK_API_ORIGIN", "https://ark.cn-beijing.volces.com")
    if not key or not model:
        raise RuntimeError("未配置 ARK_API_KEY / ARK_ENDPOINT")
    if images:
        model = cfg.get("ARK_VISION_MODEL", model)
        content: Any = [{"type": "text", "text": prompt}]
        for image in images:
            content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image}"}})
    else:
        content = prompt
    body = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0,
    }
    req = urllib.request.Request(
        origin.rstrip("/") + "/api/v3/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=int(cfg.get("ARK_TIMEOUT", "180"))) as resp:
        data = json.load(resp)
    return str(data["choices"][0]["message"]["content"])


def _parse_json(raw: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if not match:
            raise
        return json.loads(match.group(0))


async def recognize_ifreight(
    ifreight_id: int, *, kind: str = "foreign", replace: bool = True
) -> dict[str, Any]:
    from fastapi.concurrency import run_in_threadpool

    if kind not in {"foreign", "domestic", "agency"}:
        raise ValueError("kind 只能是 foreign / domestic / agency")
    field = {
        "foreign": "国外账单附件",
        "domestic": "国内账单附件",
        "agency": "账单附件",
    }[kind]
    label = "国外" if kind == "foreign" else "国内"
    flag = "foreign_bill_ai_recog" if kind == "foreign" else "bill_ai_recognize"
    result_field = "foreign_bill_ai_recog_result" if kind == "foreign" else "bill_ai_recognize_result"
    async with async_db_session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT file_name, storage_key, mime FROM biz_attachment "
                    "WHERE owner_table='ifreight' AND owner_id=:id AND field=:field ORDER BY id"
                ),
                {"id": ifreight_id, "field": field},
            )
        ).mappings().all()
    texts: list[str] = []
    images: list[str] = []
    for row in rows:
        path = UPLOAD_DIR / row["storage_key"]
        if not path.exists():
            continue
        content = path.read_bytes()
        if str(row["mime"]).lower().endswith("pdf") or path.suffix.lower() == ".pdf":
            extracted = _pdf_text(content)
            if len(extracted.strip()) >= 80:
                texts.append(extracted)
            else:
                images.extend(_pdf_images(content))
        elif path.suffix.lower() in {".xlsx", ".xls"}:
            texts.append(_xlsx_text(content))
    merged = "\n\n".join(t for t in texts if t.strip())
    if not merged.strip() and not images:
        raise RuntimeError("附件里没有可识别文本或图片")
    if merged.strip() and len(merged.strip()) >= 80:
        raw = await run_in_threadpool(_call_ark, PROMPT + merged[:30000])
    else:
        raw = await run_in_threadpool(_call_ark, PROMPT + "（图片见附件，请直接识别图片中的账单）", images[:3])
    data = _parse_json(raw)
    fees = data.get("fees") if isinstance(data, dict) else None
    if not isinstance(fees, list):
        raise RuntimeError("AI 返回格式不正确")
    async with async_db_session() as session:
        if replace:
            await session.execute(
                text(
                    "DELETE FROM biz_aifee WHERE ifreight_id=:id AND domestic_abroad=:label "
                    "AND (source_type=:kind OR source_type IS NULL)"
                ),
                {"id": ifreight_id, "label": label, "kind": kind},
            )
        for fee in fees:
            def num(value: Any) -> float | None:
                try:
                    return float(str(value).replace(",", "").strip())
                except (TypeError, ValueError):
                    return None

            await session.execute(
                text(
                    "INSERT INTO biz_aifee (ifreight_id, fee_category, non_standard_fee_category, "
                    "operation_no, fee_code, quantity, unit_price, currency, tax_rate, tax_calc_type, "
                    "fee_entry_status, domestic_abroad, billing_unit, separate_open, source_type) VALUES "
                    "(:iid, :cat, :nonstd, :opno, :code, :qty, :price, :currency, :tax, :tax_type, "
                    ":status, :label, :unit, :separate, :kind)"
                ),
                {
                    "iid": ifreight_id,
                    "cat": str(fee.get("fee_category") or "")[:255],
                    "nonstd": str(fee.get("non_standard_fee_category") or ""),
                    "opno": str(fee.get("operation_no") or "")[:128],
                    "code": str(fee.get("fee_code") or "")[:64],
                    "qty": num(fee.get("quantity")),
                    "price": num(fee.get("unit_price")),
                    "currency": str(fee.get("currency") or "")[:255],
                    "tax": str(fee.get("tax_rate") or ""),
                    "tax_type": str(fee.get("tax_calc_type") or ""),
                    "status": str(fee.get("fee_entry_status") or "待复核"),
                    "label": label,
                    "kind": kind,
                    "unit": str(fee.get("billing_unit") or ""),
                    "separate": str(fee.get("separate_open") or ""),
                },
            )
        await session.execute(
            text(
                f"UPDATE biz_ifreight SET {flag}=1, {result_field}=:result, "
                "update_time=NOW(3) WHERE id=:id"
            ),
            {"id": ifreight_id, "result": json.dumps(data, ensure_ascii=False)[:60000]},
        )
        await session.commit()
    return {"ifreight_id": ifreight_id, "kind": kind, "fees": len(fees), "files": len(rows)}


def _pdf_images(content: bytes, max_pages: int = 2) -> list[str]:
    import base64

    try:
        import pymupdf

        doc = pymupdf.open(stream=content, filetype="pdf")
        out = []
        for page in list(doc)[:max_pages]:
            pix = page.get_pixmap(dpi=150)
            out.append(base64.b64encode(pix.tobytes("png")).decode())
        return out
    except Exception:
        return []


def _xlsx_text(content: bytes) -> str:
    try:
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(content), data_only=True, read_only=True)
        lines: list[str] = []
        for ws in wb.worksheets[:3]:
            for row in ws.iter_rows(values_only=True):
                cells = [str(x) for x in row if x not in (None, "")]
                if cells:
                    lines.append("\t".join(cells))
        return "\n".join(lines)
    except Exception:
        return ""
