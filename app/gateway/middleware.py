"""GatewayMiddleware — 健康感知模型路由。

每次模型调用时从 ModelGateway 获取健康排序的模型链，
跳过已熔断的模型，逐个尝试直到成功，记录延迟和成败。

用于替换原来的 GatewayLLM 包装器，在 Agent 管线层工作。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableBinding
from pydantic import PrivateAttr

from app.gateway.status_events import emit_gateway_status

if TYPE_CHECKING:
    from app.gateway.model_gateway import ModelGateway
    from app.gateway.types import ModelRole

logger = logging.getLogger("apis")


class GatewayModelWrapper(BaseChatModel):
    """网关模型包装器 — 在 LLM 调用前后执行熔断检查和健康上报。

    与旧 GatewayLLM 的区别：
    - 不再硬编码到单个模型，而是每次调用时从 Gateway 动态获取模型链
    - 自动跳过已熔断的模型
    - 通过 current_handler contextvar 推送降级/恢复状态事件
    """

    _gateway: Any = PrivateAttr()
    _role: Any = PrivateAttr()
    _bound_tools: list = PrivateAttr(default_factory=list)
    _tool_choice: str | None = PrivateAttr(default=None)
    _tool_bind_kwargs: dict[str, Any] = PrivateAttr(default_factory=dict)

    def __init__(self, gateway: "ModelGateway", role: "ModelRole", **kwargs):
        super().__init__(**kwargs)
        self._gateway = gateway
        self._role = role

    def bind_tools(
        self,
        tools: Sequence,
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ):
        bound = self.model_copy(deep=False)
        bound._gateway = self._gateway
        bound._role = self._role
        bound._bound_tools = list(tools)
        bound._tool_choice = tool_choice
        bound._tool_bind_kwargs = dict(kwargs)
        return bound

    def _prepare_model(self, model, call_kwargs: dict) -> tuple[Any, dict]:
        if not self._bound_tools:
            return model, call_kwargs
        bound = model.bind_tools(
            self._bound_tools,
            tool_choice=self._tool_choice,
            **self._tool_bind_kwargs,
        )
        if isinstance(bound, RunnableBinding) and isinstance(bound.bound, BaseChatModel):
            return bound.bound, {**bound.kwargs, **call_kwargs}
        if isinstance(bound, BaseChatModel):
            return bound, call_kwargs
        raise TypeError(f"模型 {type(model).__name__} 的 bind_tools 返回了不支持的类型")

    @property
    def _llm_type(self) -> str:
        return "gateway-wrapper"

    def _build_model_chain(self) -> list[tuple[str, Any]]:
        """从网关获取当前可用的模型链。"""
        return self._gateway.get_model_chain(self._role)

    def _select_model(self) -> tuple[str, Any]:
        chain = self._build_model_chain()
        if not chain:
            raise RuntimeError("无可用模型")
        return chain[0]

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        chain = self._build_model_chain()
        if not chain:
            raise RuntimeError("无可用模型，所有模型均已熔断或未注册")
        active_name = self._gateway.get_active_name()
        if active_name and chain[0][0] != active_name:
            await emit_gateway_status(
                status="fallback",
                fromModel=active_name,
                toModel=chain[0][0],
                reason="circuit_open",
            )

        last_error: Exception | None = None
        for index, (name, model) in enumerate(chain):
            try:
                start = time.monotonic()
                # 执行熔断前检查
                if not await self._gateway.before_request(name):
                    logger.warning(f"[Gateway] {name} 已熔断，跳过")
                    if index + 1 < len(chain):
                        await emit_gateway_status(
                            status="fallback",
                            fromModel=name,
                            toModel=chain[index + 1][0],
                            reason="circuit_open",
                        )
                    continue
                target, call_kwargs = self._prepare_model(model, kwargs)
                result = await target._agenerate(
                    messages,
                    stop=stop,
                    run_manager=run_manager,
                    **call_kwargs,
                )
                latency = (time.monotonic() - start) * 1000
                await self._gateway.record_success(name, latency)
                return result
            except Exception as e:
                await self._gateway.record_failure(name, str(e))
                last_error = e
                logger.warning(f"[Gateway] {name} 调用失败: {e}，尝试降级")
                if index + 1 < len(chain):
                    await emit_gateway_status(
                        status="fallback",
                        fromModel=name,
                        toModel=chain[index + 1][0],
                        reason=str(e),
                    )

        raise last_error or RuntimeError("所有模型不可用")

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        chain = self._build_model_chain()
        if not chain:
            raise RuntimeError("无可用模型")
        active_name = self._gateway.get_active_name()
        if active_name and chain[0][0] != active_name:
            await emit_gateway_status(
                status="fallback",
                fromModel=active_name,
                toModel=chain[0][0],
                reason="circuit_open",
            )

        last_error: Exception | None = None
        for index, (name, model) in enumerate(chain):
            emitted = False
            try:
                start = time.monotonic()
                if not await self._gateway.before_request(name):
                    if index + 1 < len(chain):
                        await emit_gateway_status(
                            status="fallback",
                            fromModel=name,
                            toModel=chain[index + 1][0],
                            reason="circuit_open",
                        )
                    continue
                target, call_kwargs = self._prepare_model(model, kwargs)
                async for chunk in target._astream(
                    messages,
                    stop=stop,
                    run_manager=run_manager,
                    **call_kwargs,
                ):
                    emitted = True
                    yield chunk
                latency = (time.monotonic() - start) * 1000
                await self._gateway.record_success(name, latency)
                return
            except Exception as e:
                await self._gateway.record_failure(name, str(e))
                last_error = e
                if emitted:
                    raise
                logger.warning(f"[Gateway] {name} 流式调用失败: {e}，尝试降级")
                if index + 1 < len(chain):
                    await emit_gateway_status(
                        status="fallback",
                        fromModel=name,
                        toModel=chain[index + 1][0],
                        reason=str(e),
                    )

        raise last_error or RuntimeError("所有模型不可用")

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise NotImplementedError("仅支持异步调用")
