"""模型网关状态和热切换管理接口。"""

from fastapi import APIRouter, Request

from app.api.routes.agent_schemas import GatewaySwitchRequest
from app.common.response import error, ok

router = APIRouter(tags=["agent-gateway"])


@router.post("/admin/gateway")
async def gateway_status(request: Request):
    gateway = request.app.state.container.model_gateway
    return ok(gateway.get_all_status())


@router.post("/admin/gateway/switch")
async def gateway_switch(req: GatewaySwitchRequest, request: Request):
    gateway = request.app.state.container.model_gateway
    try:
        await gateway.set_active(req.modelName)
        return ok(None, message=f"已切换到 {req.modelName}")
    except ValueError as exc:
        return error(400, str(exc))
