"""后台任务查询、流式状态、取消和恢复接口。"""

import json

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from app.api.routes.agent_schemas import TaskQueryRequest, TaskResumeRequest
from app.modules.identity.auth import get_current_user_id
from app.common.response import error, ok
from app.common.streaming import StreamEventType, make_sse
from app.modules.tasks.context import TaskStatus

router = APIRouter(tags=["agent-tasks"])


@router.post("/task/status")
async def task_status(req: TaskQueryRequest, request: Request):
    result = await request.app.state.container.task_executor.get_status(
        req.taskId,
        user_id=get_current_user_id(request),
    )
    if not result:
        return error(404, "任务不存在")
    return ok(result)


@router.post("/task/stream")
async def task_stream(req: TaskQueryRequest, request: Request):
    snapshot = await request.app.state.container.task_executor.get_status(
        req.taskId,
        user_id=get_current_user_id(request),
    )
    if not snapshot:
        async def _err():
            yield make_sse(json.dumps({
                "type": StreamEventType.ERROR,
                "content": "任务不存在",
            }, ensure_ascii=False))

        return EventSourceResponse(_err())

    async def event_generator():
        status = snapshot["status"]
        if status == TaskStatus.COMPLETED.value:
            yield make_sse(json.dumps({
                "type": StreamEventType.TEXT,
                "content": snapshot.get("result", ""),
            }, ensure_ascii=False))
        elif status == TaskStatus.FAILED.value:
            yield make_sse(json.dumps({
                "type": StreamEventType.ERROR,
                "content": snapshot.get("error", "执行失败"),
            }, ensure_ascii=False))
        elif status == TaskStatus.CANCELLED.value:
            yield make_sse(json.dumps({
                "type": StreamEventType.ERROR,
                "content": "任务已取消",
            }, ensure_ascii=False))
        elif status in (
            TaskStatus.CREATED.value,
            TaskStatus.PENDING.value,
            TaskStatus.RUNNING.value,
            TaskStatus.EXECUTING.value,
            TaskStatus.WAITING_HUMAN.value,
        ):
            yield make_sse(json.dumps({
                "type": StreamEventType.TASK_STATUS,
                "taskId": req.taskId,
                "status": status,
                "approvalId": snapshot.get("approvalId", ""),
                "interruptInfo": snapshot.get("interruptInfo"),
            }, ensure_ascii=False))
        yield make_sse(json.dumps({
            "type": StreamEventType.COMPLETE,
        }, ensure_ascii=False))
        yield make_sse("[DONE]")

    return EventSourceResponse(event_generator())


@router.post("/task/cancel")
async def task_cancel(req: TaskQueryRequest, request: Request):
    if await request.app.state.container.task_executor.cancel(
        req.taskId,
        user_id=get_current_user_id(request),
    ):
        return ok(None, message="已发送取消信号")
    return error(404, "任务不存在")


@router.post("/task/resume")
async def task_resume(req: TaskResumeRequest, request: Request):
    """恢复挂起的后台任务（HITL 审批完成后调用）。"""
    resumed = await request.app.state.container.task_executor.resume(
        req.taskId,
        {"action": req.action, "comment": req.comment},
        user_id=get_current_user_id(request),
    )
    if not resumed:
        return error(404, "任务不存在或非挂起状态")
    return ok(None, message=f"任务已恢复（{req.action}）")


@router.post("/task/list")
async def task_list(request: Request):
    tasks = await request.app.state.container.task_executor.list_tasks(
        user_id=get_current_user_id(request),
    )
    return ok(tasks)
