"""兼容导出；新代码使用 :mod:`app.infrastructure.milvus.vector_store`。"""

from app.infrastructure.milvus.vector_store import (
    COLLECTION_NAME,
    VectorStore,
    vector_store,
)

__all__ = ["COLLECTION_NAME", "VectorStore", "vector_store"]
