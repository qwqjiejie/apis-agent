"""Skills 管理 API — 列表 / 上传 / 启用禁用 / 删除。"""

from fastapi import APIRouter, Request, UploadFile
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.common.response import error, ok

router = APIRouter(prefix="/api/v1/skills", tags=["skills"])


class ToggleRequest(BaseModel):
    name: str
    enabled: bool


@router.get("")
async def list_skills(request: Request):
    manager = request.app.state.container.skill_manager
    return ok(await run_in_threadpool(manager.list_skills))


@router.post("/upload")
async def upload_skill(request: Request, file: UploadFile):
    if not file.filename or not file.filename.endswith(".zip"):
        return error(400, "仅支持 .zip 格式的 Skill 包")

    content = await file.read()
    result = await run_in_threadpool(
        request.app.state.container.skill_manager.upload_zip,
        content,
        file.filename,
    )
    if result is None:
        return error(400, "Skill 已存在或格式无效（需包含 SKILL.md）")
    return ok(result)


@router.put("/{name}/toggle")
async def toggle_skill(name: str, req: ToggleRequest, request: Request):
    if req.name != name:
        return error(400, "路径名称与请求名称不一致")
    if await run_in_threadpool(
        request.app.state.container.skill_manager.toggle_enabled,
        name,
        req.enabled,
    ):
        return ok(None, message=f"已{'启用' if req.enabled else '禁用'} {name}")
    return error(404, "Skill 不存在或 DB 不可用")


@router.delete("/{name}")
async def delete_skill(name: str, request: Request):
    if await run_in_threadpool(
        request.app.state.container.skill_manager.delete_skill,
        name,
    ):
        return ok(None, message=f"已删除 {name}")
    return error(404, "Skill 不存在或 DB 不可用")
