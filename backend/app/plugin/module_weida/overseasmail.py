"""国外账单邮件监控：weida-tao 收件箱，主题含「国外」。"""

from __future__ import annotations

import asyncio
import email
import imaplib
import re
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

from sqlalchemy import bindparam, text

from app.core.database import async_db_session

from .billmail import _attachments, _body_text, _decode, _settings
from .service import save_attachment

KEYWORD = "国外"
FOLDER = "INBOX"
CURSOR_FOLDER = "INBOX#overseas"
ACCOUNT = "weida-tao@foxmail.com"
MASTER_RE = re.compile(r"\b\d{3}[-\s]?\d{8}\b")


def _norm_master(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z-]", "", value).upper()


def _scan_sync(since: datetime, limit: int) -> list[dict[str, Any]]:
    host, user, password, port = _settings()
    if not password:
        raise RuntimeError("未配置 weida-tao 的 IMAP 授权码")
    client = imaplib.IMAP4_SSL(host, port, timeout=30)
    try:
        client.login(user, password)
        client.select(FOLDER, readonly=False)
        typ, data = client.uid("search", None, f'(SINCE "{since.date().isoformat()}")')
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
            if KEYWORD not in subject:
                continue
            body = _body_text(msg)
            try:
                sent_at = parsedate_to_datetime(msg.get("Date"))
            except (TypeError, ValueError):
                sent_at = None
            text_all = subject + "\n" + body + "\n" + "\n".join(
                a["name"] for a in _attachments(msg)
            )
            masters = sorted({_norm_master(x) for x in MASTER_RE.findall(text_all)})
            out.append(
                {
                    "uid": uid,
                    "subject": subject,
                    "sent_at": sent_at,
                    "masters": masters,
                    "attachments": _attachments(msg),
                }
            )
        return out
    finally:
        try:
            client.logout()
        except Exception:
            pass


async def _ifreight_index() -> dict[str, list[int]]:
    async with async_db_session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT i.id, o.master_no FROM biz_link l "
                    "JOIN biz_ifreight i ON i.id=l.to_id AND i.is_deleted=0 "
                    "JOIN biz_order o ON o.id=l.from_id AND o.is_deleted=0 "
                    "WHERE l.from_table='order' AND l.to_table='ifreight' "
                    "AND l.field='所属订单' AND o.master_no IS NOT NULL AND o.master_no<>''"
                )
            )
        ).all()
    out: dict[str, list[int]] = {}
    for iid, master in rows:
        key = _norm_master(str(master))
        if key:
            out.setdefault(key, []).append(int(iid))
    return out


async def scan_overseas_bills(*, lookback_days: int = 14, limit: int = 200) -> dict[str, Any]:
    from fastapi.concurrency import run_in_threadpool

    index = await _ifreight_index()
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
    result = {"scanned": len(mails), "matched": 0, "attachments": 0, "skipped": 0}
    pending: list[int] = []
    max_uid = last_uid
    for mail in mails:
        max_uid = max(max_uid, int(mail["uid"]))
        target_ids: list[int] = []
        for master in mail["masters"]:
            for iid in index.get(master, []):
                if iid not in target_ids:
                    target_ids.append(iid)
        files = [
            a for a in mail["attachments"]
            if a["ext"] in {".pdf", ".xls", ".xlsx", ".doc", ".docx", ".csv"}
        ]
        if not target_ids or not files:
            result["skipped"] += 1
            continue
        result["matched"] += 1
        for iid in target_ids:
            async with async_db_session() as session:
                existing = {
                    r[0]
                    for r in (
                        await session.execute(
                            text(
                                "SELECT file_name FROM biz_attachment "
                                "WHERE owner_table='ifreight' AND owner_id=:id AND field='国外账单附件'"
                            ),
                            {"id": iid},
                        )
                    ).all()
                }
            for item in files:
                if item["name"] in existing:
                    continue
                await save_attachment("ifreight", iid, "国外账单附件", item["name"], item["content"], item["mime"])
                result["attachments"] += 1
        async with async_db_session() as session:
            found = [
                int(r[0])
                for r in (
                    await session.execute(
                        text(
                            "SELECT id FROM biz_ifreight WHERE id IN :ids "
                            "AND (foreign_bill_ai_recog_result IS NULL OR foreign_bill_ai_recog_result='')"
                        ).bindparams(bindparam("ids", expanding=True)),
                        {"ids": target_ids},
                    )
                ).all()
            ]
            pending.extend(i for i in found if i not in pending)
            await session.execute(
                text(
                    "UPDATE biz_ifreight SET foreign_bill_ai_recog=1, update_time=NOW(3) "
                    "WHERE id IN :ids"
                ).bindparams(bindparam("ids", expanding=True)),
                {"ids": target_ids},
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
    if pending:
        asyncio.create_task(_recognize_many(pending))
    return result


async def _recognize_many(ids: list[int]) -> None:
    from .billai import recognize_ifreight

    sem = asyncio.Semaphore(2)

    async def one(iid: int) -> None:
        async with sem:
            try:
                await recognize_ifreight(iid)
            except Exception:
                pass

    await asyncio.gather(*(one(iid) for iid in ids))
