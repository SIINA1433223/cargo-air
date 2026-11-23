"""由系统 cron 调用的邮件扫描入口。"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text

from app.core.database import async_db_session

from .agencymail import scan_agency_bills
from .billai import recognize_ifreight
from .billmail import scan_customer_bills
from .instructionmail import scan_instructions
from .invoicemail import scan_invoice_returns
from .overseasmail import scan_overseas_bills
from .service import fleet_plan_reminders, sync_approval_statuses

KIND_FIELD = {
    "foreign": "国外账单附件",
    "domestic": "国内账单附件",
    "agency": "账单附件",
}


async def _pending() -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    async with async_db_session() as session:
        for kind, field in KIND_FIELD.items():
            flag = "foreign_bill_ai_recog_result" if kind == "foreign" else "bill_ai_recognize_result"
            rows = (
                await session.execute(
                    text(
                        "SELECT DISTINCT a.owner_id FROM biz_attachment a "
                        "JOIN biz_ifreight i ON i.id=a.owner_id "
                        f"WHERE a.owner_table='ifreight' AND a.field=:field "
                        f"AND (i.{flag} IS NULL OR i.{flag}='')"
                    ),
                    {"field": field},
                )
            ).all()
            out.extend((int(r[0]), kind) for r in rows)
    return out


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    for name, fn in (
        ("客户账单", scan_customer_bills),
        ("发票回传", scan_invoice_returns),
        ("国外账单", scan_overseas_bills),
        ("代理账单", scan_agency_bills),
        ("托书", scan_instructions),
    ):
        try:
            result = await fn()
            print(name, result)
        except Exception as exc:
            print(name, "失败", type(exc).__name__, str(exc)[:300])
    try:
        print("审批回写", await sync_approval_statuses())
    except Exception as exc:
        print("审批回写失败", type(exc).__name__, str(exc)[:200])
    try:
        print("车队提醒", await fleet_plan_reminders())
    except Exception as exc:
        print("车队提醒失败", type(exc).__name__, str(exc)[:200])
    pending = await _pending()
    if pending:
        sem = asyncio.Semaphore(2)

        async def one(iid: int, kind: str) -> None:
            async with sem:
                try:
                    await recognize_ifreight(iid, kind=kind)
                except Exception as exc:
                    print("AI失败", iid, kind, type(exc).__name__, str(exc)[:160])

        await asyncio.gather(*(one(iid, kind) for iid, kind in pending))


if __name__ == "__main__":
    asyncio.run(main())
