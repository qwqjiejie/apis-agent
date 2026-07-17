"""模型网关状态事件在调用链与 HTTP SSE 之间的轻量传递通道。"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar

GatewayStatusQueue = asyncio.Queue[dict]

gateway_status_queue: ContextVar[GatewayStatusQueue | None] = ContextVar(
    "gateway_status_queue",
    default=None,
)


async def emit_gateway_status(**payload) -> None:
    queue = gateway_status_queue.get()
    if queue is not None:
        await queue.put(payload)


def drain_gateway_status(queue: GatewayStatusQueue) -> list[dict]:
    events = []
    while True:
        try:
            events.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            return events
