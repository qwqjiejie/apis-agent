"""Agent 路由聚合器。

具体行为按聊天、任务、网关、产物和反馈拆分。本模块保留原有公开符号，供现有
调用方渐进迁移。
"""

from fastapi import APIRouter

from app.api.routes.agent_schemas import (
    ChatRequest,
    FeedbackRequest,
    GatewaySwitchRequest,
    ShellConfirmRequest,
    StopRequest,
    TaskQueryRequest,
    TaskResumeRequest,
)
from app.api.routes.artifact_routes import (
    agent_pptx_download,
    agent_stop,
    router as artifact_router,
    shell_confirm,
)
from app.api.routes.chat_routes import (
    agent_chat,
    legacy_router as legacy_chat_router,
    online_eval,
    router as chat_router,
    store,
)
from app.api.routes.feedback_routes import (
    router as feedback_router,
    submit_feedback,
)
from app.api.routes.gateway_routes import (
    gateway_status,
    gateway_switch,
    router as gateway_router,
)
from app.api.routes.task_routes import (
    router as task_router,
    task_cancel,
    task_list,
    task_resume,
    task_status,
    task_stream,
)

router = APIRouter(prefix="/agent", tags=["agent"])
router.include_router(legacy_chat_router)
router.include_router(task_router)
router.include_router(gateway_router)
router.include_router(artifact_router)
router.include_router(feedback_router)

__all__ = [
    "ChatRequest",
    "FeedbackRequest",
    "GatewaySwitchRequest",
    "ShellConfirmRequest",
    "StopRequest",
    "TaskQueryRequest",
    "TaskResumeRequest",
    "agent_chat",
    "agent_pptx_download",
    "agent_stop",
    "chat_router",
    "gateway_status",
    "gateway_switch",
    "online_eval",
    "router",
    "shell_confirm",
    "store",
    "submit_feedback",
    "task_cancel",
    "task_list",
    "task_resume",
    "task_status",
    "task_stream",
]
