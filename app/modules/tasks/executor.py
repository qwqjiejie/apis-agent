"""TaskExecutor — 完整后台任务执行引擎。

支持：
- submit / cancel / get_status / list_tasks
- HITL: interrupt(waiting_human) + resume
- 快照持久化（生产使用 PG Store，基础设施不可用时降级内存）
- 优雅关闭：drain → 等待 → 超时打快照 → cancel
- 审批兜底：连续 3 轮未处理审批标记强制中断
- Journal: 结构化执行日志
- DeadLetter: 关键操作失败重试
- EventBus: 状态变更事件广播
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

from app.modules.tasks.context import (
    MemoryTaskStore,
    TaskContextManager,
    TaskSnapshot,
    TaskStatus,
    TaskStore,
)
from app.modules.tasks.dead_letter import DeadLetterQueue
from app.modules.tasks.events import EventBus

logger = logging.getLogger("apis")

APPROVAL_MARKER = "[HUMAN_APPROVAL_REQUIRED]"
MAX_UNHANDLED_APPROVAL_ROUNDS = 3


class ApprovalNotHandledError(Exception):
    """LLM 连续多轮未处理审批标记时触发。"""
    def __init__(self, task_id: str, rounds: int, approval_id: str = ""):
        self.task_id = task_id
        self.rounds = rounds
        self.approval_id = approval_id
        super().__init__(f"任务 {task_id}: 连续 {rounds} 轮未处理审批标记，强制中断")


class TaskExecutor:
    """后台任务执行引擎。"""

    def __init__(
        self,
        store: TaskStore | None = None,
        event_bus_instance: EventBus | None = None,
        context_manager_instance: TaskContextManager | None = None,
        dead_letter_queue_instance: DeadLetterQueue | None = None,
        executor_agent=None,
    ):
        self._store = store if store is not None else MemoryTaskStore()
        self._event_bus = (
            event_bus_instance if event_bus_instance is not None else EventBus()
        )
        self._context_manager = (
            context_manager_instance
            if context_manager_instance is not None
            else TaskContextManager()
        )
        self._dead_letter_queue = (
            dead_letter_queue_instance
            if dead_letter_queue_instance is not None
            else DeadLetterQueue()
        )
        self.executor_agent = executor_agent
        self._running: dict[str, asyncio.Task] = {}
        self._live_snapshots: dict[str, TaskSnapshot] = {}
        self._draining: bool = False
        self._msg_counts: dict[str, int] = {}
        self._unhandled_approval: dict[str, int] = {}

    @property
    def context_manager(self) -> TaskContextManager:
        return self._context_manager

    @property
    def event_bus(self) -> EventBus:
        return self._event_bus

    @property
    def dead_letter_queue(self) -> DeadLetterQueue:
        return self._dead_letter_queue

    # ── 对外接口 ──────────────────────────────────

    async def submit(
        self,
        conversation_id: str,
        query: str,
        execute_fn,
        *,
        user_id: str = "",
    ) -> str:
        if self._draining:
            raise RuntimeError("任务执行器正在关闭，不接受新任务")

        task_id = f"task_{uuid.uuid4().hex[:12]}"
        snapshot = TaskSnapshot(
            task_id=task_id,
            conversation_id=conversation_id,
            query=query,
            goal=query,
            session_id=conversation_id,
            user_id=user_id or self._context_manager.get().user_id,
            status=TaskStatus.CREATED,
        )
        await self._save_snapshot(snapshot)
        await self._write_journal(task_id, "created", f"任务已创建: {query[:200]}")
        await self._event_bus.publish("task.created", snapshot.to_dict())

        async def _run():
            try:
                snapshot.status = TaskStatus.EXECUTING
                await self._save_snapshot(snapshot)
                await self._write_journal(task_id, "executing", "任务开始执行")
                await self._event_bus.publish("task.executing", snapshot.to_dict())
                await self._drive(snapshot, lambda: execute_fn(snapshot))
            finally:
                self._forget_runtime(task_id)

        task = asyncio.create_task(_run())
        self._running[task_id] = task
        self._live_snapshots[task_id] = snapshot
        snapshot._task_ref = task
        logger.info(f"[TaskExecutor] 任务已提交: {task_id}, query={query[:60]}")
        return task_id

    async def resume(
        self,
        task_id: str,
        resume_data: dict,
        *,
        user_id: str = "",
    ) -> bool:
        """恢复挂起的任务（HITL 审批完成）。"""
        action = resume_data.get("action", "approved")
        if action not in {"approved", "rejected"}:
            raise ValueError(f"不支持的审批动作: {action}")

        snapshot = await self._store.get(task_id)
        if (
            not snapshot
            or not self._is_owned_by(snapshot, user_id)
            or snapshot.status != TaskStatus.WAITING_HUMAN
        ):
            return False

        logger.info(f"[TaskExecutor] 恢复任务: {task_id}, action={action}")
        snapshot.status = TaskStatus.EXECUTING
        snapshot.interrupt_info = None
        snapshot.approval_id = ""
        snapshot.recovery_hint = ""
        snapshot.error = ""
        snapshot.error_message = ""
        await self._save_snapshot(snapshot)
        await self._write_journal(
            task_id,
            "decision",
            f"人工审批: {action}",
            {"action": action, "comment": resume_data.get("comment", "")},
        )
        await self._event_bus.publish("task.resumed", snapshot.to_dict())

        async def _resume():
            from app.agent.executor_agent import ExecutorAgent

            try:
                wrapper = ExecutorAgent(
                    snapshot,
                    executor_agent=self.executor_agent,
                    context_manager=self._context_manager,
                )
                await self._drive(snapshot, lambda: wrapper.resume(resume_data))
            finally:
                self._forget_runtime(task_id)

        task = asyncio.create_task(_resume())
        self._running[task_id] = task
        self._live_snapshots[task_id] = snapshot
        snapshot._task_ref = task
        self._unhandled_approval.pop(task_id, None)
        return True

    async def _drive(self, snapshot: TaskSnapshot, stream_factory) -> None:
        """消费首次执行或 checkpoint 续跑事件，并统一持久化生命周期。"""
        task_id = snapshot.task_id
        parts = [snapshot.result] if snapshot.result else []

        try:
            async for event in stream_factory():
                if snapshot.cancel_event.is_set():
                    snapshot.status = TaskStatus.CANCELLED
                    await self._persist_terminal(snapshot)
                    return

                if not isinstance(event, dict):
                    continue

                payload = self._event_payload(event)
                status_update = payload.get("_task_status")
                if status_update == TaskStatus.WAITING_HUMAN.value:
                    snapshot.status = TaskStatus.WAITING_HUMAN
                    snapshot.interrupt_info = (
                        payload.get("interrupt_info") or snapshot.interrupt_info
                    )
                    snapshot.approval_id = (
                        payload.get("approval_id") or snapshot.approval_id
                    )
                    self._update_result(snapshot, parts)
                    await self._save_snapshot(snapshot)
                    await self._write_journal(
                        task_id,
                        "approval_requested",
                        "任务等待人工审批",
                        {"approval_id": snapshot.approval_id},
                    )
                    await self._event_bus.publish("task.interrupted", snapshot.to_dict())
                    self._unhandled_approval.pop(task_id, None)
                    return

                if status_update:
                    try:
                        next_status = TaskStatus(status_update)
                        snapshot.status = (
                            TaskStatus.EXECUTING
                            if next_status == TaskStatus.RUNNING
                            else next_status
                        )
                    except ValueError:
                        logger.warning(
                            "[TaskExecutor] 忽略未知任务状态: %s (%s)",
                            status_update,
                            task_id,
                        )

                event_type = payload.get("type")
                content = str(payload.get("content", "") or "")
                if event_type == "error":
                    parts.append(f"\n[错误] {content}")
                    snapshot.error = content
                    snapshot.error_message = content
                    self._check_approval_marker(task_id, content)
                elif event_type == "text":
                    parts.append(content)
                    self._check_approval_marker(task_id, content)
                elif event_type == "thinking":
                    parts.append(f"\n[思考] {content}")

            self._update_result(snapshot, parts)
            if not snapshot.is_terminal:
                snapshot.status = TaskStatus.COMPLETED
            await self._persist_terminal(snapshot)

        except ApprovalNotHandledError as error:
            self._update_result(snapshot, parts)
            await self._force_approval_interrupt(snapshot, error)
        except asyncio.CancelledError:
            snapshot.status = TaskStatus.CANCELLED
            self._update_result(snapshot, parts)
            await self._persist_terminal(snapshot)
        except Exception as error:
            logger.error(f"[TaskExecutor] 任务 {task_id} 异常: {error}", exc_info=True)
            snapshot.status = TaskStatus.FAILED
            snapshot.error = str(error)
            snapshot.error_message = str(error)
            self._update_result(snapshot, parts)
            await self._persist_terminal(snapshot)

    async def _persist_terminal(self, snapshot: TaskSnapshot) -> None:
        snapshot.updated_at = datetime.now(timezone.utc).isoformat()
        await self._save_snapshot(snapshot)

        event_name = f"task.{snapshot.status.value}"
        description = {
            TaskStatus.COMPLETED: f"任务完成: {snapshot.result_summary[:200]}",
            TaskStatus.FAILED: f"任务失败: {snapshot.error_message[:200]}",
            TaskStatus.CANCELLED: "任务已取消",
        }.get(snapshot.status, f"任务状态变更: {snapshot.status.value}")
        await self._write_journal(snapshot.task_id, snapshot.status.value, description)
        if snapshot.status == TaskStatus.COMPLETED:
            logger.info(f"[TaskExecutor] 任务完成: {snapshot.task_id}")
        await self._event_bus.publish(event_name, snapshot.to_dict())

    @staticmethod
    def _update_result(snapshot: TaskSnapshot, parts: list[str]) -> None:
        snapshot.result = "".join(parts)
        snapshot.result_summary = snapshot.result[:500]

    def _forget_runtime(self, task_id: str) -> None:
        self._running.pop(task_id, None)
        self._live_snapshots.pop(task_id, None)
        self._msg_counts.pop(task_id, None)
        self._unhandled_approval.pop(task_id, None)

    async def cancel(self, task_id: str, *, user_id: str = "") -> bool:
        snapshot = await self._store.get(task_id)
        if not snapshot or not self._is_owned_by(snapshot, user_id):
            return False

        live_snapshot = self._live_snapshots.get(task_id)
        if live_snapshot:
            live_snapshot.cancel_event.set()
        running = self._running.get(task_id)
        if running and not running.done():
            running.cancel()
        else:
            snapshot.status = TaskStatus.CANCELLED
            await self._persist_terminal(snapshot)
        logger.info(f"[TaskExecutor] 任务已取消: {task_id}")
        return True

    async def get_status(self, task_id: str, *, user_id: str = "") -> dict | None:
        snapshot = await self._store.get(task_id)
        if not snapshot or not self._is_owned_by(snapshot, user_id):
            return None
        status = snapshot.status.value if isinstance(snapshot.status, TaskStatus) else str(snapshot.status)
        return {
            "taskId": snapshot.task_id,
            "conversationId": snapshot.conversation_id,
            "query": snapshot.query,
            "status": status,
            "result": snapshot.result[:2000] if snapshot.result else "",
            "resultSummary": snapshot.result_summary[:500] if snapshot.result_summary else "",
            "error": snapshot.error,
            "progress": snapshot.progress,
            "approvalId": snapshot.approval_id,
            "interruptInfo": snapshot.interrupt_info,
            "recoveryHint": snapshot.recovery_hint,
            "createdAt": snapshot.created_at,
            "updatedAt": snapshot.updated_at,
        }

    async def list_tasks(self, *, user_id: str = "") -> list[dict]:
        return [
            {
                "taskId": t.task_id,
                "conversationId": t.conversation_id,
                "query": t.query[:100],
                "status": t.status.value if isinstance(t.status, TaskStatus) else str(t.status),
                "createdAt": t.created_at,
            }
            for t in await self._store.list_tasks()
            if self._is_owned_by(t, user_id)
        ]

    async def list_tasks_by_session(
        self,
        session_id: str,
        *,
        user_id: str = "",
    ) -> list[dict]:
        """返回某会话的所有任务状态（供 /chat 注入已完成任务结果）。"""
        return [
            {
                "taskId": t.task_id,
                "conversationId": t.conversation_id,
                "query": t.query[:100],
                "status": t.status.value if isinstance(t.status, TaskStatus) else str(t.status),
                "result": (t.result or "")[:500],
                "resultSummary": (t.result_summary or "")[:200],
                "createdAt": t.created_at,
                "updatedAt": t.updated_at,
            }
            for t in await self._store.list_by_session(session_id)
            if self._is_owned_by(t, user_id)
        ]

    @staticmethod
    def _is_owned_by(snapshot: TaskSnapshot, user_id: str) -> bool:
        return not user_id or snapshot.user_id == user_id

    # ── 优雅关闭 ──────────────────────────────────

    async def drain(self):
        self._draining = True
        logger.info(f"[TaskExecutor] 进入排干模式，运行中任务={len(self._running)}")

    async def shutdown(self, timeout: float = 30.0):
        await self.drain()
        running = list(self._running.items())
        if not running:
            return

        logger.info(f"[TaskExecutor] 等待 {len(running)} 个运行中任务完成（{timeout}s）")
        done, pending = await asyncio.wait(
            [t for _, t in running], timeout=timeout,
        )

        for task_id, bg_task in running:
            if bg_task in pending:
                snapshot = self._live_snapshots.get(task_id) or await self._store.get(task_id)
                if snapshot and not snapshot.is_terminal:
                    logger.warning(f"[TaskExecutor] 任务 {task_id} 超时，保存快照后取消")
                    snapshot.recovery_hint = f"服务器关闭时保存 — 原状态: {snapshot.status.value if isinstance(snapshot.status, TaskStatus) else snapshot.status}"
                    await self._save_snapshot(snapshot)
                bg_task.cancel()
                try:
                    await bg_task
                except (asyncio.CancelledError, Exception):
                    pass

        logger.info("[TaskExecutor] 关闭完成")

    async def recover_tasks(self):
        """启动时恢复未完成的任务。

        - WAITING_HUMAN：保留挂起状态，等待人工 resume
        - EXECUTING/CREATED：服务重启时这些任务已丢失运行时句柄，标记为取消
        """
        recoverable = await self._store.list_by_status(TaskStatus.WAITING_HUMAN)
        recovered = 0
        for snapshot in recoverable:
            # 只注册句柄，等待人工 resume
            recovered += 1
            logger.info(f"[TaskExecutor] 恢复挂起任务: {snapshot.task_id}")

        executing = await self._store.list_by_status(TaskStatus.EXECUTING)
        executing.extend(await self._store.list_by_status(TaskStatus.CREATED))
        for snapshot in executing:
            snapshot.status = TaskStatus.CANCELLED
            snapshot.error = "服务重启，任务丢失（未实现完整快照恢复）"
            await self._save_snapshot(snapshot)
            logger.warning(f"[TaskExecutor] 标记丢失任务: {snapshot.task_id}")
            recovered += 1

        if recovered:
            logger.info(
                f"[TaskExecutor] 启动恢复: {recovered} 个任务已处理"
                f"（{len(recoverable)} 等待人审, {len(executing)} 标记丢失）"
            )
        return recovered

    # ── 审批兜底检测 ──────────────────────────────

    def _check_approval_marker(self, task_id: str, content: str):
        if APPROVAL_MARKER not in content:
            return
        current = self._unhandled_approval.get(task_id, 0) + 1
        self._unhandled_approval[task_id] = current
        logger.warning(f"[TaskExecutor] 审批标记第 {current} 轮未处理: {task_id}")
        if current >= MAX_UNHANDLED_APPROVAL_ROUNDS:
            raise ApprovalNotHandledError(task_id=task_id, rounds=current)

    async def _force_approval_interrupt(self, snapshot: TaskSnapshot, error: ApprovalNotHandledError):
        snapshot.status = TaskStatus.WAITING_HUMAN
        snapshot.interrupt_info = {
            "action_requests": [{
                "name": "request_approval",
                "description": f"系统兜底中断：连续 {error.rounds} 轮未处理审批标记",
            }],
            "_synthetic": True,
        }
        snapshot.recovery_hint = f"连续 {error.rounds} 轮未处理审批标记，已自动挂起"
        await self._save_snapshot(snapshot)
        await self._write_journal(snapshot.task_id, "approval_requested", snapshot.recovery_hint)
        await self._event_bus.publish("task.interrupted", snapshot.to_dict())
        logger.warning(f"[TaskExecutor] 审批兜底中断: {snapshot.task_id}")

    # ── Journal ──────────────────────────────────

    async def _write_journal(self, task_id: str, event: str, description: str, detail: dict | None = None):
        try:
            entry = await self._store.append_journal(
                task_id,
                event,
                description,
                detail,
            )
            logger.info(
                f"[Journal] {task_id} | step={entry.step} | {event}: {description[:200]}"
            )
        except Exception as exc:
            await self._dead_letter_queue.enqueue(
                "task_journal_append",
                {
                    "task_id": task_id,
                    "event": event,
                    "description": description,
                    "detail": detail or {},
                },
                error=str(exc),
            )
            logger.exception(f"[Journal] 写入失败，已进入死信: {task_id}")

    async def _save_snapshot(self, snapshot: TaskSnapshot) -> None:
        try:
            await self._store.save(snapshot)
        except Exception as exc:
            await self._dead_letter_queue.enqueue(
                "task_snapshot_save",
                {"snapshot": snapshot.to_dict()},
                error=str(exc),
            )
            logger.exception(
                f"[TaskExecutor] 快照写入失败，已进入死信: {snapshot.task_id}"
            )

    async def read_journal(
        self,
        task_id: str,
        *,
        user_id: str = "",
    ) -> list[dict]:
        snapshot = await self._store.get(task_id)
        if user_id and (
            snapshot is None or not self._is_owned_by(snapshot, user_id)
        ):
            return []
        return [entry.to_dict() for entry in await self._store.read_journal(task_id)]

    @staticmethod
    def _event_payload(event: dict) -> dict:
        """将 Executor 的 SSE 包装事件转换为 TaskExecutor 内部事件。"""
        if "type" in event or "_task_status" in event:
            return event
        data = event.get("data")
        if not isinstance(data, str):
            return event
        try:
            payload = json.loads(data)
            return payload if isinstance(payload, dict) else event
        except json.JSONDecodeError:
            return event
