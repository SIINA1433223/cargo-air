"""工作台写后事件总线（进程内）。

单实例部署时足够；多实例时把 `publish_event` 换成 Redis PUBLISH、
`subscribe` 换成 Redis SUBSCRIBE 即可，接口不变。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

_queues: set[asyncio.Queue[dict[str, Any]]] = set()


def subscribe() -> asyncio.Queue[dict[str, Any]]:
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)
    _queues.add(queue)
    return queue


def unsubscribe(queue: asyncio.Queue[dict[str, Any]]) -> None:
    _queues.discard(queue)


async def publish_event(
    *,
    table: str,
    record_id: int,
    fields: list[str],
    actor: int | None = None,
) -> None:
    event = {
        "type": "record_changed",
        "table": table,
        "id": record_id,
        "fields": fields,
        "actor": actor,
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    payload = json.dumps(event, ensure_ascii=False)
    for queue in list(_queues):
        try:
            queue.put_nowait(json.loads(payload))
        except asyncio.QueueFull:
            # 慢客户端丢事件；前端还有 3 秒轮询兜底
            continue
