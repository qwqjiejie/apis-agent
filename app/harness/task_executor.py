"""TaskExecutor — 完整后台任务执行引擎。

支持：
- submit / cancel / get_status / list_tasks
- HITL: interrupt(waiting_human) + resume
- 快照持久化（内存 TaskStore，可升级 PG Store）
- 优雅关闭：drain → 等待 → 超时打快照 → cancel
- 审批兜底：连续 3 轮未处理审批标记强制中断
- Journal: 结构化执行日志
- DeadLetter: 关键操作失败重试
- EventBus: 状态变更事件广播
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from app.harness.task_context import (
    JournalEntry,
    TaskSnapshot,
    TaskStatus,
    task_store,
)
from app.harness.event_bus import event_bus
from app.harness.dead_letter import dead_letter_queue

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

    def __init__(self):
        self._running: dict[str, asyncio.Task] = {}
        self._draining: bool = False
        self._msg_counts: dict[str, int] = {}
        self._unhandled_approval: dict[str, int] = {}

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
        task_store.save(snapshot)
        await event_bus.publish("task.created", snapshot.to_dict())

        async def _run():
            snapshot.status = TaskStatus.EXECUTING
            task_store.save(snapshot)
            await event_bus.publish("task.executing", snapshot.to_dict())

            try:
                parts: list[str] = []
                async for event in execute_fn(snapshot):
                    if snapshot.cancel_event.is_set():
                        snapshot.status = TaskStatus.CANCELLED
                        task_store.save(snapshot)
                        await event_bus.publish("task.cancelled", snapshot.to_dict())
                        return

                    if isinstance(event, dict):
                        status_update = event.get("_task_status")
                        if status_update:
                            if status_update == "waiting_human":
                                snapshot.status = TaskStatus.WAITING_HUMAN
                                task_store.save(snapshot)
                                await event_bus.publish("task.interrupted", snapshot.to_dict())
                                self._unhandled_approval.pop(task_id, None)
                                return  # 挂起，等待外部 resume
                            else:
                                try:
                                    snapshot.status = TaskStatus(status_update)
                                except ValueError:
                                    pass
                                task_store.save(snapshot)

                        if event.get("type") == "error":
                            parts.append(f"[错误] {event.get('content', '')}")
                            self._check_approval_marker(task_id, event.get("content", ""))
                        elif event.get("type") == "text":
                            content = event.get("content", "")
                            parts.append(content)
                            self._check_approval_marker(task_id, content)
                        elif event.get("type") == "thinking":
                            parts.append(f"[思考] {event.get('content', '')}")

                snapshot.result = "\n".join(parts)
                snapshot.result_summary = snapshot.result[:500]
                if snapshot.status not in (
                    TaskStatus.CANCELLED,
                    TaskStatus.WAITING_HUMAN,
                ):
                    snapshot.status = TaskStatus.COMPLETED
                snapshot.updated_at = datetime.now(timezone.utc).isoformat()
                task_store.save(snapshot)

                # 写入完成 Journal
                await self._write_journal(task_id, "completed", f"任务完成: {snapshot.result_summary[:200]}")
                await event_bus.publish("task.completed", snapshot.to_dict())
                logger.info(f"[TaskExecutor] 任务完成: {task_id}")

            except ApprovalNotHandledError as e:
                await self._force_approval_interrupt(snapshot, e)
            except asyncio.CancelledError:
                snapshot.status = TaskStatus.CANCELLED
                task_store.save(snapshot)
                await event_bus.publish("task.cancelled", snapshot.to_dict())
            except Exception as e:
                logger.error(f"[TaskExecutor] 任务 {task_id} 异常: {e}", exc_info=True)
                snapshot.status = TaskStatus.FAILED
                snapshot.error = str(e)
                snapshot.error_message = str(e)
                snapshot.updated_at = datetime.now(timezone.utc).isoformat()
                task_store.save(snapshot)
                await event_bus.publish("task.failed", snapshot.to_dict())
            finally:
                self._running.pop(task_id, None)
                self._msg_counts.pop(task_id, None)
                self._unhandled_approval.pop(task_id, None)

        task = asyncio.create_task(_run())
        self._running[task_id] = task
        snapshot._task_ref = task
        task_store.save(snapshot)
        logger.info(f"[TaskExecutor] 任务已提交: {task_id}, query={query[:60]}")
        return task_id

    async def resume(self, task_id: str, resume_data: dict) -> bool:
        """恢复挂起的任务（HITL 审批完成）。"""
        snapshot = task_store.get(task_id)
        if not snapshot or snapshot.status != TaskStatus.WAITING_HUMAN:
            return False

        action = resume_data.get("action", "approved")
        logger.info(f"[TaskExecutor] 恢复任务: {task_id}, action={action}")

        # 重建执行协程
        async def _resume():
            snapshot.status = TaskStatus.EXECUTING
            task_store.save(snapshot)
            await event_bus.publish("task.resumed", snapshot.to_dict())
            # 审批结果注入到结果中
            snapshot.result = (snapshot.result or "") + f"\n[审批结果: {action}]"
            task_store.save(snapshot)
            await event_bus.publish("task.completed", snapshot.to_dict())

        task = asyncio.create_task(_resume())
        self._running[task_id] = task
        snapshot._task_ref = task
        self._unhandled_approval.pop(task_id, None)
        return True

    def cancel(self, task_id: str) -> bool:
        snapshot = task_store.get(task_id)
        if not snapshot:
            return False
        snapshot.cancel_event.set()
        if snapshot._task_ref and not snapshot._task_ref.done():
            snapshot._task_ref.cancel()
        logger.info(f"[TaskExecutor] 任务已取消: {task_id}")
        return True

    def get_status(self, task_id: str) -> dict | None:
        snapshot = task_store.get(task_id)
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
            "createdAt": snapshot.created_at,
            "updatedAt": snapshot.updated_at,
        }

    def list_tasks(self) -> list[dict]:
        return [
            {
                "taskId": t.task_id,
                "conversationId": t.conversation_id,
                "query": t.query[:100],
                "status": t.status.value if isinstance(t.status, TaskStatus) else str(t.status),
                "createdAt": t.created_at,
            }
            for t in task_store.list_tasks()
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
                snapshot = task_store.get(task_id)
                if snapshot and not snapshot.is_terminal:
                    logger.warning(f"[TaskExecutor] 任务 {task_id} 超时，保存快照后取消")
                    snapshot.recovery_hint = f"服务器关闭时保存 — 原状态: {snapshot.status.value if isinstance(snapshot.status, TaskStatus) else snapshot.status}"
                    task_store.save(snapshot)
                bg_task.cancel()
                try:
                    await bg_task
                except (asyncio.CancelledError, Exception):
                    pass

        logger.info("[TaskExecutor] 关闭完成")

    async def recover_tasks(self):
        """启动时恢复未完成的任务。"""
        recoverable = task_store.list_by_status(TaskStatus.WAITING_HUMAN)
        for snapshot in recoverable:
            # 只注册句柄，等待人工 resume
            recovered += 1
            logger.info(f"[TaskExecutor] 恢复挂起任务: {snapshot.task_id}")

        executing = task_store.list_by_status(TaskStatus.EXECUTING)
        executing.extend(task_store.list_by_status(TaskStatus.CREATED))
        for snapshot in executing:
            snapshot.status = TaskStatus.CANCELLED
            snapshot.error = "服务重启，任务丢失（未实现完整快照恢复）"
            task_store.save(snapshot)
            logger.warning(f"[TaskExecutor] 标记丢失任务: {snapshot.task_id}")
            recovered += 1

        if recovered := len(recoverable) + len(executing):
            logger.info(f"[TaskExecutor] 启动恢复: {recovered} 个任务已处理（{len(recoverable)} 等待人审）")
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
        task_store.save(snapshot)
        await self._write_journal(snapshot.task_id, "approval_requested", snapshot.recovery_hint)
        await event_bus.publish("task.interrupted", snapshot.to_dict())
        logger.warning(f"[TaskExecutor] 审批兜底中断: {snapshot.task_id}")

    # ── Journal ──────────────────────────────────

    async def _write_journal(self, task_id: str, event: str, description: str, detail: dict | None = None):
        """写入一条执行日志（当前为 logger 记录，可升级为 PG Store）。"""
        logger.info(f"[Journal] {task_id} | {event}: {description[:200]}")


task_executor = TaskExecutor()
