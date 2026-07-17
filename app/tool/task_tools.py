"""任务管理工具 — task / create_background_task / get_task_status。

这些工具让 LLM 能够委托 Specialist 和执行后台任务。
"""

import logging

from langchain_core.tools import tool

from app.bootstrap.container import get_application_container
from app.tool.registry import register_tool

logger = logging.getLogger("apis")

@register_tool
@tool
async def create_background_task(goal: str, plan: str = "") -> str:
    """将复杂任务转为后台异步执行。

    适用于需要多个 Specialist 协作、涉及审批、需要长周期执行的任务。
    任务创建后立即返回任务编号，用户可随时查询进度。

    Args:
        goal: 任务目标（一句话描述）
        plan: 简要执行计划（可选）
    """
    runtime = get_application_container()
    task_executor = runtime.task_executor
    context_manager = runtime.context_manager

    query = goal
    if plan:
        query = f"{goal}\n\n执行计划: {plan}"

    # 提交后台任务
    context = context_manager.get()
    conv = context.session_id or "bg"

    async def _execute(snapshot):
        from app.agent.executor_agent import ExecutorAgent
        executor = ExecutorAgent(
            snapshot,
            plan,
            executor_agent=runtime.executor_agent,
            context_manager=context_manager,
        )
        async for event in executor.run():
            yield event

    task_id = await task_executor.submit(
        conv,
        query,
        _execute,
        user_id=context.user_id,
    )
    return f"任务已创建。任务编号: {task_id}\n目标: {goal}\n状态: 后台执行中，可随时查询进度。"


@register_tool
@tool
async def get_task_status(task_id: str = "") -> str:
    """查询后台任务状态、进度和结果。

    Args:
        task_id: 任务编号。不传则列出当前会话所有任务概览。
    """
    runtime = get_application_container()
    task_executor = runtime.task_executor
    user_id = runtime.context_manager.get().user_id

    if task_id:
        status = await task_executor.get_status(task_id, user_id=user_id)
        if not status:
            return f"任务 '{task_id}' 不存在"
        return (
            f"任务: {status['taskId']}\n"
            f"状态: {status['status']}\n"
            f"查询: {status['query'][:200]}\n"
            f"结果: {status.get('result', '')[:500] or '(暂无)'}\n"
            f"错误: {status.get('error', '') or '无'}"
        )

    tasks = await task_executor.list_tasks(user_id=user_id)
    if not tasks:
        return "当前无后台任务"
    lines = ["当前任务列表:"]
    for t in tasks[:10]:
        lines.append(f"- {t['taskId']}: [{t['status']}] {t['query'][:60]}")
    return "\n".join(lines)
