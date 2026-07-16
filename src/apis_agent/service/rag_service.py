import logging

from src.apis_agent.common.langfuse_client import observe
from src.apis_agent.rag.retrieval_pipeline import build_context_enhanced
from src.apis_agent.service.embedding_service import embed_query, embedding_available
from src.apis_agent.storage.vector_store import vector_store

logger = logging.getLogger("apis")


@observe(name="rag.retrieve")
def retrieve(file_id: str, query: str, top_k: int = 5) -> list[dict]:
    """简单单路检索（向后兼容）。新代码建议使用 build_context。"""
    if not embedding_available() or not vector_store.ready:
        return []
    q_vec = embed_query(query)
    if not q_vec:
        return []
    results = vector_store.search(q_vec, top_k=top_k)
    return [r for r in results if r.get("file_id") == file_id]


@observe(name="rag.build_context")
async def build_context(query: str, file_id: str, full_text: str, top_k: int = 5) -> str:
    """构建 RAG 上下文。使用增强检索管线（查询重写 + 多路召回 + RRF + 动态裁剪）。"""
    return await build_context_enhanced(query, file_id, full_text, top_k)
