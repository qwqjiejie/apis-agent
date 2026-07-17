"""文档向量化服务。"""

import logging

from openai import OpenAI

from app.config.settings import get_settings

logger = logging.getLogger("apis")

_available: bool | None = None
_dim: int | None = None


def embedding_available() -> bool:
    global _available
    if _available is None:
        try:
            client = _client()
            resp = client.embeddings.create(model=get_settings().embedding_model, input=["test"])
            global _dim
            _dim = len(resp.data[0].embedding)
            _available = True
            logger.info(f"Embedding 服务可用, model={get_settings().embedding_model}, dim={_dim}")
        except Exception as e:
            logger.warning(f"Embedding 不可用: {e}")
            _available = False
    return _available


def embedding_dim() -> int:
    if _available is None:
        embedding_available()
    return _dim or get_settings().embedding_dim


def _client() -> OpenAI:
    return OpenAI(api_key=get_settings().llm_api_key, base_url=get_settings().llm_base_url)


def embed_texts(texts: list[str]) -> list[list[float]] | None:
    if not texts:
        return []
    try:
        resp = _client().embeddings.create(model=get_settings().embedding_model, input=texts)
        return [d.embedding for d in resp.data]
    except Exception as e:
        logger.error(f"Embedding 失败: {e}")
        return None


def embed_query(text: str) -> list[float] | None:
    result = embed_texts([text])
    return result[0] if result else None
