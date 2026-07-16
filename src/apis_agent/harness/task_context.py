"""任务上下文模型 — TaskSnapshot, TaskStatus, JournalEntry, TaskStore。"""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class TaskStatus(str, Enum):
    CREATED = "created"
    PENDING = "pending"
    RUNNING = "running"
    EXECUTING = "executing"
    WAITING_HUMAN = "waiting_human"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskSnapshot:
    """任务执行快照 — 完整生命周期数据。

    包含任务标识、目标、状态、结果、HITL 中断信息。
    支持 snapshot_json 序列化到持久化存储（PG Store / 内存）。
    """
    task_id: str
    conversation_id: str = ""
    query: str = ""
    user_id: str = ""
    session_id: str = ""
    goal: str = ""
    status: TaskStatus = TaskStatus.PENDING
    plan: list[dict] = field(default_factory=list)
    progress: str = ""
    result: str = ""
    result_summary: str = ""
    error: str = ""
    error_message: str = ""
    approval_id: str = ""
    interrupt_info: dict | None = None
    recovery_hint: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # 运行时状态（不参与持久化）
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    _task_ref: asyncio.Task | None = field(default=None, repr=False)

    @property
    def is_terminal(self) -> bool:
        return self.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "conversation_id": self.conversation_id,
            "query": self.query,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "goal": self.goal,
            "status": self.status.value if isinstance(self.status, TaskStatus) else self.status,
            "plan": self.plan,
            "progress": self.progress,
            "result": self.result,
            "result_summary": self.result_summary,
            "error": self.error,
            "error_message": self.error_message,
            "approval_id": self.approval_id,
            "interrupt_info": self.interrupt_info,
            "recovery_hint": self.recovery_hint,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TaskSnapshot":
        status_raw = data.get("status", "pending")
        if isinstance(status_raw, TaskStatus):
            status = status_raw
        else:
            try:
                status = TaskStatus(status_raw)
            except ValueError:
                status = TaskStatus.PENDING
        return cls(
            task_id=data.get("task_id", ""),
            conversation_id=data.get("conversation_id", ""),
            query=data.get("query", ""),
            user_id=data.get("user_id", ""),
            session_id=data.get("session_id", ""),
            goal=data.get("goal", data.get("query", "")),
            status=status,
            plan=list(data.get("plan", [])),
            progress=data.get("progress", ""),
            result=data.get("result", ""),
            result_summary=data.get("result_summary", ""),
            error=data.get("error", ""),
            error_message=data.get("error_message", ""),
            approval_id=data.get("approval_id", ""),
            interrupt_info=data.get("interrupt_info"),
            recovery_hint=data.get("recovery_hint", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )


@dataclass
class JournalEntry:
    """任务执行日志中的一条记录。"""
    step: int
    event: str = ""           # specialist_result / approval_requested / decision / error / completed
    description: str = ""
    detail: dict | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "step": self.step,
            "event": self.event,
            "description": self.description,
            "detail": self.detail or {},
            "timestamp": self.timestamp,
        }


class TaskStore:
    """内存任务状态存储。生产环境可替换为 PG Store 实现。"""

    def __init__(self):
        self._tasks: dict[str, TaskSnapshot] = {}

    def save(self, snapshot: TaskSnapshot):
        snapshot.updated_at = datetime.now(timezone.utc).isoformat()
        self._tasks[snapshot.task_id] = snapshot

    def get(self, task_id: str) -> TaskSnapshot | None:
        return self._tasks.get(task_id)

    def list_tasks(self) -> list[TaskSnapshot]:
        return sorted(self._tasks.values(), key=lambda t: t.created_at, reverse=True)

    def delete(self, task_id: str):
        self._tasks.pop(task_id, None)

    def list_by_status(self, status: TaskStatus) -> list[TaskSnapshot]:
        return [t for t in self._tasks.values() if t.status == status]


task_store = TaskStore()
