"""DeadLetterQueue — 死信队列。

关键操作（审批写入、任务结果回写）失败时的可靠存储与重试机制。
当前实现为内存存储，后续可升级为 PG Store 持久化。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("apis")

RetryHandler = Callable[[], Any]


class DeadLetterQueue:
    """死信队列 — 失败操作的可靠存储与重试。"""

    def __init__(self):
        self._dead_letters: list[dict[str, Any]] = []
        self._retry_handlers: dict[str, RetryHandler] = {}
        self._scan_task: asyncio.Task | None = None

    def enqueue(self, operation_type: str, operation_args: dict[str, Any]):
        """将失败的操作写入死信队列。"""
        self._dead_letters.append({
            "operation_type": operation_type,
            "operation_args": operation_args,
        })
        logger.warning(f"[DeadLetter] 操作入队: {operation_type}, 队列长度={len(self._dead_letters)}")

    def register_retry_handler(self, operation_type: str, handler: RetryHandler):
        """注册操作的重试处理器。"""
        self._retry_handlers[operation_type] = handler

    async def retry_all(self) -> int:
        """尝试重试所有死信。返回成功重试的数量。"""
        success = 0
        remaining = []
        for item in self._dead_letters:
            op_type = item["operation_type"]
            handler = self._retry_handlers.get(op_type)
            if handler is None:
                remaining.append(item)
                continue
            try:
                await handler()
                success += 1
            except Exception as e:
                logger.warning(f"[DeadLetter] 重试失败: {op_type} → {e}")
                remaining.append(item)
        self._dead_letters = remaining
        if success > 0:
            logger.info(f"[DeadLetter] 重试完成: {success} 成功, {len(remaining)} 残留")
        return success

    async def start_scanner(self, interval_seconds: float = 120.0):
        """启动后台扫描器，定期重试死信。"""
        if self._scan_task is not None:
            return

        async def _loop():
            while True:
                await asyncio.sleep(interval_seconds)
                await self.retry_all()

        self._scan_task = asyncio.create_task(_loop())
        logger.info(f"[DeadLetter] 后台扫描器已启动 ({interval_seconds}s)")

    async def stop_scanner(self):
        if self._scan_task:
            self._scan_task.cancel()
            try:
                await self._scan_task
            except asyncio.CancelledError:
                pass
            self._scan_task = None

    @property
    def pending_count(self) -> int:
        return len(self._dead_letters)


dead_letter_queue = DeadLetterQueue()
