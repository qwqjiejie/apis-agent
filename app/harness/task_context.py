"""任务上下文模型 — TaskSnapshot, TaskStatus, JournalEntry, TaskStore。"""

import asyncio
import uuid
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol


@dataclass(frozen=True)
class ChatContext:
    """贯穿 HTTP、Agent、工具和后台任务的身份上下文。"""

    user_id: str = ""
    session_id: str = ""
    task_id: str = ""
    trace_id: str = ""

    def configurable(self) -> dict[str, str]:
        return {
            "thread_id": self.session_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
        }


class TaskContextManager:
    """基于 ContextVar 的并发安全请求/任务上下文管理器。"""

    def __init__(self):
        self._current: ContextVar[ChatContext] = ContextVar(
            "apis_task_context",
            default=ChatContext(),
        )

    def get(self) -> ChatContext:
        return self._current.get()

    def set(self, context: ChatContext) -> Token[ChatContext]:
        return self._current.set(context)

    def reset(self, token: Token[ChatContext]) -> None:
        self._current.reset(token)

    @contextmanager
    def bind(self, context: ChatContext):
        token = self.set(context)
        try:
            yield context
        finally:
            self.reset(token)
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

    @classmethod
    def from_dict(cls, data: dict) -> "JournalEntry":
        return cls(
            step=int(data.get("step", 0)),
            event=str(data.get("event", "")),
            description=str(data.get("description", "")),
            detail=dict(data.get("detail", {}) or {}),
            timestamp=str(data.get("timestamp", "")),
        )


TASK_NAMESPACE = ("tasks",)
JOURNAL_NAMESPACE = ("task_journals",)
TASK_SEARCH_LIMIT = 1000


class TaskStore(Protocol):
    """任务快照仓储协议。"""

    async def save(self, snapshot: TaskSnapshot) -> None: ...

    async def get(self, task_id: str) -> TaskSnapshot | None: ...

    async def list_tasks(self) -> list[TaskSnapshot]: ...

    async def list_by_status(self, status: TaskStatus) -> list[TaskSnapshot]: ...

    async def list_by_session(self, session_id: str) -> list[TaskSnapshot]: ...

    async def append_journal(
        self,
        task_id: str,
        event: str,
        description: str,
        detail: dict | None = None,
    ) -> JournalEntry: ...

    async def read_journal(self, task_id: str) -> list[JournalEntry]: ...

    async def delete(self, task_id: str) -> None: ...


class MemoryTaskStore:
    """PG 不可用时使用的进程内降级仓储。"""

    def __init__(self):
        self._tasks: dict[str, TaskSnapshot] = {}
        self._journals: dict[str, list[JournalEntry]] = {}
        self._lock = asyncio.Lock()

    async def save(self, snapshot: TaskSnapshot) -> None:
        snapshot.updated_at = datetime.now(timezone.utc).isoformat()
        async with self._lock:
            self._tasks[snapshot.task_id] = TaskSnapshot.from_dict(snapshot.to_dict())

    async def get(self, task_id: str) -> TaskSnapshot | None:
        async with self._lock:
            snapshot = self._tasks.get(task_id)
            return TaskSnapshot.from_dict(snapshot.to_dict()) if snapshot else None

    async def list_tasks(self) -> list[TaskSnapshot]:
        async with self._lock:
            snapshots = [TaskSnapshot.from_dict(t.to_dict()) for t in self._tasks.values()]
        return sorted(snapshots, key=lambda t: t.created_at, reverse=True)

    async def delete(self, task_id: str) -> None:
        async with self._lock:
            self._tasks.pop(task_id, None)
            self._journals.pop(task_id, None)

    async def list_by_status(self, status: TaskStatus) -> list[TaskSnapshot]:
        return [t for t in await self.list_tasks() if t.status == status]

    async def list_by_session(self, session_id: str) -> list[TaskSnapshot]:
        return [t for t in await self.list_tasks() if t.conversation_id == session_id]

    async def append_journal(
        self,
        task_id: str,
        event: str,
        description: str,
        detail: dict | None = None,
    ) -> JournalEntry:
        async with self._lock:
            entries = self._journals.setdefault(task_id, [])
            entry = JournalEntry(
                step=len(entries) + 1,
                event=event,
                description=description,
                detail=detail,
            )
            entries.append(entry)
            return JournalEntry.from_dict(entry.to_dict())

    async def read_journal(self, task_id: str) -> list[JournalEntry]:
        async with self._lock:
            return [
                JournalEntry.from_dict(entry.to_dict())
                for entry in self._journals.get(task_id, [])
            ]


class PgTaskStore:
    """基于 LangGraph AsyncPostgresStore 的持久化任务仓储。"""

    def __init__(self, store: Any):
        if store is None:
            raise ValueError("AsyncPostgresStore 不能为空")
        self._store = store

    @staticmethod
    def _value(snapshot: TaskSnapshot) -> dict[str, Any]:
        data = snapshot.to_dict()
        return {
            "snapshot": data,
            "status": data["status"],
            "conversation_id": data["conversation_id"],
            "created_at": data["created_at"],
            "updated_at": data["updated_at"],
        }

    @staticmethod
    def _snapshot(value: dict[str, Any] | None) -> TaskSnapshot | None:
        if not value:
            return None
        data = value.get("snapshot")
        if not isinstance(data, dict):
            return None
        return TaskSnapshot.from_dict(data)

    async def save(self, snapshot: TaskSnapshot) -> None:
        snapshot.updated_at = datetime.now(timezone.utc).isoformat()
        await self._store.aput(
            TASK_NAMESPACE,
            snapshot.task_id,
            self._value(snapshot),
            index=False,
        )

    async def get(self, task_id: str) -> TaskSnapshot | None:
        item = await self._store.aget(TASK_NAMESPACE, task_id)
        return self._snapshot(item.value) if item else None

    async def list_tasks(self) -> list[TaskSnapshot]:
        items = await self._store.asearch(TASK_NAMESPACE, limit=TASK_SEARCH_LIMIT)
        snapshots = [self._snapshot(item.value) for item in items]
        return sorted(
            [snapshot for snapshot in snapshots if snapshot is not None],
            key=lambda task: task.created_at,
            reverse=True,
        )

    async def list_by_status(self, status: TaskStatus) -> list[TaskSnapshot]:
        items = await self._store.asearch(
            TASK_NAMESPACE,
            filter={"status": status.value},
            limit=TASK_SEARCH_LIMIT,
        )
        return [
            snapshot
            for item in items
            if (snapshot := self._snapshot(item.value)) is not None
        ]

    async def list_by_session(self, session_id: str) -> list[TaskSnapshot]:
        items = await self._store.asearch(
            TASK_NAMESPACE,
            filter={"conversation_id": session_id},
            limit=TASK_SEARCH_LIMIT,
        )
        snapshots = [self._snapshot(item.value) for item in items]
        return sorted(
            [snapshot for snapshot in snapshots if snapshot is not None],
            key=lambda task: task.created_at,
            reverse=True,
        )

    async def append_journal(
        self,
        task_id: str,
        event: str,
        description: str,
        detail: dict | None = None,
    ) -> JournalEntry:
        entries = await self.read_journal(task_id)
        entry = JournalEntry(
            step=len(entries) + 1,
            event=event,
            description=description,
            detail=detail,
        )
        key = f"{entry.timestamp}_{uuid.uuid4().hex}"
        await self._store.aput(
            JOURNAL_NAMESPACE + (task_id,),
            key,
            entry.to_dict(),
            index=False,
        )
        return entry

    async def read_journal(self, task_id: str) -> list[JournalEntry]:
        items = await self._store.asearch(
            JOURNAL_NAMESPACE + (task_id,),
            limit=TASK_SEARCH_LIMIT,
        )
        entries = [
            JournalEntry.from_dict(item.value)
            for item in items
            if isinstance(item.value, dict)
        ]
        return sorted(entries, key=lambda entry: (entry.step, entry.timestamp))

    async def delete(self, task_id: str) -> None:
        await self._store.adelete(TASK_NAMESPACE, task_id)
        items = await self._store.asearch(
            JOURNAL_NAMESPACE + (task_id,),
            limit=TASK_SEARCH_LIMIT,
        )
        for item in items:
            key = getattr(item, "key", "")
            if key:
                await self._store.adelete(JOURNAL_NAMESPACE + (task_id,), key)
