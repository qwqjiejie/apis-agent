"""兼容导出；新代码使用 :mod:`app.modules.tasks.executor`。"""

from app.modules.tasks.executor import (
    APPROVAL_MARKER,
    MAX_UNHANDLED_APPROVAL_ROUNDS,
    ApprovalNotHandledError,
    TaskExecutor,
)

__all__ = [
    "APPROVAL_MARKER",
    "MAX_UNHANDLED_APPROVAL_ROUNDS",
    "ApprovalNotHandledError",
    "TaskExecutor",
]
