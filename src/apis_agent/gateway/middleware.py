"""GatewayMiddleware — 健康感知模型路由。

每次模型调用时从 ModelGateway 获取健康排序的模型链，
跳过已熔断的模型，逐个尝试直到成功，记录延迟和成败。

用于替换原来的 GatewayLLM 包装器，在 Agent 管线层工作。
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from langchain_core.language_models import BaseChatModel

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from src.apis_agent.gateway.model_gateway import ModelGateway
    from src.apis_agent.gateway.types import ModelRole

logger = logging.getLogger("apis")


class GatewayModelWrapper(BaseChatModel):
    """网关模型包装器 — 在 LLM 调用前后执行熔断检查和健康上报。

    与旧 GatewayLLM 的区别：
    - 不再硬编码到单个模型，而是每次调用时从 Gateway 动态获取模型链
    - 自动跳过已熔断的模型
    - 通过 current_handler contextvar 推送降级/恢复状态事件
    """

    def __init__(self, gateway: "ModelGateway", role: "ModelRole", **kwargs):
        super().__init__(**kwargs)
        self._gateway = gateway
        self._role = role

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

        last_error: Exception | None = None
        for name, model in chain:
            try:
                start = time.monotonic()
                # 执行熔断前检查
                if not await self._gateway.before_request(name):
                    logger.warning(f"[Gateway] {name} 已熔断，跳过")
                    continue
                result = await model._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
                latency = (time.monotonic() - start) * 1000
                await self._gateway.record_success(name, latency)
                return result
            except Exception as e:
                await self._gateway.record_failure(name, str(e))
                last_error = e
                logger.warning(f"[Gateway] {name} 调用失败: {e}，尝试降级")

        raise last_error or RuntimeError("所有模型不可用")

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        chain = self._build_model_chain()
        if not chain:
            raise RuntimeError("无可用模型")

        last_error: Exception | None = None
        for name, model in chain:
            try:
                start = time.monotonic()
                if not await self._gateway.before_request(name):
                    continue
                async for chunk in model._astream(messages, stop=stop, run_manager=run_manager, **kwargs):
                    yield chunk
                latency = (time.monotonic() - start) * 1000
                await self._gateway.record_success(name, latency)
                return
            except Exception as e:
                await self._gateway.record_failure(name, str(e))
                last_error = e
                logger.warning(f"[Gateway] {name} 流式调用失败: {e}，尝试降级")

        raise last_error or RuntimeError("所有模型不可用")

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise NotImplementedError("仅支持异步调用")
