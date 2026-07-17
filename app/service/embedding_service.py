"""兼容导出；新代码使用文档模块的 embedding 服务。"""

from app.modules.documents.embedding import (
    embed_query,
    embed_texts,
    embedding_available,
    embedding_dim,
)

__all__ = ["embed_query", "embed_texts", "embedding_available", "embedding_dim"]
