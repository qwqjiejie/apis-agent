"""Agent 产物下载、停止和 Shell 确认接口。"""

import os
from io import BytesIO

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, StreamingResponse
from minio.error import S3Error

from app.api.routes.agent_schemas import ShellConfirmRequest, StopRequest
from app.api.dependencies import inspect_session_access
from app.auth import get_current_user_id
from app.common.redis import publish_stop
from app.common.response import error, ok
from app.storage.models.ai_ppt_inst import PptInstRepo
from app.tool.bash_tool import resolve_confirmation

router = APIRouter(tags=["agent-artifacts"])


@router.post("/pptx/download")
async def agent_pptx_download(req: StopRequest, request: Request):
    if not inspect_session_access(request, req.conversationId).allowed:
        return error(403, "无权访问该会话")

    instance = PptInstRepo().find_by_session_id(req.conversationId)
    if not instance or not instance.file_url:
        return error(404, "PPT文件不存在")

    file_url = instance.file_url
    if file_url.startswith("local://"):
        local_path = file_url.removeprefix("local://")
        if not os.path.exists(local_path):
            return error(404, "文件已被清理")
        return FileResponse(
            local_path,
            filename=os.path.basename(local_path),
            media_type=(
                "application/vnd.openxmlformats-officedocument."
                "presentationml.presentation"
            ),
        )

    if file_url.startswith("minio://"):
        client = request.app.state.container.minio_client
        if client is None:
            return error(404, "文件下载失败")
        bucket, object_name = file_url.removeprefix("minio://").split("/", 1)
        try:
            data = client.get_object(bucket, object_name)
            return StreamingResponse(
                BytesIO(data.read()),
                media_type=(
                    "application/vnd.openxmlformats-officedocument."
                    "presentationml.presentation"
                ),
                headers={
                    "Content-Disposition": (
                        f"attachment; filename={object_name.rsplit('/', 1)[-1]}"
                    ),
                },
            )
        except S3Error:
            return error(404, "文件下载失败")
    return error(404, "无效的文件路径")


@router.post("/stop")
async def agent_stop(req: StopRequest, request: Request):
    if not inspect_session_access(request, req.conversationId).allowed:
        return error(403, "无权访问该会话")
    await publish_stop(req.conversationId)
    return ok(None, message="已发送停止信号")


@router.post("/shell/confirm")
async def shell_confirm(req: ShellConfirmRequest, request: Request):
    confirmed = resolve_confirmation(
        req.confirmId,
        req.approved,
        get_current_user_id(request),
    )
    if not confirmed:
        return error(404, "确认请求不存在或已过期")
    return ok(None, message="已确认" if req.approved else "已拒绝")
