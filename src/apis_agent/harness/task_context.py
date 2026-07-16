import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_HUMAN = "waiting_human"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskSnapshot:
    task_id: str
    conversation_id: str
    query: str
    status: TaskStatus = TaskStatus.PENDING
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    result: str = ""
    error: str = ""

    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    _task_ref: asyncio.Task | None = field(default=None, repr=False)


class TaskStore:
    """内存任务状态存储。"""

    def __init__(self):
        self._tasks: dict[str, TaskSnapshot] = {}

    def save(self, snapshot: TaskSnapshot):
        snapshot.updated_at = time.time()
        self._tasks[snapshot.task_id] = snapshot

    def get(self, task_id: str) -> TaskSnapshot | None:
        return self._tasks.get(task_id)

    def get_by_conversation(self, conversation_id: str) -> TaskSnapshot | None:
        for t in self._tasks.values():
            if t.conversation_id == conversation_id:
                return t
        return None

    def list_tasks(self) -> list[TaskSnapshot]:
        return sorted(self._tasks.values(), key=lambda t: t.created_at, reverse=True)

    def delete(self, task_id: str):
        self._tasks.pop(task_id, None)


task_store = TaskStore()
