"""Skills 管理 API — 列表 / 上传 / 启用禁用 / 删除。"""

import logging

from fastapi import APIRouter, Request, UploadFile
from pydantic import BaseModel

from src.apis_agent.common.response import error, ok
from src.apis_agent.skill.skill_manager import skill_manager

logger = logging.getLogger("apis")
router = APIRouter(prefix="/api/v1/skills", tags=["skills"])


class ToggleRequest(BaseModel):
    name: str
    enabled: bool


@router.get("")
async def list_skills():
    return ok(skill_manager.list_skills())


@router.post("/upload")
async def upload_skill(file: UploadFile):
    if not file.filename or not file.filename.endswith(".zip"):
        return error(400, "仅支持 .zip 格式的 Skill 包")

    content = await file.read()
    result = skill_manager.upload_zip(content, file.filename)
    if result is None:
        return error(400, "Skill 已存在或格式无效（需包含 SKILL.md）")
    return ok(result)


@router.put("/{name}/toggle")
async def toggle_skill(name: str, req: ToggleRequest):
    if skill_manager.toggle_enabled(req.name, req.enabled):
        return ok(None, message=f"已{'启用' if req.enabled else '禁用'} {req.name}")
    return error(404, "Skill 不存在或 DB 不可用")


@router.delete("/{name}")
async def delete_skill(name: str):
    if skill_manager.delete_skill(name):
        return ok(None, message=f"已删除 {name}")
    return error(404, "Skill 不存在或 DB 不可用")
