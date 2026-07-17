"""tool_search — 延迟工具按需发现。

LLM 通过 tool_search 按关键词检索工具库，
获取匹配工具的 name + description，然后再调用具体工具。

与常驻工具的区别：
- 常驻工具：name + description 始终在 system prompt 中可见
- 延迟工具：不在 prompt 中，需要先 tool_search 发现
"""

import logging

from langchain_core.tools import tool

from app.tool.registry import TOOL_REGISTRY, register_tool

logger = logging.getLogger("apis")

# 延迟工具名列表 — 不在 system prompt 中直接展示，需 tool_search 发现
DEFERRED_TOOL_NAMES: set[str] = set()


def register_deferred(*names: str):
    """将工具标记为延迟工具。"""
    DEFERRED_TOOL_NAMES.update(names)


def get_deferred_tools() -> dict[str, object]:
    """返回所有延迟工具 {name: tool_obj}。"""
    return {n: TOOL_REGISTRY[n] for n in DEFERRED_TOOL_NAMES if n in TOOL_REGISTRY}


def get_always_on_tools() -> dict[str, object]:
    """返回所有常驻工具。"""
    return {n: t for n, t in TOOL_REGISTRY.items() if n not in DEFERRED_TOOL_NAMES}


@register_tool
@tool
async def tool_search(query: str) -> str:
    """搜索可用工具。当常驻工具不足以完成任务时，通过关键词搜索延迟工具库。

    返回匹配工具的 name 和 description。找到需要的工具后，直接按 name 调用它。

    Args:
        query: 搜索关键词（如 "图表", "SQL", "术语"）
    """
    if not query:
        return "请提供搜索关键词"

    deferred = get_deferred_tools()
    if not deferred:
        return "（当前无可用延迟工具）"

    query_lower = query.lower()
    matches = []
    for name, t in deferred.items():
        desc = getattr(t, "description", "") or ""
        if query_lower in name.lower() or query_lower in desc.lower():
            matches.append(f"- {name}: {desc.split(chr(10))[0]}")

    if not matches:
        available = ", ".join(deferred.keys())
        return f"未找到匹配 '{query}' 的工具。可用延迟工具: {available}"

    return "\n".join(matches)
