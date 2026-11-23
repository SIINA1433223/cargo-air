"""发票回传邮件监控：weida-tao 收件箱，发件人 2088557486@qq.com。"""

from __future__ import annotations

import email
import imaplib
import io
import re
import zipfile
from datetime import datetime, timedelta
from email.header import decode_header
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from sqlalchemy import text

from app.core.database import async_db_session

from .billmail import _match, _order_index, _settings
from .service import save_attachment

SENDER = "2088557486@qq.com"
FOLDER = "INBOX"
CURSOR_FOLDER = "INBOX#invoice"
ACCOUNT = "weida-tao@foxmail.com"
TOKEN_RE = re.compile(r"(?i)\b(TAOW[A-Z0-9-]{4,}|\d{3}-\d{8})\b")
MAX_PDFS = 8
MAX_PDF_BYTES = 40 * 1024 * 1024


def _decode(raw: Any) -> str:
    if raw is None:
        return ""
    out = []
    for value, charset in decode_header(str(raw)):
        if isinstance(value, bytes):
            out.append(value.decode(charset or "utf-8", errors="replace"))
        else:
            out.append(value)
    return "".join(out)


def _pdf_text(data: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages[:20])
    except Exception:
        return ""


def _tokens(text_value: str) -> list[str]:
    return [m.group(1).strip() for m in TOKEN_RE.finditer(text_value)]


def _pdfs(msg: email.message.Message) -> list[tuple[str, bytes]]:
    out: list[tuple[str, bytes]] = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        name = Path(_decode(part.get_filename())).name
        if not name:
            continue
        payload = part.get_payload(decode=True) or b""
        low = name.lower()
        if low.endswith(".pdf"):
            out.append((name, payload))
        elif low.endswith(".zip"):
            try:
                with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                    for info in zf.infolist()[:60]:
                        if not info.filename.lower().endswith(".pdf"):
                            continue
                        if info.file_size > MAX_PDF_BYTES:
                            continue
                        out.append((Path(info.filename).name, zf.read(info)))
            except Exception:
                continue
    return out[:MAX_PDFS]


def _scan_sync(since: datetime, limit: int) -> list[dict[str, Any]]:
    host, user, password, port = _settings()
    if not password:
        raise RuntimeError("未配置 weida-tao 的 IMAP 授权码")
    client = imaplib.IMAP4_SSL(host, port, timeout=30)
    try:
        client.login(user, password)
        client.select(FOLDER, readonly=False)
        typ, data = client.uid("search", None, f'(SINCE "{since.date().isoformat()}" FROM "{SENDER}")')
        if typ != "OK":
            return []
        uids = [int(x) for x in data[0].split()][-limit:]
        out = []
        for uid in uids:
            typ, fetch = client.uid("fetch", str(uid), "(RFC822)")
            if typ != "OK" or not fetch:
                continue
            raw = next((item[1] for item in fetch if isinstance(item, tuple)), None)
            if not raw:
                continue
            msg = email.message_from_bytes(raw)
            try:
                sent_at = parsedate_to_datetime(msg.get("Date"))
            except (TypeError, ValueError):
                sent_at = None
            out.append(
                {
                    "uid": uid,
                    "subject": _decode(msg.get("Subject")),
                    "sent_at": sent_at,
                    "pdfs": _pdfs(msg),
                }
            )
        return out
    finally:
        try:
            client.logout()
        except Exception:
            pass


async def scan_invoice_returns(*, lookback_days: int = 14, limit: int = 200) -> dict[str, Any]:
    from fastapi.concurrency import run_in_threadpool

    index = await _order_index()
    async with async_db_session() as session:
        row = (
            await session.execute(
                text("SELECT last_uid FROM biz_mail_cursor WHERE account=:a AND folder=:f"),
                {"a": ACCOUNT, "f": CURSOR_FOLDER},
            )
        ).first()
        last_uid = int(row[0]) if row else 0
    since = datetime.now() - timedelta(days=lookback_days)
    mails = await run_in_threadpool(_scan_sync, since, limit)
    mails = sorted((m for m in mails if m["uid"] > last_uid), key=lambda m: m["uid"])
    result = {"scanned": len(mails), "matched": 0, "invoices": 0, "skipped": 0}
    max_uid = last_uid
    for mail in mails:
        max_uid = max(max_uid, int(mail["uid"]))
        order_id = None
        chosen: dict[str, bytes] = {}
        for name, content in mail["pdfs"]:
            text_value = _pdf_text(content)
            token = (_tokens(text_value) or _tokens(name) or [None])[0]
            oid = _match(token or "", index) if token else None
            if not oid:
                oid = _match(name, index)
            if oid:
                order_id = order_id or oid
                if oid == order_id:
                    chosen[token or Path(name).stem] = content
        if not order_id or not chosen:
            result["skipped"] += 1
            continue
        result["matched"] += 1
        async with async_db_session() as session:
            existing = {
                r[0]
                for r in (
                    await session.execute(
                        text(
                            "SELECT file_name FROM biz_attachment "
                            "WHERE owner_table='order' AND owner_id=:id AND field='发票'"
                        ),
                        {"id": order_id},
                    )
                ).all()
            }
        for token, content in chosen.items():
            file_name = f"{token}发票.pdf"
            if file_name in existing:
                continue
            await save_attachment("order", order_id, "发票", file_name, content, "application/pdf")
            result["invoices"] += 1
        async with async_db_session() as session:
            await session.execute(
                text(
                    "UPDATE biz_order SET finance_invoice_issued='是', "
                    "finance_invoice_time=COALESCE(finance_invoice_time, :at), update_time=NOW(3) "
                    "WHERE id=:id"
                ),
                {"id": order_id, "at": mail["sent_at"]},
            )
            await session.commit()
    async with async_db_session() as session:
        await session.execute(
            text(
                "INSERT INTO biz_mail_cursor (account, folder, last_uid, last_scan_at) "
                "VALUES (:a, :f, :u, NOW()) ON DUPLICATE KEY UPDATE "
                "last_uid=GREATEST(last_uid, :u), last_scan_at=NOW()"
            ),
            {"a": ACCOUNT, "f": CURSOR_FOLDER, "u": max_uid},
        )
        await session.commit()
    return result
