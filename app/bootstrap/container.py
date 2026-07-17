"""应用运行时容器。

容器由应用生命周期创建，是进程内运行时依赖的唯一所有者。模块级访问器仅用于
尚未迁移到构造器注入的非 HTTP 调用链，新的 API 代码应优先从 ``request.app``
读取容器。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.gateway.model_gateway import ModelGateway


@dataclass(slots=True)
class ApplicationContainer:
    model_gateway: ModelGateway
    checkpointer: Any = None
    store: Any = None
    minio_client: Any = None
    agent: Any = None
    executor_agent: Any = None
    specialist_subagents: list[Any] = field(default_factory=list)
    task_executor: Any = None
    context_manager: Any = None
    dead_letter_queue: Any = None
    semantic_memory: Any = None
    event_bus: Any = None
    tool_hot_reloader: Any = None
    subagent_hot_reloader: Any = None


_application_container: ApplicationContainer | None = None


def set_application_container(container: ApplicationContainer) -> None:
    global _application_container
    _application_container = container


def get_application_container(*, required: bool = True) -> ApplicationContainer | None:
    if _application_container is None and required:
        raise RuntimeError("应用运行时容器尚未初始化")
    return _application_container


def clear_application_container(container: ApplicationContainer | None = None) -> None:
    global _application_container
    if container is None or _application_container is container:
        _application_container = None
