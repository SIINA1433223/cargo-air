"""托书邮件监控：weida-tao 收件箱，主题含「托书」。"""

from __future__ import annotations

import asyncio
import email
import imaplib
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

from sqlalchemy import bindparam, text

from app.core.database import async_db_session

from .billmail import _attachments, _body_text, _decode, _match, _order_index, _settings
from .service import save_attachment

KEYWORD = "托书"
ACCOUNT = "weida-tao@foxmail.com"
FOLDER = "INBOX#instruction"
EXTS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".jpg", ".jpeg", ".png"}


def _scan_sync(since: datetime, limit: int) -> list[dict[str, Any]]:
    host, user, password, port = _settings()
    if not password:
        raise RuntimeError("未配置 weida-tao 的 IMAP 授权码")
    client = imaplib.IMAP4_SSL(host, port, timeout=30)
    try:
        client.login(user, password)
        client.select("INBOX", readonly=False)
        typ, data = client.uid("search", None, f'(SINCE "{since.date().isoformat()}")')
        if typ != "OK":
            return []
        out = []
        for uid in [int(x) for x in data[0].split()][-limit:]:
            typ, fetch = client.uid("fetch", str(uid), "(RFC822)")
            if typ != "OK" or not fetch:
                continue
            raw = next((item[1] for item in fetch if isinstance(item, tuple)), None)
            if not raw:
                continue
            msg = email.message_from_bytes(raw)
            subject = _decode(msg.get("Subject"))
            if KEYWORD not in subject:
                continue
            try:
                sent_at = parsedate_to_datetime(msg.get("Date"))
            except (TypeError, ValueError):
                sent_at = None
            attachments = _attachments(msg)
            text_all = subject + "\n" + _body_text(msg) + "\n" + "\n".join(a["name"] for a in attachments)
            out.append({"uid": uid, "subject": subject, "sent_at": sent_at, "text": text_all, "attachments": attachments})
        return out
    finally:
        try:
            client.logout()
        except Exception:
            pass


async def _ifreights_of_order(order_id: int) -> list[int]:
    async with async_db_session() as session:
        return [
            int(r[0])
            for r in (
                await session.execute(
                    text(
                        "SELECT to_id FROM biz_link WHERE from_table='order' AND to_table='ifreight' "
                        "AND field='所属订单' AND from_id=:id"
                    ),
                    {"id": order_id},
                )
            ).all()
        ]


async def scan_instructions(*, lookback_days: int = 14, limit: int = 200) -> dict[str, Any]:
    index = await _order_index()
    async with async_db_session() as session:
        row = (
            await session.execute(
                text("SELECT last_uid FROM biz_mail_cursor WHERE account=:a AND folder=:f"),
                {"a": ACCOUNT, "f": FOLDER},
            )
        ).first()
        last_uid = int(row[0]) if row else 0
    since = datetime.now() - timedelta(days=lookback_days)
    mails = await asyncio.to_thread(_scan_sync, since, limit)
    mails = sorted((m for m in mails if m["uid"] > last_uid), key=lambda m: m["uid"])
    result = {"scanned": len(mails), "matched": 0, "attachments": 0, "skipped": 0}
    max_uid = last_uid
    for mail in mails:
        max_uid = max(max_uid, int(mail["uid"]))
        order_id = _match(mail["text"], index)
        files = [a for a in mail["attachments"] if a["ext"] in EXTS]
        if not order_id or not files:
            result["skipped"] += 1
            continue
        ifreights = await _ifreights_of_order(order_id)
        if not ifreights:
            result["skipped"] += 1
            continue
        result["matched"] += 1
        for iid in ifreights:
            async with async_db_session() as session:
                existing = {
                    r[0]
                    for r in (
                        await session.execute(
                            text(
                                "SELECT file_name FROM biz_attachment "
                                "WHERE owner_table='ifreight' AND owner_id=:id AND field='委托书'"
                            ),
                            {"id": iid},
                        )
                    ).all()
                }
            for item in files:
                if item["name"] in existing:
                    continue
                if item["ext"] in {".jpg", ".jpeg", ".png"} and len(item["content"]) < 20_000:
                    continue
                await save_attachment("ifreight", iid, "委托书", item["name"], item["content"], item["mime"])
                result["attachments"] += 1
        async with async_db_session() as session:
            await session.execute(
                text(
                    "UPDATE biz_ifreight SET power_of_attorney_ai_checkbox=1, update_time=NOW(3) "
                    "WHERE id IN :ids"
                ).bindparams(bindparam("ids", expanding=True)),
                {"ids": ifreights},
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
