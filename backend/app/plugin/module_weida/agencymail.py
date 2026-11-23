"""代理账单邮件监控：两个邮箱，主题含「青岛威达」。"""

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

from .billmail import _attachments, _body_text, _decode
from .mailer import MAIL_ENV
from .overseasmail import _ifreight_index, _norm_master
from .service import save_attachment

KEYWORD = "青岛威达"
MASTER_RE = re.compile(r"\b\d{3}[-\s]?\d{8}\b")
ACCOUNTS = (
    {"label": "taoair", "user": "taoair@foxmail.com", "folder": "INBOX#agency", "prefix": "AGENCY_"},
    {"label": "weida-tao", "user": "weida-tao@foxmail.com", "folder": "INBOX#agency-taow", "prefix": ""},
)


def _cfg() -> dict[str, str]:
    out: dict[str, str] = {}
    if MAIL_ENV.exists():
        for line in MAIL_ENV.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def _scan_sync(account: dict[str, str], since: datetime, limit: int) -> list[dict[str, Any]]:
    cfg = _cfg()
    prefix = account["prefix"]
    host = cfg.get(prefix + "IMAP_HOST") or cfg.get("MAIL_IMAP_HOST") or "imap.qq.com"
    port = int(cfg.get(prefix + "IMAP_PORT") or cfg.get("MAIL_IMAP_PORT") or "993")
    user = account["user"]
    password = cfg.get(prefix + "IMAP_PASS") or cfg.get("MAIL_IMAP_PASS") or ""
    if not password:
        raise RuntimeError(f"未配置 {account['label']} 的 IMAP 授权码")
    client = imaplib.IMAP4_SSL(host, port, timeout=30)
    try:
        client.login(user, password)
        client.select("INBOX", readonly=False)
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
            try:
                sent_at = parsedate_to_datetime(msg.get("Date"))
            except (TypeError, ValueError):
                sent_at = None
            attachments = _attachments(msg)
            text_all = subject + "\n" + _body_text(msg) + "\n" + "\n".join(a["name"] for a in attachments)
            out.append(
                {
                    "uid": uid,
                    "subject": subject,
                    "sent_at": sent_at,
                    "masters": sorted({_norm_master(x) for x in MASTER_RE.findall(text_all)}),
                    "attachments": attachments,
                }
            )
        return out
    finally:
        try:
            client.logout()
        except Exception:
            pass


async def _scan_one(account: dict[str, str], index: dict[str, list[int]], lookback_days: int, limit: int) -> dict[str, Any]:
    async with async_db_session() as session:
        row = (
            await session.execute(
                text("SELECT last_uid FROM biz_mail_cursor WHERE account=:a AND folder=:f"),
                {"a": account["user"], "f": account["folder"]},
            )
        ).first()
        last_uid = int(row[0]) if row else 0
    since = datetime.now() - timedelta(days=lookback_days)
    mails = await asyncio.to_thread(_scan_sync, account, since, limit)
    mails = sorted((m for m in mails if m["uid"] > last_uid), key=lambda m: m["uid"])
    result = {"account": account["label"], "scanned": len(mails), "matched": 0, "attachments": 0, "skipped": 0}
    pending: list[int] = []
    max_uid = last_uid
    for mail in mails:
        max_uid = max(max_uid, int(mail["uid"]))
        target_ids: list[int] = []
        for master in mail["masters"]:
            for iid in index.get(master, []):
                if iid not in target_ids:
                    target_ids.append(iid)
        files = [a for a in mail["attachments"] if a["ext"] in {".pdf", ".xls", ".xlsx", ".doc", ".docx", ".csv"}]
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
                                "WHERE owner_table='ifreight' AND owner_id=:id AND field='账单附件'"
                            ),
                            {"id": iid},
                        )
                    ).all()
                }
            for item in files:
                if item["name"] in existing:
                    continue
                await save_attachment("ifreight", iid, "账单附件", item["name"], item["content"], item["mime"])
                result["attachments"] += 1
            pending.append(iid)
        async with async_db_session() as session:
            await session.execute(
                text("UPDATE biz_ifreight SET bill_ai_recognize=1, update_time=NOW(3) WHERE id IN :ids")
                .bindparams(bindparam("ids", expanding=True)),
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
            {"a": account["user"], "f": account["folder"], "u": max_uid},
        )
        await session.commit()
    if pending:
        asyncio.create_task(_recognize_many(pending))
    return result


async def scan_agency_bills(*, lookback_days: int = 14, limit: int = 200) -> dict[str, Any]:
    index = await _ifreight_index()
    results = []
    for account in ACCOUNTS:
        try:
            results.append(await _scan_one(account, index, lookback_days, limit))
        except Exception as exc:
            results.append({"account": account["label"], "error": str(exc)[:300]})
    return {"accounts": results}


async def _recognize_many(ids: list[int]) -> None:
    from .billai import recognize_ifreight

    sem = asyncio.Semaphore(2)

    async def one(iid: int) -> None:
        async with sem:
            try:
                await recognize_ifreight(iid, kind="domestic")
            except Exception:
                pass

    await asyncio.gather(*(one(iid) for iid in ids))
