import json
import os

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from sse_starlette.sse import EventSourceResponse

from fastapi import Body

from src.dodo_agent.agent.base_agent import BaseAgent
from src.dodo_agent.agent.chat_agent import ChatAgent
from src.dodo_agent.common.exceptions import DodoAgentError, QueryTooLongError
from src.dodo_agent.common.logger import logger
from src.dodo_agent.common.redis import publish_stop
from src.dodo_agent.common.response import ok, error
from src.dodo_agent.common.streaming import make_sse
from src.dodo_agent.config.settings import get_settings
from src.dodo_agent.tool.bash_tool import resolve_confirmation

router = APIRouter(prefix="/agent", tags=["agent"])


def _check_query_length(query: str):
    """在 SSE 生成器之前校验，异常由全局处理器返回非 200，前端弹窗展示。"""
    if len(query) > get_settings().max_query_length:
        raise QueryTooLongError(get_settings().max_query_length)


@router.get("/chat/stream")
async def agent_chat_stream(
        query: str = Query(...),
        conversationId: str = Query(...),
        fileId: str = Query(default=""),
):
    _check_query_length(query)

    async def event_generator():
        agent = ChatAgent(conversationId, query, fileId, agent_type="chat")
        async for payload in agent.run():
            yield payload

    return EventSourceResponse(event_generator())


@router.get("/file/stream")
async def agent_file_stream(
        query: str = Query(...),
        conversationId: str = Query(...),
        fileId: str = Query(...),
):
    _check_query_length(query)

    async def event_generator():
        agent = ChatAgent(conversationId, query, fileId, agent_type="chat")
        async for payload in agent.run():
            yield payload

    return EventSourceResponse(event_generator())


@router.get("/pptx/stream")
async def agent_pptx_stream(
        query: str = Query(...),
        conversationId: str = Query(...),
):
    _check_query_length(query)

    async def event_generator():
        from src.dodo_agent.agent.ppt_builder_agent import PptBuilderAgent
        agent = PptBuilderAgent(conversationId, query)
        async for payload in agent.run():
            yield payload

    return EventSourceResponse(event_generator())


@router.get("/pptx/download")
async def agent_pptx_download(conversationId: str = Query(...)):
    from src.dodo_agent.storage.models.ai_ppt_inst import PptInstRepo
    from minio import Minio
    from minio.error import S3Error

    repo = PptInstRepo()
    inst = repo.find_by_conversation_id(conversationId)
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
            from fastapi.responses import StreamingResponse
            from io import BytesIO
            data = client.get_object(bucket, obj_name)
            return StreamingResponse(
                BytesIO(data.read()),
                media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                headers={"Content-Disposition": f"attachment; filename={obj_name.rsplit('/', 1)[-1]}"},
            )
        except S3Error:
            return error(404, "文件下载失败")

    return error(404, "无效的文件路径")


@router.get("/deep/stream")
async def agent_deep_stream(
        query: str = Query(...),
        conversationId: str = Query(...),
        fileId: str = Query(default=""),
):
    _check_query_length(query)

    from src.dodo_agent.agent.deep_research_agent import DeepResearchAgent

    async def event_generator():
        agent = DeepResearchAgent(conversationId, query, fileId)
        async for payload in agent.run():
            yield payload

    return EventSourceResponse(event_generator())


@router.get("/skills/stream")
async def agent_skills_stream(
        query: str = Query(...),
        conversationId: str = Query(...),
        fileId: str = Query(default=""),
):
    _check_query_length(query)

    async def event_generator():
        agent = ChatAgent(conversationId, query, fileId, agent_type="skills")
        async for payload in agent.run():
            yield payload

    return EventSourceResponse(event_generator())


@router.get("/stop")
async def agent_stop(conversationId: str = Query(...)):
    event = BaseAgent._running_tasks.get(conversationId)
    if event:
        event.set()
    await publish_stop(conversationId)
    if event:
        return ok(None, message="已发送停止信号")
    return ok(None, message="无运行中的任务")


@router.post("/shell/confirm")
async def shell_confirm(confirmId: str = Body(...), approved: bool = Body(...)):
    ok_result = resolve_confirmation(confirmId, approved)
    if not ok_result:
        return error(404, "确认请求不存在或已过期")
    return ok(None, message="已确认" if approved else "已拒绝")
