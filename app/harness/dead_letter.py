"""持久化死信队列，用于重试任务快照和 Journal 等关键写操作。"""

from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger("apis")

RetryHandler = Callable[[dict[str, Any]], Any]
DEAD_LETTER_NAMESPACE = ("dead_letters",)
DEAD_LETTER_LIMIT = 1000


class DeadLetterQueue:
    """PG 优先、内存兜底的失败操作队列。"""

    def __init__(self, store=None):
        self._store = store
        self._memory: dict[str, dict[str, Any]] = {}
        self._retry_handlers: dict[str, RetryHandler] = {}
        self._scan_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    def configure(self, store=None) -> None:
        self._store = store

    async def enqueue(
        self,
        operation_type: str,
        operation_args: dict[str, Any],
        *,
        error: str = "",
    ) -> str:
        record_id = f"dlq_{uuid.uuid4().hex}"
        record = {
            "id": record_id,
            "operation_type": operation_type,
            "operation_args": operation_args,
            "attempts": 0,
            "last_error": error,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        await self._save(record)
        logger.warning(f"[DeadLetter] 操作入队: {operation_type}, id={record_id}")
        return record_id

    def register_retry_handler(self, operation_type: str, handler: RetryHandler):
        self._retry_handlers[operation_type] = handler

    async def list_pending(self) -> list[dict[str, Any]]:
        records: dict[str, dict[str, Any]] = {}
        if self._store is not None:
            try:
                items = await self._store.asearch(
                    DEAD_LETTER_NAMESPACE,
                    limit=DEAD_LETTER_LIMIT,
                )
                for item in items:
                    if isinstance(item.value, dict) and item.value.get("id"):
                        records[item.value["id"]] = dict(item.value)
            except Exception as exc:
                logger.warning(f"[DeadLetter] PG 读取失败，使用内存兜底: {exc}")
        async with self._lock:
            records.update({key: dict(value) for key, value in self._memory.items()})
        return sorted(records.values(), key=lambda item: item.get("created_at", ""))

    async def retry_all(self) -> int:
        success = 0
        for record in await self.list_pending():
            handler = self._retry_handlers.get(record["operation_type"])
            if handler is None:
                continue
            try:
                result = handler(dict(record.get("operation_args", {})))
                if inspect.isawaitable(result):
                    await result
                await self._delete(record["id"])
                success += 1
            except Exception as exc:
                record["attempts"] = int(record.get("attempts", 0)) + 1
                record["last_error"] = str(exc)
                record["updated_at"] = datetime.now(timezone.utc).isoformat()
                await self._save(record)
                logger.warning(
                    f"[DeadLetter] 重试失败: {record['operation_type']} -> {exc}"
                )
        if success:
            logger.info(f"[DeadLetter] 重试完成: {success} 成功")
        return success

    async def start_scanner(self, interval_seconds: float = 120.0):
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

    async def _save(self, record: dict[str, Any]) -> None:
        if self._store is not None:
            try:
                await self._store.aput(
                    DEAD_LETTER_NAMESPACE,
                    record["id"],
                    record,
                    index=False,
                )
                async with self._lock:
                    self._memory.pop(record["id"], None)
                return
            except Exception as exc:
                logger.warning(f"[DeadLetter] PG 写入失败，转内存兜底: {exc}")
        async with self._lock:
            self._memory[record["id"]] = dict(record)

    async def _delete(self, record_id: str) -> None:
        if self._store is not None:
            try:
                await self._store.adelete(DEAD_LETTER_NAMESPACE, record_id)
            except Exception as exc:
                logger.warning(f"[DeadLetter] PG 删除失败: {exc}")
                return
        async with self._lock:
            self._memory.pop(record_id, None)

    @property
    def pending_count(self) -> int:
        """内存兜底中的待处理数量；完整数量使用 list_pending。"""
        return len(self._memory)


dead_letter_queue = DeadLetterQueue()
