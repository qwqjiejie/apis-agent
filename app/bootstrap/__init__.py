"""应用启动与依赖装配。"""

from app.bootstrap.container import (
    ApplicationContainer,
    clear_application_container,
    get_application_container,
    set_application_container,
)

__all__ = [
    "ApplicationContainer",
    "clear_application_container",
    "get_application_container",
    "set_application_container",
]
