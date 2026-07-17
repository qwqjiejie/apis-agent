import asyncio
import logging
import re

from app.common.langfuse_client import observe
from app.common.llm import build_llm
from app.config.settings import get_settings
from app.modules.documents.embedding import embed_query, embedding_available
from app.infrastructure.milvus.vector_store import vector_store

logger = logging.getLogger("apis")

# =============================================================================
# QueryRewriter — LLM 自适应查询重写
# =============================================================================

REWRITE_PROMPT = """你是一个搜索查询优化助手。判断问题类型并生成搜索变体。

问题: {question}

判断规则:
- FACTUAL: 事实型，涉及具体数值、定义、流程等
- COMPLEX: 复杂型，涉及分析、总结、对比等
- SPECIFIC: 精确型，涉及特定文档/章节引用

输出格式（只输出以下内容）:
类型: <FACTUAL|COMPLEX|SPECIFIC>
变体:
<FACTUAL: 生成 2-3 个同义改写，每行一个>
<COMPLEX: 生成 0-1 个变体，保持精度>
<SPECIFIC: 不生成变体，此行留空>"""


class QueryRewriter:

    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    @observe(name="rag.rewrite")
    async def rewrite(self, question: str) -> list[str]:
        if not self.enabled or not question:
            return [question]
        try:
            llm = build_llm()
            response = await llm.ainvoke(REWRITE_PROMPT.format(question=question))
            text = response.content if hasattr(response, "content") else str(response)

            variants = []
            in_variants = False
            for line in text.split("\n"):
                line = line.strip()
                if line.startswith("变体:"):
                    in_variants = True
                    continue
                if in_variants and len(line) > 3:
                    variants.append(line)

            result = [question] + [v for v in variants if v.lower() != question.lower()]
            result = result[:3]

            logger.info(f"[QueryRewriter] {question[:50]} → {len(result)} 变体")
            return result
        except Exception as e:
            logger.warning(f"[QueryRewriter] 失败，回退原文: {e}")
            return [question]


# =============================================================================
# RRF 融合 — Reciprocal Rank Fusion
# =============================================================================

def _rrf_fusion(all_results: list[list[dict]], k: int = 60) -> list[dict]:
    """多路检索结果 RRF 融合。

    对每个查询变体的检索结果按排名加权，奖励在多个变体中稳定出现的文档。
    score = Σ 1/(k + rank)
    """
    rrf_scores: dict[str, dict] = {}
    for results in all_results:
        for rank, item in enumerate(results, 1):
            key = item.get("text", "")[:120]
            if key not in rrf_scores:
                rrf_scores[key] = {"item": item, "score": 0.0}
            rrf_scores[key]["score"] += 1.0 / (k + rank)

    sorted_items = sorted(rrf_scores.values(), key=lambda x: x["score"], reverse=True)
    return [x["item"] for x in sorted_items]


# =============================================================================
# DynamicTopK — 基于分数分布的自适应截断
# =============================================================================

def _dynamic_top_k(results: list[dict], top_k: int = 10, drop_ratio: float = 0.25,
                   min_score: float = 0.3, min_k: int = 1) -> list[dict]:
    """根据分数分布动态裁剪结果。

    1. 绝对阈值：分数低于 min_score 直接丢弃
    2. 相对截断：后续分数相对最高分降幅超过 drop_ratio 时截断
    """
    if not results:
        return []

    # 绝对分数过滤
    filtered = [r for r in results if r.get("score", 0) >= min_score]
    if not filtered:
        return results[:min_k]

    top_score = filtered[0].get("score", 0)
    if top_score <= 0:
        return filtered[:top_k]

    for i in range(1, len(filtered)):
        current = filtered[i].get("score", 0)
        if (top_score - current) / top_score > drop_ratio:
            return filtered[:max(i, min_k)][:top_k]

    return filtered[:top_k]


# =============================================================================
# LLM Relevance Filter — 过滤高分噪声
# =============================================================================

RELEVANCE_PROMPT = """判断以下文档片段是否与问题直接相关。只输出不相关的编号（如"3,5"），全相关输出"无"。

问题: {question}

{chunks}

不相关的编号:"""


async def _llm_relevance_filter(results: list[dict], question: str, max_check: int = 5) -> list[dict]:
    """用 LLM 过滤 Rerank 后分数高但不回答问题的片段。"""
    if len(results) <= 1:
        return results

    check_items = results[:max_check]
    chunks_text = "\n".join(
        f"[{i + 1}] {r['text'][:200]}" for i, r in enumerate(check_items)
    )

    try:
        llm = build_llm()
        response = await llm.ainvoke(RELEVANCE_PROMPT.format(question=question, chunks=chunks_text))
        raw = response.content if hasattr(response, "content") else str(response)
        raw = raw.strip()

        if raw in ("无", "none", "", "None"):
            return results

        irrelevant = {int(m.group()) - 1 for m in re.finditer(r"\d+", raw)
                      if 0 <= int(m.group()) - 1 < len(check_items)}
        if not irrelevant:
            return results

        filtered = [r for i, r in enumerate(results) if i not in irrelevant]
        logger.info(f"[RelevanceFilter] 过滤 {len(irrelevant)} 个不相关片段")
        return filtered
    except Exception as e:
        logger.warning(f"[RelevanceFilter] 失败，保留全部: {e}")
        return results


# =============================================================================
# RetrievalPipeline — 统一检索管线
# =============================================================================

class RetrievalPipeline:
    """检索增强管线：QueryRewriter → MultiRecall + RRF → DynamicTopK → RelevanceFilter。

    使用方式:
        pipeline = RetrievalPipeline()
        results = await pipeline.retrieve("查询", file_id="xxx")
    """

    def __init__(self):
        s = get_settings()
        self.rewriter = QueryRewriter(enabled=True)
        self.enable_fusion = True
        self.enable_relevance_filter = True
        self.top_k = s.deep_research_max_sub_tasks * 2 or 10
        self.drop_ratio = 0.25
        self.min_score = 0.3

    @observe(name="rag.pipeline.retrieve")
    async def retrieve(self, query: str, file_id: str, top_k: int = 5) -> list[dict]:
        """执行完整检索管线。"""
        if not embedding_available() or not vector_store.ready:
            return []

        # Step 1: 查询重写
        variants = await self.rewriter.rewrite(query)

        # Step 2: 多路并行检索
        if self.enable_fusion and len(variants) > 1:
            all_results = await self._multi_search(variants, top_k * 2)
            results = _rrf_fusion(all_results)
        else:
            q_vec = embed_query(query)
            if not q_vec:
                return []
            results = vector_store.search(q_vec, top_k=top_k * 2)

        if not results:
            return []

        # 过滤指定 file_id
        results = [r for r in results if r.get("file_id") == file_id]

        # Step 3: 动态 Top-K
        results = _dynamic_top_k(results, top_k=top_k, drop_ratio=self.drop_ratio,
                                 min_score=self.min_score)

        # Step 4: LLM 相关性过滤
        if self.enable_relevance_filter and len(results) > 1:
            results = await _llm_relevance_filter(results, query)

        logger.info(f"[RetrievalPipeline] {query[:40]} → {len(results)} 结果 ({len(variants)} 变体)")
        return results[:top_k]

    async def _multi_search(self, queries: list[str], top_k: int) -> list[list[dict]]:
        """并行执行多路向量检索。"""

        def _search_one(q: str) -> list[dict]:
            vec = embed_query(q)
            if not vec:
                return []
            return vector_store.search(vec, top_k=top_k)

        tasks = [asyncio.to_thread(_search_one, q) for q in queries]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [r for r in results if isinstance(r, list)]


# =============================================================================
# build_context — 构建文本上下文
# =============================================================================

_pipeline: RetrievalPipeline | None = None


def _get_pipeline() -> RetrievalPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = RetrievalPipeline()
    return _pipeline


@observe(name="rag.build_context")
async def build_context_enhanced(query: str, file_id: str, full_text: str, top_k: int = 5) -> str:
    """增强版上下文构建：使用 RetrievalPipeline 替代简单检索。"""
    parts = []

    pipeline = _get_pipeline()
    retrieved = await pipeline.retrieve(query, file_id, top_k)
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
