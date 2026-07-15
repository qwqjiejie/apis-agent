import asyncio
import json
import re
import time
from dataclasses import dataclass, field

from langchain.agents import create_agent

from src.dodo_agent.agent.base_agent import BaseAgent
from src.dodo_agent.common.llm import build_llm
from src.dodo_agent.common.logger import logger
from src.dodo_agent.common.streaming import AgentStopped, make_event, make_sse
from src.dodo_agent.common.tag_parser import StreamingTagParser
from src.dodo_agent.config.settings import settings
from src.dodo_agent.tool.tavily_search import tavily_search

THINK_PATTERN = re.compile(r"<think>(.*?)</think>", re.DOTALL)


# =============================================================================
# 数据结构
# =============================================================================

@dataclass
class ResearchTask:
    """单个研究子任务"""
    id: str       # 任务编号
    order: int    # 执行顺序（同 order 可并行，不同 order 串行）
    title: str    # 任务标题
    query: str    # 搜索查询语句


@dataclass
class TaskResult:
    """子任务执行结果"""
    task_id: str
    title: str
    order: int
    full_text: str = ""
    references: list = field(default_factory=list)

    @property
    def summary(self) -> str:
        return self.full_text[:500].replace("\n", " ")


# =============================================================================
# LLM Prompts — 四阶段提示词
# =============================================================================

CLARIFY_PROMPT = """你是一个专业的研究规划助手。分析用户的研究问题，明确研究目标和范围。

输出格式：
## 研究目标
（一句话概括核心研究问题）

## 研究范围
（明确什么需要研究、什么不需要）

## 关键子问题
1. 子问题1
2. 子问题2
3. 子问题3
（3-5个需要回答的关键子问题）

注意：如果用户的问题已经很具体清晰，直接拆解即可，不需要重复用户的话。"""

PLANNING_PROMPT = """你是一个研究规划专家。根据研究目标，生成一个结构化的研究计划。

每个子研究任务必须包含：
- id: 序号字符串
- order: 执行顺序编号（从1开始，同order的任务可并行执行，不同order的任务串行执行）
- title: 任务简短标题
- query: 具体的搜索查询语句

输出严格的JSON格式，不要包含其他文字：
```json
{{
  "topics": [
    {{"id": "1", "order": 1, "title": "任务标题", "query": "具体的搜索查询"}},
    {{"id": "2", "order": 1, "title": "另一个任务", "query": "另一个搜索查询"}}
  ]
}}
```

规则：
1. 大多数任务order应为1（可并行），仅当任务B明显依赖任务A的结果时才将B设为更高order
2. query要具体，适合直接用于搜索引擎
3. 总共{max_topics}个以内的子任务"""

SUB_TASK_PROMPT = """你是一个深度研究助手，正在执行一个具体的子研究任务。

请针对研究查询进行深入搜索和分析，要求：
1. 至少进行2-3次不同角度的搜索
2. 对信息进行综合分析，不要简单罗列搜索结果
3. 提取关键事实、数据和观点
4. 在回答末尾列出参考来源（标题 + URL）
5. 输出使用中文，清晰准确、有条理"""

CRITIQUE_PROMPT = """你是一个严谨的研究审核专家。评估当前研究结果是否充分回答了原始问题。

原始问题：{query}

已完成的子研究：
{results_summary}

请判断：
1. 信息是否充分？有哪些重要方面没有覆盖？
2. 是否存在矛盾或需要验证的信息？
3. 是否需要补充新的研究角度？

输出格式：
如果信息充分，输出一行：SUFFICIENT
如果信息不足，输出：
INSUFFICIENT
补充研究主题：
```json
{{"topics": [{{"id": "s1", "order": 1, "title": "...", "query": "..."}}]}}
```"""

SYNTHESIS_PROMPT = """你是一个专业的研究报告撰写专家。根据以下研究结果，撰写一份结构化的深度研究报告。

要求：
1. 结构清晰：摘要 -> 分章节论述 -> 结论
2. 引用具体数据和事实，标注来源
3. 对比不同观点，给出平衡分析
4. 使用中文，专业、客观
5. 报告末尾输出 <recommend>["推荐问题1", "推荐问题2", "推荐问题3"]</recommend>

研究结果：
{all_results}"""


# =============================================================================
# 工具函数
# =============================================================================

def _parse_topics_json(text: str) -> list[dict]:
    """从 LLM 输出中解析 JSON topics 数组。两层容错：
    1. 优先匹配 ```json ... ``` 代码块
    2. 回退到匹配包含 "topics" 键的 JSON 对象
    """
    json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    json_str = json_match.group(1) if json_match else text

    try:
        data = json.loads(json_str)
        return data.get("topics", [])
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\"topics\".*\}", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            return data.get("topics", [])
        except json.JSONDecodeError:
            pass

    return []


def _group_by_order(tasks: list[ResearchTask]) -> dict[int, list[ResearchTask]]:
    """按 order 分组：同 order 的任务可并行执行。"""
    grouped: dict[int, list[ResearchTask]] = {}
    for t in tasks:
        grouped.setdefault(t.order, []).append(t)
    return grouped


def _build_previous_context(results: list[TaskResult], order: int) -> str:
    """构建前序研究上下文：汇总所有 order < 当前 order 的结果摘要。
    这使得后续 order 的任务可以利用前序研究的发现，实现串行依赖传递。
    """
    relevant = [r for r in results if r.order < order]
    if not relevant:
        return ""
    parts = [f"### {r.title}\n{r.summary}" for r in relevant]
    return "前序研究发现：\n\n" + "\n\n".join(parts)


def _extract_references(output: str) -> list[dict]:
    """从工具输出中提取 SOURCES 字段的参考来源列表。"""
    if "SOURCES:" not in output:
        return []
    try:
        sources_str = output.split("SOURCES: ", 1)[1]
        if "\n\nDETAILS:" in sources_str:
            sources_str = sources_str.split("\n\nDETAILS:")[0]
        refs = json.loads(sources_str)
        if isinstance(refs, list):
            return refs
    except Exception:
        pass
    return []


def _results_summary_for_critique(results: list[TaskResult]) -> str:
    """将已完成的子研究结果格式化为 Critique 阶段的输入。"""
    parts = []
    for r in results:
        parts.append(f"【{r.title}】{r.summary}\n  来源数: {len(r.references)}")
    return "\n".join(parts)


def _results_full_for_synthesis(results: list[TaskResult]) -> str:
    """将所有研究结果格式化为 Synthesis 阶段的完整输入。"""
    parts = []
    for i, r in enumerate(results, 1):
        sources = "\n".join(
            f"  - {s.get('title', '')}: {s.get('url', '')}" for s in r.references[:5]
        )
        parts.append(f"### 研究{i}: {r.title}\n\n{r.full_text}\n\n参考来源:\n{sources or '无'}")
    return "\n\n---\n\n".join(parts)


# =============================================================================
# DeepResearchAgent — Plan-Execute-Critique 多轮自主研究
# =============================================================================

class DeepResearchAgent(BaseAgent):
    """深度研究 Agent，四阶段流程：

    Phase 1 — 需求澄清：分析用户问题，明确研究目标和范围
    Phase 2 — 研究规划：拆解为结构化子任务，按 order 编排并行/串行依赖
    Phase 3 — 执行+批判循环：
        - 按 order 分组并行执行子任务（Semaphore 控制并发上限）
        - 每轮执行完后 Critique 评估充分度，不充分则补充新主题
        - 最多迭代 max_iterations 轮
    Phase 4 — 综合报告：整合所有研究结果，生成结构化报告
    """

    async def run(self):
        """四阶段深度研究流程。"""
        # ---- 获取锁并注册任务 ----
        ok, error_events = await self._try_start()
        if not ok:
            for evt in error_events:
                yield evt
            return

        llm = build_llm()
        all_results: list[TaskResult] = []
        # 信号量控制子任务并发上限
        semaphore = asyncio.Semaphore(settings.deep_research_max_concurrency)

        try:
            # ================================================================
            # Phase 1: 需求澄清 — 分析研究问题
            # ================================================================
            yield make_event("phase_start", phase="clarify", content="正在分析研究问题...")
            objective = await self._phase_clarify(llm)
            yield make_event("thinking", content=objective)
            yield make_event("phase_end", phase="clarify", content=objective[:200])
            logger.info(f"[DeepResearch] Phase 1 完成, conversation={self.conversation_id}")

            # ================================================================
            # Phase 2: 研究规划 — 拆解子主题
            # ================================================================
            yield make_event("phase_start", phase="planning", content="正在拆解研究主题...")
            topics = await self._phase_planning(llm, objective)
            yield make_event("plan", steps=[
                {"id": t.id, "order": t.order, "title": t.title, "query": t.query}
                for t in topics
            ])
            yield make_event("phase_end", phase="planning",
                             content=f"已生成 {len(topics)} 个研究子主题")
            logger.info(f"[DeepResearch] Phase 2 完成, {len(topics)} 个主题")

            # ================================================================
            # Phase 3: 执行+批判循环
            # ================================================================
            current_topics = topics
            for iteration in range(1, settings.deep_research_max_iterations + 1):
                if self.cancel_event.is_set():
                    raise AgentStopped

                # --- 执行本轮子任务 ---
                yield make_event("phase_start", phase="execute", iteration=iteration,
                                 content=f"第 {iteration} 轮研究执行中...")

                grouped = _group_by_order(current_topics)
                round_results: list[TaskResult] = []

                # 按 order 升序执行：同 order 并行，不同 order 串行
                for order in sorted(grouped.keys()):
                    if self.cancel_event.is_set():
                        raise AgentStopped

                    tasks = grouped[order]
                    # 构建前序研究上下文，供后续依赖任务使用
                    prev_ctx = _build_previous_context(all_results + round_results, order)

                    async for event in self._execute_order_group(
                        tasks, llm, prev_ctx, semaphore, round_results,
                    ):
                        yield event

                all_results.extend(round_results)
                yield make_event("phase_end", phase="execute", iteration=iteration,
                                 content=f"第 {iteration} 轮执行完成，已收集 {len(all_results)} 个研究结果")

                # --- 批判评估：判断是否需要补充研究 ---
                if iteration < settings.deep_research_max_iterations:
                    if not all_results:
                        break

                    yield make_event("phase_start", phase="critique",
                                     content="正在评估研究充分度...")
                    critique = await self._phase_critique(llm, all_results)
                    yield make_event("critique", decision=critique["decision"],
                                     content=critique.get("content", ""))

                    if critique["decision"] == "SUFFICIENT":
                        logger.info(f"[DeepResearch] 研究充分，共 {iteration} 轮")
                        break

                    new_topics = critique.get("new_topics", [])
                    if not new_topics:
                        break
                    # 生成新的 ResearchTask 列表，进入下一轮
                    current_topics = [
                        ResearchTask(
                            id=t.get("id", f"s{i}"),
                            order=int(t.get("order", 1)),
                            title=str(t.get("title", "")),
                            query=str(t.get("query", "")),
                        )
                        for i, t in enumerate(new_topics[:settings.deep_research_max_sub_tasks])
                    ]
                else:
                    yield make_event("critique", decision="MAX_ROUNDS",
                                     content=f"已达最大迭代次数 {settings.deep_research_max_iterations}")
                    break

            # ================================================================
            # Phase 4: 综合报告 — 整合所有研究结果
            # ================================================================
            if not all_results:
                yield make_sse(json.dumps({"type": "error", "content": "研究未获得有效结果"}, ensure_ascii=False))
                yield make_sse(json.dumps({"type": "complete"}, ensure_ascii=False))
                yield make_sse("[DONE]")
                return

            synthesis_result: dict = {}
            async for event in self._phase_synthesize(llm, all_results, synthesis_result):
                yield event

            # ---- 持久化消息 ----
            self._save_message(
                answer=synthesis_result.get("final_text", ""),
                thinking="\n".join(synthesis_result.get("thinking_parts", [])),
                references=json.dumps(synthesis_result.get("references", []), ensure_ascii=False),
                recommend=synthesis_result.get("recommend_json", "[]"),
                tools="tavily_search",
                agent_type="deep",
            )

        except AgentStopped:
            yield make_sse(json.dumps({"type": "error", "content": "用户已停止"}, ensure_ascii=False))
            yield make_sse(json.dumps({"type": "complete"}, ensure_ascii=False))
            yield make_sse("[DONE]")
            return
        except Exception as e:
            logger.error(f"深度研究异常: {e}")
            yield make_sse(json.dumps({"type": "error", "content": str(e)}, ensure_ascii=False))
        finally:
            await self._cleanup()

        total_ms = int((time.time() - self._start_time) * 1000)
        logger.info(f"[DeepResearch] 完成, 耗时={total_ms}ms, 研究数={len(all_results)}")

        yield make_event("recommend", content=synthesis_result.get("recommend_json", "[]"))
        yield make_sse(json.dumps({"type": "complete"}, ensure_ascii=False))
        yield make_sse("[DONE]")

    # =========================================================================
    # Phase 实现
    # =========================================================================

    async def _phase_clarify(self, llm) -> str:
        """Phase 1: 调用 LLM 分析研究问题，返回结构化的研究目标文本。"""
        response = await llm.ainvoke([
            ("system", CLARIFY_PROMPT),
            ("user", self.query),
        ])
        return response.content if hasattr(response, "content") else str(response)

    async def _phase_planning(self, llm, objective: str) -> list[ResearchTask]:
        """Phase 2: 根据研究目标拆解为子任务列表，JSON 解析失败时回退为单任务模式。"""
        prompt = PLANNING_PROMPT.format(max_topics=settings.deep_research_max_sub_tasks)
        response = await llm.ainvoke([
            ("system", prompt),
            ("user", f"研究目标：\n{objective}\n\n原始问题：{self.query}"),
        ])
        text = response.content if hasattr(response, "content") else str(response)
        topics_data = _parse_topics_json(text)

        if not topics_data:
            logger.warning("计划生成JSON解析失败，回退到单任务模式")
            return [ResearchTask(id="1", order=1, title="综合研究", query=self.query)]

        tasks = []
        for t in topics_data[:settings.deep_research_max_sub_tasks]:
            tasks.append(ResearchTask(
                id=str(t.get("id", len(tasks) + 1)),
                order=int(t.get("order", 1)),
                title=str(t.get("title", t.get("query", ""))),
                query=str(t.get("query", "")),
            ))
        return tasks

    async def _phase_critique(self, llm, results: list[TaskResult]) -> dict:
        """Phase 3-Critique: 审核当前研究结果是否充分，不充分则返回补充主题。"""
        summary = _results_summary_for_critique(results)
        response = await llm.ainvoke([
            ("system", CRITIQUE_PROMPT.format(query=self.query, results_summary=summary)),
        ])
        text = response.content if hasattr(response, "content") else str(response)

        if text.strip().upper().startswith("SUFFICIENT"):
            return {"decision": "SUFFICIENT"}

        new_topics = _parse_topics_json(text)
        return {
            "decision": "INSUFFICIENT",
            "content": text[:300],
            "new_topics": new_topics,
        }

    async def _phase_synthesize(self, llm, results: list[TaskResult], result: dict):
        """Phase 4: 流式生成综合研究报告，使用 StreamingTagParser 解析 think/recommend 标签。"""
        all_text = _results_full_for_synthesis(results)
        tag_parser = StreamingTagParser()
        references: list[dict] = []
        seen_urls: set[str] = set()

        # 合并所有子任务的参考来源，按 URL 去重
        for r in results:
            for ref in r.references:
                url = ref.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    references.append(ref)

        yield make_event("phase_start", phase="synthesis", content="正在生成综合研究报告...")

        try:
            async for chunk in llm.astream([
                ("system", SYNTHESIS_PROMPT.format(all_results=all_text)),
                ("user", f"原始问题：{self.query}\n\n请基于以上研究结果撰写深度研究报告。"),
            ]):
                # 提取 reasoning_content（DeepSeek 等模型原生思考）
                reasoning = None
                if hasattr(chunk, "additional_kwargs") and chunk.additional_kwargs:
                    reasoning = chunk.additional_kwargs.get("reasoning_content", "")
                if reasoning:
                    tag_parser.thinking_parts.append(reasoning)
                    yield make_event("thinking", content=reasoning)

                content_text = chunk.content if hasattr(chunk, "content") and chunk.content else ""
                if not content_text:
                    continue

                # 流式标签解析
                events = tag_parser.feed(content_text)
                for evt_type, evt_content in events:
                    yield make_event(evt_type, content=evt_content)
        except asyncio.CancelledError:
            raise AgentStopped

        # flush 残留 buffer
        flush_events = tag_parser.flush()
        for evt_type, evt_content in flush_events:
            yield make_event(evt_type, content=evt_content)

        tag_parser.finalize()

        if references:
            yield make_event("reference", content=references)

        yield make_event("phase_end", phase="synthesis")

        # 将结果写回 result dict，供 run() 方法保存
        result.update({
            "final_text": tag_parser.full_text,
            "thinking_parts": tag_parser.thinking_parts,
            "references": references,
            "recommend_json": tag_parser.recommend_json,
        })

    # =========================================================================
    # 子任务执行 — 并行编排
    # =========================================================================

    async def _run_sub_task(
        self, task: ResearchTask, llm, previous_context: str,
        event_queue: asyncio.Queue, semaphore: asyncio.Semaphore,
    ):
        """执行单个子研究任务。

        每个子任务内部是一个独立的 ReAct Agent（仅搜索工具），
        通过 event_queue 将执行事件发送回主循环。

        Semaphore 控制同时执行的子任务数，防止工具调用过载。
        """
        async with semaphore:
            # 检查取消信号
            if self.cancel_event.is_set():
                await event_queue.put(make_event("task_end", taskId=task.id, status="cancelled"))
                await event_queue.put({"_done": True, "task_id": task.id, "result": None})
                return

            await event_queue.put(make_event(
                "task_start", taskId=task.id, title=task.title, order=task.order,
            ))

            # 为每个子任务创建独立的 ReAct Agent
            agent = create_agent(llm, [tavily_search], system_prompt=SUB_TASK_PROMPT)
            query_text = task.query
            if previous_context:
                # 注入前序研究上下文，实现跨 order 的知识传递
                query_text = f"{previous_context}\n\n当前研究任务：{task.query}"

            inputs = {"messages": [("user", query_text)]}

            full_text = ""
            references: list[dict] = []

            try:
                async for chunk in agent.astream_events(inputs, version="v2"):
                    if self.cancel_event.is_set():
                        break

                    kind = chunk["event"]

                    if kind == "on_tool_start":
                        await event_queue.put(make_event(
                            "tool_start", taskId=task.id,
                            toolName=chunk.get("name", ""),
                            toolCallId=chunk.get("run_id", ""),
                        ))

                    elif kind == "on_tool_end":
                        await event_queue.put(make_event(
                            "tool_end", taskId=task.id,
                            toolName=chunk.get("name", ""),
                            toolCallId=chunk.get("run_id", ""),
                        ))
                        output = chunk.get("data", {}).get("output", "")
                        if isinstance(output, str):
                            refs = _extract_references(output)
                            for ref in refs:
                                if ref not in references:
                                    references.append(ref)
                            if refs:
                                await event_queue.put(make_event(
                                    "reference", taskId=task.id, content=refs,
                                ))

                    elif kind == "on_chat_model_stream":
                        data = chunk.get("data", {})
                        chunk_obj = data.get("chunk", "")

                        reasoning = None
                        if hasattr(chunk_obj, "additional_kwargs") and chunk_obj.additional_kwargs:
                            reasoning = chunk_obj.additional_kwargs.get("reasoning_content", "")
                        if reasoning:
                            await event_queue.put(make_event(
                                "thinking", taskId=task.id, content=reasoning,
                            ))

                        content_text = chunk_obj.content if hasattr(chunk_obj, "content") and chunk_obj.content else ""
                        if content_text:
                            full_text += content_text
                            await event_queue.put(make_event(
                                "text", taskId=task.id, content=content_text,
                            ))

                task_result = TaskResult(
                    task_id=task.id,
                    title=task.title,
                    order=task.order,
                    full_text=full_text,
                    references=references,
                )

            except asyncio.CancelledError:
                await event_queue.put(make_event("task_end", taskId=task.id, status="cancelled"))
                await event_queue.put({"_done": True, "task_id": task.id, "result": None})
                return
            except Exception as e:
                logger.error(f"子任务 {task.id} ({task.title}) 失败: {e}")
                await event_queue.put(make_event(
                    "task_end", taskId=task.id, status="error", error=str(e),
                ))
                await event_queue.put({"_done": True, "task_id": task.id, "result": None})
                return

            # 发送完成信号
            await event_queue.put(make_event(
                "task_end", taskId=task.id,
                summary=task_result.summary,
                sources=task_result.references,
            ))
            await event_queue.put({"_done": True, "task_id": task.id, "result": task_result})

    async def _execute_order_group(
        self, tasks: list[ResearchTask], llm, previous_context: str,
        semaphore: asyncio.Semaphore, results_out: list[TaskResult],
    ):
        """并行执行同一 order 的所有子任务。

        为每个子任务创建独立的 asyncio.Task，通过 event_queue 收集事件。
        同时监听 cancel_event，支持整体取消。
        """
        event_queue: asyncio.Queue = asyncio.Queue()
        pending_count = len(tasks)

        # 并行启动所有子任务（Semaphore 控制实际并发数）
        for t in tasks:
            asyncio.create_task(self._run_sub_task(
                t, llm, previous_context, event_queue, semaphore,
            ))

        # 收集事件直到所有子任务完成
        while pending_count > 0:
            get_task = asyncio.create_task(event_queue.get())
            watch_task = asyncio.create_task(self.cancel_event.wait())

            done, _ = await asyncio.wait(
                [get_task, watch_task],
                return_when=asyncio.FIRST_COMPLETED,
            )

            # 取消信号 → 终止等待
            if watch_task in done:
                get_task.cancel()
                break

            watch_task.cancel()
            item = get_task.result()

            # _done 标记表示一个子任务结束
            if isinstance(item, dict) and item.get("_done"):
                pending_count -= 1
                if item.get("result"):
                    results_out.append(item["result"])
            else:
                yield item
