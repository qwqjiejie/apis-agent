"""兼容导出；新代码使用 :mod:`app.modules.tasks.dead_letter`。"""

from app.modules.tasks.dead_letter import (
    DEAD_LETTER_LIMIT,
    DEAD_LETTER_NAMESPACE,
    DeadLetterQueue,
    RetryHandler,
)

__all__ = [
    "DEAD_LETTER_LIMIT",
    "DEAD_LETTER_NAMESPACE",
    "DeadLetterQueue",
    "RetryHandler",
]
