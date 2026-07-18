from datetime import datetime

from langchain_core.tools import tool

from app.tool.registry import register_tool


@register_tool
@tool
def get_current_time() -> str:
    """查询当前系统本地时间，返回带时区偏移的 ISO 8601 时间。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")
