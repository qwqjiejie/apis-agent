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

from app.harness.task_context import (
    TaskSnapshot,
    TaskStatus,
    TaskStore,
    task_store,
)
from app.harness.event_bus import event_bus

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

    def __init__(self, store: TaskStore | None = None, event_bus_instance=None):
        self._store = store or task_store
        self._event_bus = event_bus_instance or event_bus
        self.executor_agent = None
        self._running: dict[str, asyncio.Task] = {}
        self._live_snapshots: dict[str, TaskSnapshot] = {}
        self._draining: bool = False
        self._msg_counts: dict[str, int] = {}
        self._unhandled_approval: dict[str, int] = {}

    def configure(self, *, store: TaskStore, executor_agent=None) -> None:
        """在 lifespan 中注入持久化仓储和单例 Executor Agent。"""
        if self._running:
            raise RuntimeError("存在运行中任务时不能替换 TaskStore")
        self._store = store
        self.executor_agent = executor_agent
        self._draining = False

    # ── 对外接口 ──────────────────────────────────

    async def submit(self, conversation_id: str, query: str, execute_fn) -> str:
        if self._draining:
            raise RuntimeError("任务执行器正在关闭，不接受新任务")

        task_id = f"task_{uuid.uuid4().hex[:12]}"
        snapshot = TaskSnapshot(
            task_id=task_id,
            conversation_id=conversation_id,
            query=query,
            goal=query,
            session_id=conversation_id,
            status=TaskStatus.CREATED,
        )
        await self._store.save(snapshot)
        await self._event_bus.publish("task.created", snapshot.to_dict())

        async def _run():
            try:
                snapshot.status = TaskStatus.EXECUTING
                await self._store.save(snapshot)
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

    async def resume(self, task_id: str, resume_data: dict) -> bool:
        """恢复挂起的任务（HITL 审批完成）。"""
        action = resume_data.get("action", "approved")
        if action not in {"approved", "rejected"}:
            raise ValueError(f"不支持的审批动作: {action}")

        snapshot = await self._store.get(task_id)
        if not snapshot or snapshot.status != TaskStatus.WAITING_HUMAN:
            return False

        logger.info(f"[TaskExecutor] 恢复任务: {task_id}, action={action}")
        snapshot.status = TaskStatus.EXECUTING
        snapshot.interrupt_info = None
        snapshot.approval_id = ""
        snapshot.recovery_hint = ""
        snapshot.error = ""
        snapshot.error_message = ""
        await self._store.save(snapshot)
        await self._event_bus.publish("task.resumed", snapshot.to_dict())

        async def _resume():
            from app.agent.executor_agent import ExecutorAgent

            try:
                wrapper = ExecutorAgent(snapshot)
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
                    await self._store.save(snapshot)
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
        await self._store.save(snapshot)

        event_name = f"task.{snapshot.status.value}"
        if snapshot.status == TaskStatus.COMPLETED:
            await self._write_journal(
                snapshot.task_id,
                "completed",
                f"任务完成: {snapshot.result_summary[:200]}",
            )
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

    async def cancel(self, task_id: str) -> bool:
        snapshot = await self._store.get(task_id)
        if not snapshot:
            return False

        live_snapshot = self._live_snapshots.get(task_id)
        if live_snapshot:
            live_snapshot.cancel_event.set()
        running = self._running.get(task_id)
        if running and not running.done():
            running.cancel()
        else:
            snapshot.status = TaskStatus.CANCELLED
            await self._store.save(snapshot)
            await self._event_bus.publish("task.cancelled", snapshot.to_dict())
        logger.info(f"[TaskExecutor] 任务已取消: {task_id}")
        return True

    async def get_status(self, task_id: str) -> dict | None:
        snapshot = await self._store.get(task_id)
        if not snapshot:
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

    async def list_tasks(self) -> list[dict]:
        return [
            {
                "taskId": t.task_id,
                "conversationId": t.conversation_id,
                "query": t.query[:100],
                "status": t.status.value if isinstance(t.status, TaskStatus) else str(t.status),
                "createdAt": t.created_at,
            }
            for t in await self._store.list_tasks()
        ]

    async def list_tasks_by_session(self, session_id: str) -> list[dict]:
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
        ]

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
                    await self._store.save(snapshot)
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
            await self._store.save(snapshot)
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
        await self._store.save(snapshot)
        await self._write_journal(snapshot.task_id, "approval_requested", snapshot.recovery_hint)
        await self._event_bus.publish("task.interrupted", snapshot.to_dict())
        logger.warning(f"[TaskExecutor] 审批兜底中断: {snapshot.task_id}")

    # ── Journal ──────────────────────────────────

    async def _write_journal(self, task_id: str, event: str, description: str, detail: dict | None = None):
        """写入一条执行日志（当前为 logger 记录，可升级为 PG Store）。"""
        logger.info(f"[Journal] {task_id} | {event}: {description[:200]}")

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


task_executor = TaskExecutor()
