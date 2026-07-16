"""SemanticMemoryStore — 跨会话语义长期记忆。

基于向量相似度的记忆检索：
- 每轮对话后自动将 QA 对向量化存储
- 新会话时检索 TOP-K 相关历史记忆
- 检索结果注入 Agent 上下文，让 LLM "记住" 用户偏好和历史决策

后端支持：
- 内存模式（默认，无外部依赖）
- PgVector 模式（需安装 pgvector 扩展，生产环境推荐）
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("apis")

_MEMORY_DIM = 1024
_DEFAULT_TOP_K = 5
_DEFAULT_THRESHOLD = 0.7


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
        self._vector_store = None  # PgVector 客户端（升级路径）
        self._available = backend == "memory"

    @property
    def available(self) -> bool:
        return self._available

    async def add(self, user_id: str, question: str, answer: str):
        """添加一条 QA 记忆。"""
        if not question or not answer:
            return

        embedding = self._embed_text(question[:500])
        if embedding is None:
            return

        entry = {
            "question": question[:500],
            "answer": answer[:500],
            "embedding": embedding,
        }

        if user_id not in self._memories:
            self._memories[user_id] = []
        self._memories[user_id].append(entry)

        # 限制每个用户最多 200 条记忆
        if len(self._memories[user_id]) > 200:
            self._memories[user_id] = self._memories[user_id][-200:]

        logger.debug(f"[SemanticMemory] 已存储: user={user_id}, total={len(self._memories[user_id])}")

    async def search(self, user_id: str, query: str, top_k: int = 0) -> list[dict]:
        """检索与 query 最相关的历史记忆。

        Returns:
            [{"question": "...", "answer": "...", "score": 0.95}, ...]
        """
        if not self._available or user_id not in self._memories:
            return []

        k = top_k or self._top_k
        query_emb = self._embed_text(query[:500])
        if query_emb is None:
            return []

        entries = self._memories.get(user_id, [])
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
            from src.apis_agent.service.embedding_service import embed_query
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


semantic_memory = SemanticMemoryStore()
