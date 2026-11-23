"""工作台 SSE 事件流。

浏览器 `EventSource` 不能带 Authorization 头，所以这里用查询参数 token，
依赖 `get_current_user_ws`（与 WebSocket 同一套校验）。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.core.dependencies import get_current_user_ws

from .events import subscribe, unsubscribe

EventsRouter = APIRouter(prefix="/weida", tags=["威达事件"])


@EventsRouter.get("/events", summary="工作台变更事件（SSE）")
async def workbench_events(
    request: Request,
    _current=Depends(get_current_user_ws),
) -> StreamingResponse:
    queue = subscribe()

    async def stream() -> AsyncGenerator[str]:
        try:
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    yield (
                        "event: record_changed\n"
                        f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    )
                except TimeoutError:
                    yield ": ping\n\n"
        finally:
            unsubscribe(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
