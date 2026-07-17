"""外部基础设施适配器共享的有限重试策略。"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class HealthCheckResult:
    service: str
    available: bool
    detail: str = ""


def retry_sync(
    operation: Callable[[], T],
    *,
    attempts: int,
    delay_seconds: float = 0.05,
) -> T:
    last_error: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            return operation()
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(delay_seconds * (attempt + 1))
    assert last_error is not None
    raise last_error


async def retry_async(
    operation: Callable[[], Awaitable[T]],
    *,
    attempts: int,
    delay_seconds: float = 0.05,
) -> T:
    last_error: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            return await operation()
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                await asyncio.sleep(delay_seconds * (attempt + 1))
    assert last_error is not None
    raise last_error
