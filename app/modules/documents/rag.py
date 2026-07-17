from app.common.langfuse_client import observe
from app.modules.documents.retrieval import build_context_enhanced


@observe(name="rag.build_context")
async def build_context(query: str, file_id: str, full_text: str, top_k: int = 5) -> str:
    """构建 RAG 上下文。使用增强检索管线（查询重写 + 多路召回 + RRF + 动态裁剪）。"""
    return await build_context_enhanced(query, file_id, full_text, top_k)
