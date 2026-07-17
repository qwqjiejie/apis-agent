"""兼容导出；新代码使用 app.modules.documents.chunking。"""

from app.modules.documents.chunking import CHUNK_OVERLAP, CHUNK_SIZE, split_text

__all__ = ["CHUNK_OVERLAP", "CHUNK_SIZE", "split_text"]
