"""Agent 统一工厂 — 基于 deepagents.create_deep_agent + SubAgent。

Triage 和 Executor 使用同一工厂，仅参数不同。

deepagents 内置中间件栈:
  TodoList → Filesystem → SubAgent → Summarization → PatchToolCalls
  → HumanInTheLoop（interrupt_on 配置时启用）

SubAgent 通过 AGENT.md 声明式定义，LLM 通过内置 ``task`` 工具原生 spawn。
"""

from __future__ import annotations

import logging

from deepagents import create_deep_agent, SubAgent
from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    ModelRetryMiddleware,
    ToolCallLimitMiddleware,
    ToolRetryMiddleware,
)

from app.common.llm import _create_raw_llm
from app.config.settings import get_settings
from app.gateway.middleware import GatewayModelWrapper
from app.gateway.types import ModelRole
from app.harness.subagent_discovery import discover_specialists
from app.tool import TOOL_REGISTRY

logger = logging.getLogger("apis")


def _build_llm(gateway=None):
    """构建 LLM，优先从网关获取。"""

    if gateway is not None:
        chain = gateway.get_model_chain()
        if chain:
            logger.info(f"[AgentFactory] 网关模型: {chain[0][0]}")
            return GatewayModelWrapper(gateway, ModelRole.CHAT)

    llm = _create_raw_llm()
    model_name = get_settings().llm_model
    if gateway is not None:
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(gateway.register(model_name, llm, is_primary=True))
        except RuntimeError:
            pass
    logger.info(f"[AgentFactory] 原始 LLM: {model_name}")
    return llm


def _build_subagents_from_specialists() -> list[SubAgent]:
    """从 app/subagents/ 扫描 AGENT.md，构建 SubAgent 列表。

    每个 SubAgent 拥有独立的 system_prompt 和 allowed_tools。
    LLM 通过 deepagents 内置的 ``task`` 工具原生 spawn 子代理。
    """



    specialists = discover_specialists()
    subagents: list[SubAgent] = []

    for spec in specialists:
        name = spec["name"]
        allowed_names = spec.get("allowed_tools", [])
        # 按名从 TOOL_REGISTRY 取工具对象
        tools = [TOOL_REGISTRY[n] for n in allowed_names if n in TOOL_REGISTRY]

        sub: SubAgent = {
            "name": name,
            "description": spec.get("description", f"Specialist: {name}"),
            "system_prompt": spec.get("system_prompt", ""),
            "tools": tools,
        }
        subagents.append(sub)
        logger.info(f"[AgentFactory] SubAgent: {name} (tools={[t.name if hasattr(t, 'name') else str(t) for t in tools]})")

    return subagents


def _build_external_tools() -> list:
    """构建额外工具列表（deepagents 内置工具之外的工具）。

    排除与 deepagents 内置工具重复的文件系统工具和 grep。
    """


    # deepagents 已内置: ls, read_file, write_file, edit_file, glob, grep, execute
    _builtin = {"write_file", "edit_file", "glob_files", "grep_tool", "bash_tool"}

    tools = []
    for name, t in TOOL_REGISTRY.items():
        if name not in _builtin:
            tools.append(t)
    return tools


def _build_middleware(*, model_run_limit: int = 15, tool_run_limit: int = 50,
                      tool_thread_limit: int = 50) -> list:
    """构建领域定制中间件栈。

    接入 deepagents create_deep_agent 的 middleware 参数，使以下能力在
    Agent 链路真正生效（之前 self.agent 未传 middleware，重试/限流为死代码）：

    - ModelCallLimitMiddleware: 单次请求/会话模型调用上限，防止死循环
    - ModelRetryMiddleware: 模型调用失败快速重试（指数退避）
    - ToolCallLimitMiddleware: 全局 + 按工具调用次数限制
    - ToolRetryMiddleware: 工具调用失败指数退避重试

    Args:
        model_run_limit: 单次请求模型调用上限（triage 较紧，executor 宽松）
        tool_run_limit: 单工具单次请求调用上限
        tool_thread_limit: 单工具整会话调用上限
    """
    return [
        ModelCallLimitMiddleware(run_limit=model_run_limit, exit_behavior="end"),
        ModelRetryMiddleware(max_retries=1, backoff_factor=1.0, initial_delay=0.5),
        ToolCallLimitMiddleware(thread_limit=tool_thread_limit, run_limit=tool_run_limit),
        ToolRetryMiddleware(max_retries=3, backoff_factor=2.0, initial_delay=1.0),
    ]


async def create_triage_agent(
    system_prompt: str,
    gateway=None,
    subagents: list[SubAgent] | None = None,
    checkpointer=None,
    store=None,
    extra_tools: list | None = None,
    middleware: list | None = None,
):
    """创建 Triage DeepAgent — 统一入口分流。

    Triage 持有额外工具（搜索、任务管理），LLM 自行分流：
    简单问题 → 直接调用工具或 spawn 一个 Specialist
    复杂任务 → create_background_task 后台执行
    """
    model = _build_llm(gateway)
    subagents = subagents or _build_subagents_from_specialists()
    tools = extra_tools or _build_external_tools()
    # triage 面向单轮交互，限流较紧
    mw = middleware if middleware is not None else _build_middleware(model_run_limit=15)

    agent = create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        subagents=subagents,
        middleware=mw,
        checkpointer=checkpointer,
        store=store,
    )
    logger.info(
        f"[AgentFactory] Triage DeepAgent 创建完成 (tools={len(tools)}, subagents={len(subagents)}, middleware={len(mw)})"
    )
    return agent


async def create_executor_agent(
    system_prompt: str,
    gateway=None,
    subagents: list[SubAgent] | None = None,
    checkpointer=None,
    store=None,
    interrupt_on: dict | None = None,
    middleware: list | None = None,
    *,
    is_background: bool = False,
):
    """创建 Executor DeepAgent — 后台任务执行。

    Executor 工具精简（仅 task + request_approval），专注编排而非执行。
    """
    model = _build_llm(gateway)
    subagents = subagents or _build_subagents_from_specialists()

    executor_tools: list = []

    for name in ("request_approval", "read_task_journal"):
        if name in TOOL_REGISTRY:
            executor_tools.append(TOOL_REGISTRY[name])

    # executor 面向长任务编排，限流宽松；后台运行时进一步放宽
    _run_limit = 200 if is_background else 15
    mw = middleware if middleware is not None else _build_middleware(model_run_limit=_run_limit)

    agent = create_deep_agent(
        model=model,
        tools=executor_tools,
        system_prompt=system_prompt,
        subagents=subagents,
        middleware=mw,
        checkpointer=checkpointer,
        store=store,
        interrupt_on=interrupt_on,
    )
    logger.info(
        f"[AgentFactory] Executor DeepAgent 创建完成 (tools={len(executor_tools)}, subagents={len(subagents)}, middleware={len(mw)})"
    )
    return agent
