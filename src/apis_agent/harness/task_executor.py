import asyncio
import json
import logging
import uuid

from src.apis_agent.harness.task_context import TaskSnapshot, TaskStatus, task_store

logger = logging.getLogger("apis")


class TaskExecutor:
    """后台任务执行引擎。

    管理 Agent 后台任务的完整生命周期：
    submit → execute → (interrupt/resume) → complete/fail

    使用 asyncio.Task 驱动执行，支持：
    - 状态查询：任意时刻可查询任务状态
    - 取消：通过 cancel_event 中断执行
    - 结果获取：执行完成后获取最终结果
    """

    def __init__(self):
        self._running: dict[str, asyncio.Task] = {}

    async def submit(self, conversation_id: str, query: str, execute_fn) -> str:
        """提交后台任务，返回 task_id。

        execute_fn(task_snapshot) 应是一个 async generator，yield SSE dict。
        执行结果通过 task_snapshot.result 获取。
        """
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        snapshot = TaskSnapshot(
            task_id=task_id,
            conversation_id=conversation_id,
            query=query,
            status=TaskStatus.PENDING,
        )
        task_store.save(snapshot)

        async def _run():
            snapshot.status = TaskStatus.RUNNING
            task_store.save(snapshot)
            try:
                parts: list[str] = []
                async for event in execute_fn(snapshot):
                    if snapshot.cancel_event.is_set():
                        snapshot.status = TaskStatus.CANCELLED
                        task_store.save(snapshot)
                        return
                    if isinstance(event, dict):
                        if event.get("_task_status"):
                            snapshot.status = TaskStatus(event["_task_status"])
                            task_store.save(snapshot)
                        elif event.get("type") == "error":
                            parts.append(f"[错误] {event.get('content', '')}")
                        elif event.get("type") == "text":
                            parts.append(event.get("content", ""))
                        elif event.get("type") == "thinking":
                            parts.append(f"[思考] {event.get('content', '')}")
                snapshot.result = "\n".join(parts)
                if snapshot.status not in (TaskStatus.CANCELLED, TaskStatus.WAITING_HUMAN):
                    snapshot.status = TaskStatus.COMPLETED
                task_store.save(snapshot)
            except asyncio.CancelledError:
                snapshot.status = TaskStatus.CANCELLED
                task_store.save(snapshot)
            except Exception as e:
                logger.error(f"[TaskExecutor] 任务 {task_id} 异常: {e}")
                snapshot.status = TaskStatus.FAILED
                snapshot.error = str(e)
                task_store.save(snapshot)
            finally:
                self._running.pop(task_id, None)

        task = asyncio.create_task(_run())
        self._running[task_id] = task
        snapshot._task_ref = task
        task_store.save(snapshot)
        logger.info(f"[TaskExecutor] 任务已提交: {task_id}, query={query[:50]}")
        return task_id

    def cancel(self, task_id: str) -> bool:
        """取消后台任务。"""
        snapshot = task_store.get(task_id)
        if not snapshot:
            return False
        snapshot.cancel_event.set()
        if snapshot._task_ref and not snapshot._task_ref.done():
            snapshot._task_ref.cancel()
        logger.info(f"[TaskExecutor] 任务已取消: {task_id}")
        return True

    def get_status(self, task_id: str) -> dict | None:
        """查询任务状态。"""
        snapshot = task_store.get(task_id)
        if not snapshot:
            return None
        return {
            "taskId": snapshot.task_id,
            "conversationId": snapshot.conversation_id,
            "query": snapshot.query,
            "status": snapshot.status.value,
            "result": snapshot.result[:2000] if snapshot.result else "",
            "error": snapshot.error,
            "createdAt": snapshot.created_at,
        }

    def list_tasks(self) -> list[dict]:
        """列出所有任务。"""
        return [
            {
                "taskId": t.task_id,
                "conversationId": t.conversation_id,
                "query": t.query[:100],
                "status": t.status.value,
            }
            for t in task_store.list_tasks()
        ]


task_executor = TaskExecutor()
