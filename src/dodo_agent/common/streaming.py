import json
from enum import StrEnum


class StreamEventType(StrEnum):
    THINKING = "thinking"
    TEXT = "text"
    TOOL_START = "tool_start"
    TOOL_END = "tool_end"
    REFERENCE = "reference"
    RECOMMEND = "recommend"
    ERROR = "error"
    COMPLETE = "complete"
    PHASE_START = "phase_start"
    PHASE_END = "phase_end"
    TASK_START = "task_start"
    TASK_END = "task_end"
    PLAN = "plan"
    CRITIQUE = "critique"
    FILE_READY = "file_ready"


class AgentStopped(Exception):
    pass


def make_event(event_type: str, **kwargs) -> dict:
    payload = {"type": event_type}
    payload.update(kwargs)
    return {"event": "message", "data": json.dumps(payload, ensure_ascii=False)}


def make_sse(text: str) -> dict:
    return {"event": "message", "data": text}
