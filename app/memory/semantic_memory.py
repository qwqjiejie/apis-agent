"""SemanticMemoryStore — 跨会话语义长期记忆。

基于向量相似度的记忆检索：
- 每轮对话后自动将 QA 对向量化存储
- 新会话时检索 TOP-K 相关历史记忆
- 检索结果注入 Agent 上下文，让 LLM "记住" 用户偏好和历史决策

后端支持：
- 内存模式（默认，无外部依赖）
- LangGraph PostgreSQL Store（生产模式，服务重启后可恢复）
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("apis")

_MEMORY_DIM = 1024
_DEFAULT_TOP_K = 5
_DEFAULT_THRESHOLD = 0.7
_MEMORY_NAMESPACE = ("semantic_memories",)
_MAX_MEMORIES_PER_USER = 200


class SemanticMemoryStore:
    """语义长期记忆存储。

    使用方式:
        store = SemanticMemoryStore()
        await store.add(user_id="u1", question="...", answer="...")
        memories = await store.search(user_id="u1", query="...", top_k=5)
    """

    def __init__(self, backend: str = "memory", top_k: int = _DEFAULT_TOP_K,
                 threshold: float = _DEFAULT_THRESHOLD):
        self._backend = backend
        self._top_k = top_k
        self._threshold = threshold
        self._memories: dict[str, list[dict]] = {}  # user_id → [{q, a, embedding}]
        self._store = None
        self._available = True

    def configure(self, store=None) -> None:
        """注入 LangGraph Store；为空时使用进程内降级存储。"""
        self._store = store
        self._backend = "pg" if store is not None else "memory"

    @property
    def available(self) -> bool:
        return self._available

    async def add(self, user_id: str, question: str, answer: str):
        """添加一条 QA 记忆。"""
        if not user_id or not question or not answer:
            return

        embedding = self._embed_text(question[:500])
        if embedding is None:
            return

        entry = {
            "id": f"memory_{uuid.uuid4().hex}",
            "user_id": user_id,
            "question": question[:500],
            "answer": answer[:500],
            "embedding": embedding,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        if self._store is not None:
            try:
                await self._store.aput(
                    _MEMORY_NAMESPACE + (user_id,),
                    entry["id"],
                    entry,
                    index=False,
                )
                await self._trim_pg_memories(user_id)
                logger.debug(f"[SemanticMemory] PG 已存储: user={user_id}")
                return
            except Exception as exc:
                logger.warning(f"[SemanticMemory] PG 写入失败，转内存降级: {exc}")

        if user_id not in self._memories:
            self._memories[user_id] = []
        self._memories[user_id].append(entry)

        if len(self._memories[user_id]) > _MAX_MEMORIES_PER_USER:
            self._memories[user_id] = self._memories[user_id][-_MAX_MEMORIES_PER_USER:]

        logger.debug(f"[SemanticMemory] 已存储: user={user_id}, total={len(self._memories[user_id])}")

    async def search(self, user_id: str, query: str, top_k: int = 0) -> list[dict]:
        """检索与 query 最相关的历史记忆。

        Returns:
            [{"question": "...", "answer": "...", "score": 0.95}, ...]
        """
        if not self._available or not user_id:
            return []

        k = top_k or self._top_k
        query_emb = self._embed_text(query[:500])
        if query_emb is None:
            return []

        entries = await self._load_entries(user_id)
        if not entries:
            return []

        scored = []
        for entry in entries:
            emb = entry.get("embedding")
            if emb is None:
                continue
            score = self._cosine_sim(query_emb, emb)
            if score >= self._threshold:
                scored.append({
                    "question": entry["question"],
                    "answer": entry["answer"],
                    "score": score,
                })

        scored.sort(key=lambda x: x["score"], reverse=True)
        result = scored[:k]

        if result:
            logger.debug(f"[SemanticMemory] 检索命中: user={user_id}, top={result[0]['score']:.3f}")
        return result

    async def _load_entries(self, user_id: str) -> list[dict]:
        entries: list[dict] = []
        if self._store is not None:
            try:
                items = await self._store.asearch(
                    _MEMORY_NAMESPACE + (user_id,),
                    limit=_MAX_MEMORIES_PER_USER,
                )
                entries.extend(
                    dict(item.value)
                    for item in items
                    if isinstance(item.value, dict)
                )
            except Exception as exc:
                logger.warning(f"[SemanticMemory] PG 读取失败，使用内存降级: {exc}")
        entries.extend(self._memories.get(user_id, []))
        return entries

    async def _trim_pg_memories(self, user_id: str) -> None:
        items = await self._store.asearch(
            _MEMORY_NAMESPACE + (user_id,),
            limit=_MAX_MEMORIES_PER_USER + 50,
        )
        if len(items) <= _MAX_MEMORIES_PER_USER:
            return
        ordered = sorted(
            items,
            key=lambda item: item.value.get("created_at", "")
            if isinstance(item.value, dict)
            else "",
        )
        for item in ordered[:-_MAX_MEMORIES_PER_USER]:
            key = getattr(item, "key", "")
            if key:
                await self._store.adelete(_MEMORY_NAMESPACE + (user_id,), key)

    async def delete_user(self, user_id: str) -> None:
        self._memories.pop(user_id, None)
        if self._store is None:
            return
        items = await self._store.asearch(
            _MEMORY_NAMESPACE + (user_id,),
            limit=_MAX_MEMORIES_PER_USER + 50,
        )
        for item in items:
            key = getattr(item, "key", "")
            if key:
                await self._store.adelete(_MEMORY_NAMESPACE + (user_id,), key)

    def build_context_injection(self, memories: list[dict]) -> str:
        """将检索到的记忆构建为可注入上下文的文本。"""
        if not memories:
            return ""
        parts = ["## 历史相关对话（供参考）", ""]
        for i, m in enumerate(memories, 1):
            parts.append(f"**{i}.** Q: {m['question'][:200]}")
            parts.append(f"   A: {m['answer'][:300]}")
            parts.append("")
        return "\n".join(parts)

    # ── 内部方法 ──────────────────────────────────

    def _embed_text(self, text: str) -> list[float] | None:
        """向量化文本。"""
        try:
            from app.service.embedding_service import embed_query
            return embed_query(text)
        except Exception as e:
            logger.warning(f"[SemanticMemory] embedding 失败: {e}")
            return None

    @staticmethod
    def _cosine_sim(a: list[float], b: list[float]) -> float:
        """计算余弦相似度。"""
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)
