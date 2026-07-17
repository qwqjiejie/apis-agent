"""兼容导出；新代码使用 :mod:`app.modules.tasks.events`。"""

from app.modules.tasks.events import EventBus, Handler

__all__ = ["EventBus", "Handler"]
