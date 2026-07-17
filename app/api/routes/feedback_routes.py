"""用户对 Agent 回答的反馈接口。"""

from fastapi import APIRouter, Request

from app.api.routes.agent_schemas import FeedbackRequest
from app.api.dependencies import inspect_session_access
from app.common.response import error, ok
from app.service.feedback_service import record_feedback

router = APIRouter(tags=["agent-feedback"])


@router.post("/feedback")
async def submit_feedback(req: FeedbackRequest, request: Request):
    access = inspect_session_access(request, req.conversationId)
    user_id = access.user_id
    if not access.allowed:
        return error(403, "无权访问该会话")

    try:
        record_feedback(
            req.conversationId,
            user_id,
            req.rating,
            req.comment,
        )
        return ok(None, message="反馈已记录")
    except Exception as exc:
        return error(500, str(exc))
