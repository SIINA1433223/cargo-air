"""威达业务工作台插件入口。"""

from __future__ import annotations

import asyncio
import logging

from app.core.plugin.base import PluginBase
from app.core.plugin.context import PluginContext

from .events_router import EventsRouter
from .router import WeidaRouter

_TASKS: set[asyncio.Task] = set()


def _start(coro) -> None:
    task = asyncio.create_task(coro)
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


class Plugin(PluginBase):
    """把 `/api/v1/weida/*` 挂到应用上。"""

    def setup(self, ctx: PluginContext) -> None:
        ctx.add_router(WeidaRouter)
        ctx.add_router(EventsRouter)
        ctx.add_startup_hook(_start_mail_loops)
        ctx.add_shutdown_hook(_stop_mail_loops)


async def _instruction_mail_loop() -> None:
    from .instructionmail import scan_instructions

    logger = logging.getLogger("weida.mailwatch")
    await asyncio.sleep(100)
    while True:
        try:
            result = await scan_instructions()
            if result.get("matched"):
                logger.info("托书扫描：%s", result)
        except Exception:
            logger.exception("托书扫描失败")
        await asyncio.sleep(300)


async def _agency_mail_loop() -> None:
    from .agencymail import scan_agency_bills

    logger = logging.getLogger("weida.mailwatch")
    await asyncio.sleep(80)
    while True:
        try:
            result = await scan_agency_bills()
            if any(x.get("matched") for x in result.get("accounts", [])):
                logger.info("代理账单扫描：%s", result)
        except Exception:
            logger.exception("代理账单扫描失败")
        await asyncio.sleep(300)


async def _overseas_mail_loop() -> None:
    from .overseasmail import scan_overseas_bills

    logger = logging.getLogger("weida.mailwatch")
    await asyncio.sleep(60)
    while True:
        try:
            result = await scan_overseas_bills()
            if result.get("matched"):
                logger.info("国外账单扫描：%s", result)
        except Exception:
            logger.exception("国外账单扫描失败")
        await asyncio.sleep(300)


async def _invoice_mail_loop() -> None:
    from .invoicemail import scan_invoice_returns

    logger = logging.getLogger("weida.mailwatch")
    await asyncio.sleep(40)
    while True:
        try:
            result = await scan_invoice_returns()
            if result.get("matched"):
                logger.info("发票回传扫描：%s", result)
        except Exception:
            logger.exception("发票回传扫描失败")
        await asyncio.sleep(300)


async def _start_mail_loops() -> None:
    _start(_bill_mail_loop())
    _start(_invoice_mail_loop())
    _start(_overseas_mail_loop())
    _start(_agency_mail_loop())
    _start(_instruction_mail_loop())


async def _stop_mail_loops() -> None:
    for task in list(_TASKS):
        task.cancel()


async def _bill_mail_loop() -> None:
    from .billmail import scan_customer_bills

    logger = logging.getLogger("weida.mailwatch")
    await asyncio.sleep(20)
    while True:
        try:
            result = await scan_customer_bills()
            if result.get("matched"):
                logger.info("账单邮件扫描：%s", result)
        except Exception:
            logger.exception("账单邮件扫描失败")
        await asyncio.sleep(300)


PLUGIN = Plugin()
