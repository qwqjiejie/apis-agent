"""GraphRAG — 知识图谱增强检索。

在向量检索基础上，通过 Neo4j 知识图谱补充关联实体和关系，
提升回答的完整性和准确性。

Neo4j 不可用时自动降级为空操作。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("apis")


class GraphRAGService:
    """知识图谱增强检索服务。"""

    def __init__(self, neo4j_manager=None):
        self._neo4j = neo4j_manager

    @property
    def available(self) -> bool:
        return self._neo4j is not None and self._neo4j.available

    async def extract_keywords(self, query: str, context: str = "",
                                max_keywords: int = 5) -> list[str]:
        """从查询和上下文中提取图谱搜索关键词。

        委托给 LLM 提取 + 启发式回退。
        """
        # 启发式：取 query 中 2-5 个字的连续片段
        keywords = []
        # 简单分词
        import re
        words = re.findall(r'[一-鿿\w]{2,6}', query)
        keywords = list(set(words))[:max_keywords]

        if not keywords and context:
            words = re.findall(r'[一-鿿\w]{2,6}', context[:500])
            keywords = list(set(words))[:max_keywords]

        # LLM 提取（如需要精确提取）
        if not keywords:
            return [query[:10]]

        return keywords

    async def graph_rag(self, keywords: list[str], question: str = "",
                        max_entities: int = 15, max_relations: int = 30,
                        max_hops: int = 2) -> GraphContext | None:
        """执行图谱增强检索。

        Args:
            keywords: 搜索关键词列表
            question: 用户原始问题（用于相关性过滤）
            max_entities: 最大实体数
            max_relations: 最大关系数
            max_hops: 最大跳数

        Returns:
            GraphContext 或 None（图谱不可用时）
        """
        if not self.available:
            return None

        all_results = []
        for kw in keywords[:3]:
            results = await self._neo4j.search_related(kw, max_hops=max_hops)
            all_results.extend(results)

        if not all_results:
            return None

        # 去重 + 限流
        seen = set()
        entities: list[dict] = []
        relations: list[dict] = []
        for r in all_results[:max_entities]:
            entity = str(r.get("entity", ""))
            if entity and entity not in seen:
                seen.add(entity)
                labels = r.get("labels", [])
                entities.append({"name": entity, "labels": labels})

                related = r.get("related_entity")
                if related and str(related) not in seen:
                    relations.append({
                        "from": entity,
                        "to": str(related),
                    })

        context = GraphContext(
            entities=entities[:max_entities],
            relations=relations[:max_relations],
        )

        if context.is_empty():
            return None

        logger.info(f"[GraphRAG] 检索到 {len(context.entities)} 实体, {len(context.relations)} 关系")
        return context

    def format_context(self, ctx) -> str:
        """将 GraphContext 格式化为 LLM 可读的文本。"""
        if ctx is None or ctx.is_empty():
            return ""

        lines = ["## 知识图谱上下文", ""]
        lines.append("### 相关实体")
        for e in ctx.entities[:10]:
            labels = ", ".join(e.get("labels", []))
            lines.append(f"- {e['name']} ({labels})")

        if ctx.relations:
            lines.append("")
            lines.append("### 实体关系")
            for r in ctx.relations[:15]:
                lines.append(f"- {r['from']} → {r['to']}")

        return "\n".join(lines)


class GraphContext:
    """图谱检索上下文。"""

    def __init__(self, entities: list[dict] | None = None,
                 relations: list[dict] | None = None):
        self.entities = entities or []
        self.relations = relations or []

    def is_empty(self) -> bool:
        return len(self.entities) == 0


graph_rag_service = GraphRAGService()
