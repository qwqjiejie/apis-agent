"""任务事件总线。任务状态变更时的异步通知机制。

Redis Pub/Sub 作为跨进程传输层，本地 handler 作为进程内快速通道。
Redis 不可用时降级为纯内存模式（单进程内可用）。

事件类型约定：
    task.{status} — 任务状态变更 (created/executing/completed/failed/cancelled/interrupted/resumed)
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable

logger = logging.getLogger("apis")

Handler = Callable[[dict[str, Any]], Any]


class EventBus:
    """轻量事件总线。"""

    def __init__(self, redis_client=None):
        self._redis = redis_client
        self._handlers: dict[str, list[Handler]] = {}
        self._redis_available = redis_client is not None

    def set_redis(self, redis_client):
        """注入 Redis 客户端，启用跨进程 Pub/Sub。

        lifespan 中调用，让当前应用实例在 Redis 可用时从纯内存模式
        升级为跨进程广播模式。
        """
        self._redis = redis_client
        self._redis_available = redis_client is not None

    def subscribe(self, event_type: str, handler: Handler):
        """注册事件处理器。返回取消订阅函数。"""
        self._handlers.setdefault(event_type, []).append(handler)

        def unsubscribe():
            try:
                self._handlers.get(event_type, []).remove(handler)
            except ValueError:
                pass
        return unsubscribe

    async def publish(self, event_type: str, data: dict[str, Any]):
        """发布事件到所有订阅者。"""
        payload = {"type": event_type, "data": data}

        handlers = self._handlers.get(event_type, [])
        for handler in handlers:
            try:
                result = handler(data)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception(f"[EventBus] handler 异常: {event_type}")

        if self._redis_available:
            try:
                await self._redis.publish(
                    f"apis:event:{event_type}",
                    json.dumps(payload, ensure_ascii=False, default=str),
                )
            except Exception:
                logger.warning("[EventBus] Redis 发布失败，降级为纯内存模式")
                self._redis_available = False
