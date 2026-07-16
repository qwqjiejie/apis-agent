"""Agent 统一工厂 — 基于 LangGraph ReAct Agent + 中间件管线。

Triage 和 Executor 使用同一工厂函数，仅参数不同。

中间件管线（按执行顺序）：
1. ToolCallLimit — 全局 + 按工具限制调用次数
2. ToolRetry — 工具调用失败指数退避重试（最多 3 次）
3. ModelRetry — 模型调用失败快速兜底重试（1 次）
4. GatewayWrapper — 健康感知模型路由（网关就绪时启用）
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from src.apis_agent.agent.middleware import (
    ToolCallLimiter,
    ModelRetryWrapper,
    wrap_tool_with_limit,
    wrap_tool_with_retry,
)
from src.apis_agent.config.settings import get_settings

logger = logging.getLogger("apis")


def _build_model(gateway=None):
    from src.apis_agent.gateway.middleware import GatewayModelWrapper
    from src.apis_agent.gateway.types import ModelRole

    if gateway is not None:
        chain = gateway.get_model_chain(ModelRole.CHAT)
        if chain:
            wrapper = GatewayModelWrapper(gateway=gateway, role=ModelRole.CHAT)
            logger.info(f"[AgentFactory] 使用网关模型包装器，主模型={chain[0][0]}")
            # 模型重试包装
            return ModelRetryWrapper(wrapper)

    from src.apis_agent.common.llm import _create_raw_llm
    logger.info("[AgentFactory] 网关未就绪，使用原始 LLM")
    return ModelRetryWrapper(_create_raw_llm())


def _apply_tool_middleware(tools: list) -> list:
    """对工具列表应用中间件包装：ToolRetry + ToolCallLimit。"""
    limiter = ToolCallLimiter(run_limit=50, thread_limit=50)
    wrapped = []
    for tool in tools:
        tool = wrap_tool_with_retry(tool, max_retries=3, backoff_factor=2.0, initial_delay=1.0)
        tool = wrap_tool_with_limit(tool, limiter)
        wrapped.append(tool)
    logger.info(f"[AgentFactory] 工具中间件已应用: {len(wrapped)} 个工具")
    return wrapped


async def create_triage_agent(
    tools: list,
    system_prompt: str,
    checkpointer: AsyncPostgresSaver | None = None,
    store: Any = None,
    subagents: list[dict] | None = None,
    gateway=None,
):
    model = _build_model(gateway)
    wrapped_tools = _apply_tool_middleware(tools)
    subagents = subagents or []

    agent = create_react_agent(
        model=model,
        tools=wrapped_tools,
        prompt=system_prompt,
        checkpointer=checkpointer,
        store=store,
    )
    logger.info(f"[AgentFactory] TriageAgent 创建完成 (tools={len(wrapped_tools)}, subagents={len(subagents)})")
    return agent


async def create_executor_agent(
    tools: list,
    system_prompt: str,
    checkpointer: AsyncPostgresSaver | None = None,
    store: Any = None,
    subagents: list[dict] | None = None,
    gateway=None,
    interrupt_on: dict | None = None,
):
    model = _build_model(gateway)
    wrapped_tools = _apply_tool_middleware(tools)
    subagents = subagents or []

    agent = create_react_agent(
        model=model,
        tools=wrapped_tools,
        prompt=system_prompt,
        checkpointer=checkpointer,
        store=store,
    )
    logger.info(
        f"[AgentFactory] ExecutorAgent 创建完成 (tools={len(wrapped_tools)}, subagents={len(subagents)})"
    )
    return agent
