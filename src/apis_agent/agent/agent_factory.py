"""Agent 统一工厂 — 基于 LangGraph ReAct Agent + 网关包装。

Triage 和 Executor 使用同一工厂函数，仅参数（tools/system_prompt/subagents）不同。

与 deepagents.create_deep_agent 的关键对齐：
- 统一工厂模式
- SubAgent 支持（通过 tool_calls 委派）
- 网关模型包装（GatewayModelWrapper 替代 GatewayLLM）
- LangGraph checkpointer + store 持久化
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from src.apis_agent.config.settings import get_settings

logger = logging.getLogger("apis")


def _build_model(gateway=None):
    """构建 LLM 实例。优先使用网关包装器，否则回退原始 LLM。"""
    from src.apis_agent.gateway.middleware import GatewayModelWrapper
    from src.apis_agent.gateway.types import ModelRole

    if gateway is not None:
        chain = gateway.get_model_chain(ModelRole.CHAT)
        if chain:
            wrapper = GatewayModelWrapper(gateway=gateway, role=ModelRole.CHAT)
            logger.info(f"[AgentFactory] 使用网关模型包装器，主模型={chain[0][0]}")
            return wrapper

    # 回退：原始 LLM
    from src.apis_agent.common.llm import _create_raw_llm
    logger.info("[AgentFactory] 网关未就绪，使用原始 LLM")
    return _create_raw_llm()


async def create_triage_agent(
    tools: list,
    system_prompt: str,
    checkpointer: AsyncPostgresSaver | None = None,
    store: Any = None,
    subagents: list[dict] | None = None,
    gateway=None,
):
    """创建 Triage DeepAgent — 分流判断。

    Triage 持有全部工具，LLM 根据分流规则自行判断：
    简单问题 → 直接处理或委托一次 Specialist
    复杂任务 → 调用 create_background_task
    """
    model = _build_model(gateway)
    subagents = subagents or []

    agent = create_react_agent(
        model=model,
        tools=tools,
        prompt=system_prompt,
        checkpointer=checkpointer,
        store=store,
    )
    logger.info(f"[AgentFactory] TriageAgent 创建完成 (tools={len(tools)}, subagents={len(subagents)})")
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
    """创建 Executor DeepAgent — 后台任务执行。

    Executor 持有精简工具集（task/request_approval/read_task_journal），
    专注"制定计划 → 逐步委托 → 处理审批 → 汇报结果"。
    """
    model = _build_model(gateway)
    subagents = subagents or []

    agent = create_react_agent(
        model=model,
        tools=tools,
        prompt=system_prompt,
        checkpointer=checkpointer,
        store=store,
    )
    logger.info(
        f"[AgentFactory] ExecutorAgent 创建完成 (tools={len(tools)}, subagents={len(subagents)}, interrupt_on={bool(interrupt_on)})"
    )
    return agent
