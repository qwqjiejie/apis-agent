import logging

from src.dodo_agent.service.embedding_service import embed_query, embedding_available
from src.dodo_agent.storage.vector_store import vector_store

logger = logging.getLogger("dodo")


def retrieve(file_id: str, query: str, top_k: int = 5) -> list[dict]:
    if not embedding_available() or not vector_store.ready:
        return []
    q_vec = embed_query(query)
    if not q_vec:
        return []
    results = vector_store.search(q_vec, top_k=top_k)
    return [r for r in results if r.get("file_id") == file_id]


def build_context(query: str, file_id: str, full_text: str, top_k: int = 5) -> str:
    parts = []

    retrieved = retrieve(file_id, query, top_k)
    if retrieved:
        deduped = _dedup(retrieved)
        parts.append("【相关段落】")
        for i, r in enumerate(deduped, 1):
            parts.append(f"[{i}] {r['text']}")

    if full_text:
        text_snippet = full_text[:4000]
        parts.append("【文件全文摘要】")
        parts.append(text_snippet)

    return "\n\n".join(parts)


def _dedup(results: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for r in results:
        key = r["text"][:100]
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out
