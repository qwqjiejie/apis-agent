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
                get_settings().minio_endpoint,
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
