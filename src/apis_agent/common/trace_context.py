import uuid
from contextvars import ContextVar

trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")
session_id_var: ContextVar[str] = ContextVar("session_id", default="")
agent_type_var: ContextVar[str] = ContextVar("agent_type", default="")


def generate_trace_id() -> str:
    return uuid.uuid4().hex[:16]


def get_trace_id() -> str:
    return trace_id_var.get()


def set_trace_context(trace_id: str = "", session_id: str = "", agent_type: str = ""):
    trace_id_var.set(trace_id or generate_trace_id())
    session_id_var.set(session_id)
    agent_type_var.set(agent_type)
