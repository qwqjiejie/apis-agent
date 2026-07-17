"""兼容导出；新代码使用 :mod:`app.modules.documents.rag`。"""

from app.modules.documents.rag import build_context, retrieve

__all__ = ["build_context", "retrieve"]
