"""兼容导出；新代码使用 :mod:`app.modules.chat.sessions`。"""

from app.modules.chat.sessions import Store, store

__all__ = ["Store", "store"]
