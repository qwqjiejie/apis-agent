"""兼容导出；新代码使用 app.modules.documents.events。"""

from app.modules.documents.events import DocumentEventBus, event_bus

__all__ = ["DocumentEventBus", "event_bus"]
