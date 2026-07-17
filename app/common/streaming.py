import json
from enum import StrEnum


class StreamEventType(StrEnum):
    # Agent 生命周期
    AGENT_START = "agent_start"
    COMPLETE = "complete"
    PAUSED = "paused"

    # 输出
    THINKING = "thinking"
    TEXT = "text"
    STAGE_OUTPUT = "stage_output"

    # 工具
    TOOL_START = "tool_start"
    TOOL_END = "tool_end"

    # 任务清单
    TODO_PROGRESS = "todo_progress"

    # 引用/推荐
    REFERENCE = "reference"
    RECOMMEND = "recommend"

    # 错误/状态
    ERROR = "error"
    STATUS = "status"

    # 深度研究阶段
    PHASE_START = "phase_start"
    PHASE_END = "phase_end"
    TASK_START = "task_start"
    TASK_END = "task_end"
    PLAN = "plan"
    CRITIQUE = "critique"

    # 文件/Shell
    FILE_READY = "file_ready"
    CONFIRM_SHELL = "confirm_shell"


class AgentStopped(Exception):
    pass


def make_event(event_type: str, **kwargs) -> dict:
    payload = {"type": event_type}
    payload.update(kwargs)
    return {"event": "message", "data": json.dumps(payload, ensure_ascii=False)}


def make_sse(text: str) -> dict:
    return {"event": "message", "data": text}
