"""兼容导出；新代码使用 :mod:`app.modules.chat.service`。"""

from app.modules.chat.service import generate_title, save_session, update_session_title

__all__ = ["generate_title", "save_session", "update_session_title"]
