import json
import os

from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from src.apis_agent.agent.base_agent import BaseAgent
from src.apis_agent.agent.chat_agent import ChatAgent
from src.apis_agent.common.exceptions import QueryTooLongError, ValidationError
from src.apis_agent.common.logger import logger
from src.apis_agent.common.redis import publish_stop
from src.apis_agent.common.response import error, ok
from src.apis_agent.common.streaming import make_sse
from src.apis_agent.config.settings import get_settings
from src.apis_agent.tool.bash_tool import resolve_confirmation

router = APIRouter(prefix="/agent", tags=["agent"])


class StreamRequest(BaseModel):
    query: str = Field(..., min_length=1)
    conversationId: str = Field(..., min_length=1)
    fileId: str = ""


class StopRequest(BaseModel):
    conversationId: str = Field(..., min_length=1)


class ShellConfirmRequest(BaseModel):
    confirmId: str = Field(..., min_length=1)
    approved: bool


def _check_query_length(query: str):
    if len(query) > get_settings().max_query_length:
        raise QueryTooLongError(get_settings().max_query_length)


@router.post("/chat/stream")
async def agent_chat_stream(req: StreamRequest):
    _check_query_length(req.query)

    async def event_generator():
        agent = ChatAgent(req.conversationId, req.query, req.fileId, agent_type="chat")
        async for payload in agent.run():
            yield payload

    return EventSourceResponse(event_generator())


@router.post("/file/stream")
async def agent_file_stream(req: StreamRequest):
    _check_query_length(req.query)

    async def event_generator():
        agent = ChatAgent(req.conversationId, req.query, req.fileId, agent_type="chat")
        async for payload in agent.run():
            yield payload

    return EventSourceResponse(event_generator())


@router.post("/pptx/stream")
async def agent_pptx_stream(req: StreamRequest):
    _check_query_length(req.query)

    async def event_generator():
        from src.apis_agent.agent.ppt_builder_agent import PptBuilderAgent
        agent = PptBuilderAgent(req.conversationId, req.query)
        async for payload in agent.run():
            yield payload

    return EventSourceResponse(event_generator())


@router.post("/pptx/download")
async def agent_pptx_download(req: StopRequest):
    from minio import Minio
    from minio.error import S3Error

    from src.apis_agent.storage.models.ai_ppt_inst import PptInstRepo

    repo = PptInstRepo()
    inst = repo.find_by_conversation_id(req.conversationId)
    if not inst or not inst.file_url:
        return error(404, "PPT文件不存在")

    file_url = inst.file_url

    if file_url.startswith("local://"):
        local_path = file_url[len("local://"):]
        if not os.path.exists(local_path):
            return error(404, "文件已被清理")
        return FileResponse(local_path, filename=os.path.basename(local_path),
                            media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")

    if file_url.startswith("minio://"):
        bucket_obj = file_url[len("minio://"):]
        bucket, obj_name = bucket_obj.split("/", 1)
        try:
            client = Minio(
                f"{get_settings().minio_host}:{get_settings().minio_port}",
                access_key=get_settings().minio_access_key,
                secret_key=get_settings().minio_secret_key,
                secure=False,
            )
            from io import BytesIO

            from fastapi.responses import StreamingResponse
            data = client.get_object(bucket, obj_name)
            return StreamingResponse(
                BytesIO(data.read()),
                media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                headers={"Content-Disposition": f"attachment; filename={obj_name.rsplit('/', 1)[-1]}"},
            )
        except S3Error:
            return error(404, "文件下载失败")

    return error(404, "无效的文件路径")


@router.post("/deep/stream")
async def agent_deep_stream(req: StreamRequest):
    _check_query_length(req.query)

    from src.apis_agent.agent.deep_research_agent import DeepResearchAgent

    async def event_generator():
        agent = DeepResearchAgent(req.conversationId, req.query, req.fileId)
        async for payload in agent.run():
            yield payload

    return EventSourceResponse(event_generator())


@router.post("/skills/stream")
async def agent_skills_stream(req: StreamRequest):
    _check_query_length(req.query)

    async def event_generator():
        agent = ChatAgent(req.conversationId, req.query, req.fileId, agent_type="skills")
        async for payload in agent.run():
            yield payload

    return EventSourceResponse(event_generator())


@router.post("/stop")
async def agent_stop(req: StopRequest):
    event = BaseAgent._running_tasks.get(req.conversationId)
    if event:
        event.set()
    await publish_stop(req.conversationId)
    if event:
        return ok(None, message="已发送停止信号")
    return ok(None, message="无运行中的任务")


@router.post("/shell/confirm")
async def shell_confirm(req: ShellConfirmRequest):
    ok_result = resolve_confirmation(req.confirmId, req.approved)
    if not ok_result:
        return error(404, "确认请求不存在或已过期")
    return ok(None, message="已确认" if req.approved else "已拒绝")


# ---- 统一入口 + 后台任务 ----

class TaskQueryRequest(BaseModel):
    taskId: str = Field(..., min_length=1)


@router.post("/chat")
async def agent_chat(req: StreamRequest):
    """统一对话入口 — TriageAgent 自动判断分流。"""
    _check_query_length(req.query)

    from src.apis_agent.agent.triage_agent import TriageAgent

    async def event_generator():
        agent = TriageAgent(req.conversationId, req.query, req.fileId)
        async for payload in agent.run():
            yield payload

    return EventSourceResponse(event_generator())


@router.post("/task/status")
async def task_status(req: TaskQueryRequest):
    from src.apis_agent.harness.task_executor import task_executor

    result = task_executor.get_status(req.taskId)
    if not result:
        return error(404, "任务不存在")
    return ok(result)


@router.post("/task/stream")
async def task_stream(req: TaskQueryRequest):
    """SSE 流式获取后台任务执行进度。"""
    from src.apis_agent.harness.task_executor import task_executor
    from src.apis_agent.harness.task_context import TaskStatus

    snapshot = task_executor.get_status(req.taskId)
    if not snapshot:
        async def _err():
            yield make_sse(json.dumps({"type": "error", "content": "任务不存在"}, ensure_ascii=False))
        return EventSourceResponse(_err())

    async def event_generator():
        status = snapshot["status"]
        if status == TaskStatus.COMPLETED.value:
            yield make_sse(json.dumps({"type": "text", "content": snapshot.get("result", "")}, ensure_ascii=False))
        elif status == TaskStatus.FAILED.value:
            yield make_sse(json.dumps({"type": "error", "content": snapshot.get("error", "执行失败")}, ensure_ascii=False))
        elif status == TaskStatus.CANCELLED.value:
            yield make_sse(json.dumps({"type": "error", "content": "任务已取消"}, ensure_ascii=False))
        elif status in (TaskStatus.PENDING.value, TaskStatus.RUNNING.value):
            yield make_sse(json.dumps({
                "type": "task_status", "taskId": req.taskId, "status": status,
            }, ensure_ascii=False))
        yield make_sse(json.dumps({"type": "complete"}, ensure_ascii=False))
        yield make_sse("[DONE]")

    return EventSourceResponse(event_generator())


@router.post("/task/cancel")
async def task_cancel(req: TaskQueryRequest):
    from src.apis_agent.harness.task_executor import task_executor

    if task_executor.cancel(req.taskId):
        return ok(None, message="已发送取消信号")
    return error(404, "任务不存在")


@router.post("/task/list")
async def task_list():
    from src.apis_agent.harness.task_executor import task_executor

    return ok(task_executor.list_tasks())


# ---- 网关管理 ----

class GatewaySwitchRequest(BaseModel):
    modelName: str = Field(..., min_length=1)


@router.post("/admin/gateway")
async def gateway_status():
    from src.apis_agent.gateway.model_gateway import model_gateway
    return ok(model_gateway.get_all_status())


@router.post("/admin/gateway/switch")
async def gateway_switch(req: GatewaySwitchRequest):
    from src.apis_agent.gateway.model_gateway import model_gateway
    from src.apis_agent.agent.chat_agent import invalidate_cached_agents

    try:
        await model_gateway.set_active(req.modelName)
        invalidate_cached_agents()
        return ok(None, message=f"已切换到 {req.modelName}")
    except ValueError as e:
        return error(400, str(e))
