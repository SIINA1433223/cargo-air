"""客户账单确认邮件监控（weida-tao 收件箱，主题含「确认账单」）。"""

from __future__ import annotations

import email
import html
import imaplib
import re
from datetime import date, datetime, timedelta
from email.header import decode_header
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.core.database import async_db_session

from .mailer import MAIL_ENV
from .service import save_attachment

MARK = "确认账单"
FOLDER = "INBOX"
ACCOUNT = "weida-tao@foxmail.com"
OUR_DOMAIN = "@weidafreight.com"
DISPUTE = [
    re.compile(x)
    for x in ("不对", "有误", "不符", "不一致", "漏了", "漏掉", "少了", "多出", "多算", "为什么", "请核实", "核实一下", "重新核对", "有异议", "有疑问")
]
BILL_EXTS = {".pdf", ".xls", ".xlsx", ".doc", ".docx", ".csv", ".jpg", ".png"}


def _settings() -> tuple[str, str, str, int]:
    cfg: dict[str, str] = {}
    if MAIL_ENV.exists():
        for line in MAIL_ENV.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    return (
        cfg.get("MAIL_IMAP_HOST", "imap.qq.com"),
        cfg.get("MAIL_IMAP_USER", ACCOUNT),
        cfg.get("MAIL_IMAP_PASS", ""),
        int(cfg.get("MAIL_IMAP_PORT", "993")),
    )


def _decode(raw: Any) -> str:
    if raw is None:
        return ""
    parts = decode_header(str(raw))
    out = []
    for value, charset in parts:
        if isinstance(value, bytes):
            out.append(value.decode(charset or "utf-8", errors="replace"))
        else:
            out.append(value)
    return "".join(out)


def _body_text(msg: email.message.Message) -> str:
    texts: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() != "text":
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            charset = part.get_content_charset() or "utf-8"
            texts.append(payload.decode(charset, errors="replace"))
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            texts.append(payload.decode(msg.get_content_charset() or "utf-8", errors="replace"))
    raw = "\n".join(texts)
    raw = re.sub(r"<style[\s\S]*?</style>|<script[\s\S]*?</script>", " ", raw, flags=re.I)
    raw = re.sub(r"<[^>]+>", " ", raw)
    return html.unescape(re.sub(r"[ \t]+", " ", raw))


def _attachments(msg: email.message.Message) -> list[dict[str, Any]]:
    out = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        name = _decode(part.get_filename())
        if not name:
            continue
        payload = part.get_payload(decode=True) or b""
        if not payload:
            continue
        out.append(
            {
                "name": Path(name).name,
                "content": payload,
                "mime": part.get_content_type() or "application/octet-stream",
                "ext": Path(name).suffix.lower(),
                "inline": (part.get("Content-Disposition") or "").lower().startswith("inline"),
            }
        )
    return out


def _scan_sync(since: date, limit: int) -> list[dict[str, Any]]:
    host, user, password, port = _settings()
    if not password:
        raise RuntimeError("未配置 weida-tao 的 IMAP 授权码")
    client = imaplib.IMAP4_SSL(host, port, timeout=30)
    try:
        client.login(user, password)
        client.select(FOLDER, readonly=False)
        typ, data = client.uid("search", None, f'(SINCE "{since.isoformat()}")')
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
            subject = _decode(msg.get("Subject"))
            if MARK not in re.sub(r"\s+", "", subject):
                continue
            sender = email.utils.parseaddr(_decode(msg.get("From")))[1].lower()
            try:
                sent_at = parsedate_to_datetime(msg.get("Date"))
            except (TypeError, ValueError):
                sent_at = None
            body = _body_text(msg)
            text_all = subject + "\n" + body
            out.append(
                {
                    "uid": uid,
                    "subject": subject,
                    "sender": sender,
                    "sent_at": sent_at,
                    "body": body,
                    "text": text_all,
                    "attachments": _attachments(msg),
                    "raw": raw,
                    "dispute": any(p.search(text_all) for p in DISPUTE),
                }
            )
        return out
    finally:
        try:
            client.logout()
        except Exception:
            pass


async def _order_index() -> dict[str, int]:
    async with async_db_session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id, order_no, master_no, client_ref_no, opportunity_no "
                    "FROM biz_order WHERE is_deleted = 0"
                )
            )
        ).mappings().all()
        out: dict[str, int] = {}
        for row in rows:
            for key in ("order_no", "master_no", "client_ref_no", "opportunity_no"):
                value = str(row.get(key) or "").strip()
                if len(value) >= 4:
                    out[value.lower()] = int(row["id"])
        links = (
            await session.execute(
                text(
                    "SELECT l.from_id, i.house_bl_no FROM biz_link l "
                    "JOIN biz_ifreight i ON i.id = l.to_id AND i.is_deleted = 0 "
                    "WHERE l.from_table='order' AND l.to_table='ifreight' "
                    "AND l.field='所属订单' AND i.house_bl_no IS NOT NULL"
                )
            )
        ).all()
        for order_id, hawbs in links:
            for hawb in re.split(r"[\s,;/]+", str(hawbs or "")):
                if len(hawb) >= 4:
                    out[hawb.lower()] = int(order_id)
        return out


def _match(text_value: str, index: dict[str, int]) -> int | None:
    text_low = text_value.lower()
    hits = [(key, oid) for key, oid in index.items() if key in text_low]
    if not hits:
        return None
    return max(hits, key=lambda x: len(x[0]))[1]


async def scan_customer_bills(*, lookback_days: int = 14, limit: int = 200) -> dict[str, Any]:
    from fastapi.concurrency import run_in_threadpool

    index = await _order_index()
    async with async_db_session() as session:
        row = (
            await session.execute(
                text("SELECT last_uid FROM biz_mail_cursor WHERE account=:a AND folder=:f"),
                {"a": ACCOUNT, "f": FOLDER},
            )
        ).first()
        last_uid = int(row[0]) if row else 0
    since = (datetime.now() - timedelta(days=lookback_days)).date()
    mails = await run_in_threadpool(_scan_sync, since, limit)
    mails = sorted((m for m in mails if m["uid"] > last_uid), key=lambda m: m["uid"])
    result = {"scanned": len(mails), "matched": 0, "confirmed": 0, "sent": 0, "skipped": 0}
    max_uid = last_uid
    for mail in mails:
        max_uid = max(max_uid, int(mail["uid"]))
        order_id = _match(mail["text"], index)
        if not order_id:
            result["skipped"] += 1
            continue
        result["matched"] += 1
        is_ours = mail["sender"] == ACCOUNT or mail["sender"].endswith(OUR_DOMAIN)
        bill_files = [
            a for a in mail["attachments"]
            if a["ext"] in BILL_EXTS
            and not (a["ext"] in {".jpg", ".png"} and (a["inline"] or len(a["content"]) < 20_000))
        ]
        if is_ours:
            result["sent"] += 1
            for item in bill_files:
                await save_attachment("order", order_id, "确认账单文件", item["name"], item["content"], item["mime"])
        elif not mail["dispute"]:
            result["confirmed"] += 1
            for item in bill_files:
                await save_attachment("order", order_id, "确认账单文件", item["name"], item["content"], item["mime"])
        await save_attachment(
            "order",
            order_id,
            "确认邮件原文",
            f"{mail['subject'][:60] or '确认邮件'}-{mail['uid']}.eml",
            mail["raw"],
            "message/rfc822",
        )
        async with async_db_session() as session:
            if is_ours:
                await session.execute(
                    text(
                        "UPDATE biz_order SET confirm_bill_sent_to_customer='是', "
                        "bill_sent_time=COALESCE(bill_sent_time, :at), update_time=NOW(3) "
                        "WHERE id=:id"
                    ),
                    {"id": order_id, "at": mail["sent_at"]},
                )
            elif not mail["dispute"]:
                await session.execute(
                    text(
                        "UPDATE biz_order SET customer_reply_confirmation='是', "
                        "bill_confirm_time=COALESCE(bill_confirm_time, :at), update_time=NOW(3) "
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
            {"a": ACCOUNT, "f": FOLDER, "u": max_uid},
        )
        await session.commit()
    return result
