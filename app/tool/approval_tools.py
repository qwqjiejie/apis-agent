"""审批工具 — request_approval / read_task_journal。"""

import logging

from langchain_core.tools import tool

from app.bootstrap.container import get_application_container
from app.tool.registry import register_tool

logger = logging.getLogger("apis")


@register_tool
@tool
async def request_approval(approval_id: str, description: str) -> str:
    """发起人审审批请求。

    当 Specialist 输出中出现 ``[HUMAN_APPROVAL_REQUIRED]`` 标记时调用此工具。
    Executor 配置了 interrupt_on，调用后执行自动挂起，等待人类决策。

    Args:
        approval_id: 审批请求唯一标识
        description: 审批内容描述
    """
    return f"[HUMAN_APPROVAL_REQUIRED]\napproval_id: {approval_id}\ndescription: {description}\n状态: 等待人工审批"


@register_tool
@tool
async def read_task_journal(task_id: str = "") -> str:
    """读取当前任务的执行日志，了解已完成的步骤和关键决策。

    Args:
        task_id: 任务编号。不传则读取当前任务。
    """
    runtime = get_application_container()
    task_executor = runtime.task_executor
    context = runtime.context_manager.get()
    resolved_task_id = task_id or context.task_id
    if not resolved_task_id:
        return "当前没有可读取的任务上下文"

    entries = await task_executor.read_journal(
        resolved_task_id,
        user_id=context.user_id,
    )
    logger.info(f"[Journal] 读取日志: {resolved_task_id}, entries={len(entries)}")
    if not entries:
        return f"任务 {resolved_task_id} 暂无执行日志"

    lines = [f"任务 {resolved_task_id} 执行日志:"]
    for entry in entries:
        lines.append(
            f"{entry['step']}. [{entry['event']}] {entry['description']}"
        )
    return "\n".join(lines)
