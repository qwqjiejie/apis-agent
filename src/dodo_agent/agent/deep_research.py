import asyncio
import json
import re
import time
from dataclasses import dataclass, field

from langchain.agents import create_agent

from src.dodo_agent.agent.react_agent import build_llm
from src.dodo_agent.api.session import store
from src.dodo_agent.common.logger import logger
from src.dodo_agent.common.redis import acquire_lock, listen_stop, release_lock
from src.dodo_agent.config.settings import settings
from src.dodo_agent.tool.tavily_search import tavily_search

THINK_PATTERN = re.compile(r"<think>(.*?)</think>", re.DOTALL)
RECOMMEND_PATTERN = re.compile(r"<recommend>(.*?)</recommend>", re.DOTALL)

_running_tasks: dict[str, asyncio.Event] = {}


class _AgentStopped(Exception):
    pass


# ---- data classes ----

@dataclass
class ResearchTask:
    id: str
    order: int
    title: str
    query: str


@dataclass
class TaskResult:
    task_id: str
    title: str
    order: int
    full_text: str = ""
    references: list = field(default_factory=list)

    @property
    def summary(self) -> str:
        return self.full_text[:500].replace("\n", " ")


# ---- prompts ----

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


# ---- helpers ----

def _make_event(event_type: str, **kwargs) -> dict:
    payload = {"type": event_type}
    payload.update(kwargs)
    return {"event": "message", "data": json.dumps(payload, ensure_ascii=False)}


def _make_sse(text: str) -> dict:
    return {"event": "message", "data": text}


def _parse_topics_json(text: str) -> list[dict]:
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
    grouped: dict[int, list[ResearchTask]] = {}
    for t in tasks:
        grouped.setdefault(t.order, []).append(t)
    return grouped


def _build_previous_context(results: list[TaskResult], order: int) -> str:
    relevant = [r for r in results if r.order < order]
    if not relevant:
        return ""
    parts = [f"### {r.title}\n{r.summary}" for r in relevant]
    return "前序研究发现：\n\n" + "\n\n".join(parts)


def _extract_references(output: str) -> list[dict]:
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
    parts = []
    for r in results:
        parts.append(f"【{r.title}】{r.summary}\n  来源数: {len(r.references)}")
    return "\n".join(parts)


def _results_full_for_synthesis(results: list[TaskResult]) -> str:
    parts = []
    for i, r in enumerate(results, 1):
        sources = "\n".join(
            f"  - {s.get('title', '')}: {s.get('url', '')}" for s in r.references[:5]
        )
        parts.append(f"### 研究{i}: {r.title}\n\n{r.full_text}\n\n参考来源:\n{sources or '无'}")
    return "\n\n---\n\n".join(parts)


# ---- Phase implementations ----

async def _phase_clarify(llm, query: str) -> str:
    response = await llm.ainvoke([
        ("system", CLARIFY_PROMPT),
        ("user", query),
    ])
    return response.content if hasattr(response, "content") else str(response)


async def _phase_planning(llm, query: str, objective: str) -> list[ResearchTask]:
    prompt = PLANNING_PROMPT.format(max_topics=settings.deep_research_max_sub_tasks)
    response = await llm.ainvoke([
        ("system", prompt),
        ("user", f"研究目标：\n{objective}\n\n原始问题：{query}"),
    ])
    text = response.content if hasattr(response, "content") else str(response)
    topics_data = _parse_topics_json(text)

    if not topics_data:
        logger.warning("计划生成JSON解析失败，回退到单任务模式")
        return [ResearchTask(id="1", order=1, title="综合研究", query=query)]

    tasks = []
    for t in topics_data[:settings.deep_research_max_sub_tasks]:
        tasks.append(ResearchTask(
            id=str(t.get("id", len(tasks) + 1)),
            order=int(t.get("order", 1)),
            title=str(t.get("title", t.get("query", ""))),
            query=str(t.get("query", "")),
        ))
    return tasks


async def _phase_critique(llm, query: str, results: list[TaskResult]) -> dict:
    summary = _results_summary_for_critique(results)
    response = await llm.ainvoke([
        ("system", CRITIQUE_PROMPT.format(query=query, results_summary=summary)),
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


async def _phase_synthesize(llm, query: str, results: list[TaskResult], result: dict):
    all_text = _results_full_for_synthesis(results)
    full_text = ""
    thinking_parts: list[str] = []
    references: list[dict] = []
    seen_urls: set[str] = set()
    think_buffer = ""
    recommend_buffer = ""
    recommend_json = ""

    for r in results:
        for ref in r.references:
            url = ref.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                references.append(ref)

    yield _make_event("phase_start", phase="synthesis", content="正在生成综合研究报告...")

    try:
        async for chunk in llm.astream([
            ("system", SYNTHESIS_PROMPT.format(all_results=all_text)),
            ("user", f"原始问题：{query}\n\n请基于以上研究结果撰写深度研究报告。"),
        ]):
            reasoning = None
            if hasattr(chunk, "additional_kwargs") and chunk.additional_kwargs:
                reasoning = chunk.additional_kwargs.get("reasoning_content", "")
            if reasoning:
                thinking_parts.append(reasoning)
                yield _make_event("thinking", content=reasoning)

            content_text = chunk.content if hasattr(chunk, "content") and chunk.content else ""
            if not content_text:
                continue

            think_buffer += content_text
            while True:
                m = THINK_PATTERN.search(think_buffer)
                if not m:
                    break
                think_content = m.group(1)
                if think_content.strip():
                    thinking_parts.append(think_content)
                    yield _make_event("thinking", content=think_content)
                think_buffer = think_buffer[:m.start()] + think_buffer[m.end():]

            if "<think>" in think_buffer:
                tag_pos = think_buffer.rfind("<think>")
                text_part = think_buffer[:tag_pos]
                if text_part:
                    full_text += text_part
                    yield _make_event("text", content=text_part)
                think_buffer = think_buffer[tag_pos:]
            else:
                if think_buffer:
                    if recommend_buffer:
                        recommend_buffer += think_buffer
                    elif "<recommend" in think_buffer:
                        idx = think_buffer.find("<recommend")
                        text_part = think_buffer[:idx]
                        if text_part:
                            full_text += text_part
                            yield _make_event("text", content=text_part)
                        recommend_buffer += think_buffer[idx:]
                    else:
                        full_text += think_buffer
                        yield _make_event("text", content=think_buffer)
                think_buffer = ""
                if recommend_buffer and "</recommend>" in recommend_buffer:
                    m = RECOMMEND_PATTERN.search(recommend_buffer)
                    if m:
                        recommend_json = m.group(1).strip()
                    recommend_buffer = ""
    except asyncio.CancelledError:
        raise _AgentStopped

    if recommend_buffer:
        m = RECOMMEND_PATTERN.search(recommend_buffer)
        if m and not recommend_json:
            recommend_json = m.group(1).strip()

    if think_buffer.strip():
        clean = THINK_PATTERN.sub("", think_buffer).strip()
        if clean:
            full_text += clean
            yield _make_event("text", content=clean)

    if not recommend_json:
        m = RECOMMEND_PATTERN.search(full_text)
        if m:
            recommend_json = m.group(1).strip()
            full_text = RECOMMEND_PATTERN.sub("", full_text).strip()
    if not recommend_json:
        recommend_json = "[]"

    if references:
        yield _make_event("reference", content=references)

    yield _make_event("phase_end", phase="synthesis")

    result.update({
        "final_text": full_text,
        "thinking_parts": thinking_parts,
        "references": references,
        "recommend_json": recommend_json,
    })


# ---- sub-task execution ----

async def _run_sub_task(
    task: ResearchTask,
    llm,
    previous_context: str,
    event_queue: asyncio.Queue,
    cancel_event: asyncio.Event,
    semaphore: asyncio.Semaphore,
):
    async with semaphore:
        if cancel_event.is_set():
            await event_queue.put(_make_event("task_end", taskId=task.id, status="cancelled"))
            await event_queue.put({"_done": True, "task_id": task.id, "result": None})
            return

        await event_queue.put(_make_event(
            "task_start", taskId=task.id, title=task.title, order=task.order,
        ))

        agent = create_agent(llm, [tavily_search], system_prompt=SUB_TASK_PROMPT)
        query_text = task.query
        if previous_context:
            query_text = f"{previous_context}\n\n当前研究任务：{task.query}"

        inputs = {"messages": [("user", query_text)]}

        full_text = ""
        references: list[dict] = []

        try:
            async for chunk in agent.astream_events(inputs, version="v2"):
                if cancel_event.is_set():
                    break

                kind = chunk["event"]

                if kind == "on_tool_start":
                    await event_queue.put(_make_event(
                        "tool_start", taskId=task.id,
                        toolName=chunk.get("name", ""),
                        toolCallId=chunk.get("run_id", ""),
                    ))

                elif kind == "on_tool_end":
                    await event_queue.put(_make_event(
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
                            await event_queue.put(_make_event(
                                "reference", taskId=task.id, content=refs,
                            ))

                elif kind == "on_chat_model_stream":
                    data = chunk.get("data", {})
                    chunk_obj = data.get("chunk", "")

                    reasoning = None
                    if hasattr(chunk_obj, "additional_kwargs") and chunk_obj.additional_kwargs:
                        reasoning = chunk_obj.additional_kwargs.get("reasoning_content", "")
                    if reasoning:
                        await event_queue.put(_make_event(
                            "thinking", taskId=task.id, content=reasoning,
                        ))

                    content_text = chunk_obj.content if hasattr(chunk_obj, "content") and chunk_obj.content else ""
                    if content_text:
                        full_text += content_text
                        await event_queue.put(_make_event(
                            "text", taskId=task.id, content=content_text,
                        ))

            result = TaskResult(
                task_id=task.id,
                title=task.title,
                order=task.order,
                full_text=full_text,
                references=references,
            )

        except asyncio.CancelledError:
            await event_queue.put(_make_event("task_end", taskId=task.id, status="cancelled"))
            await event_queue.put({"_done": True, "task_id": task.id, "result": None})
            return
        except Exception as e:
            logger.error(f"子任务 {task.id} ({task.title}) 失败: {e}")
            await event_queue.put(_make_event(
                "task_end", taskId=task.id, status="error", error=str(e),
            ))
            await event_queue.put({"_done": True, "task_id": task.id, "result": None})
            return

        await event_queue.put(_make_event(
            "task_end", taskId=task.id,
            summary=result.summary,
            sources=result.references,
        ))
        await event_queue.put({"_done": True, "task_id": task.id, "result": result})


async def _execute_order_group(
    tasks: list[ResearchTask],
    llm,
    previous_context: str,
    cancel_event: asyncio.Event,
    semaphore: asyncio.Semaphore,
    results_out: list[TaskResult],
):
    event_queue: asyncio.Queue = asyncio.Queue()
    pending_count = len(tasks)

    for t in tasks:
        asyncio.create_task(_run_sub_task(
            t, llm, previous_context, event_queue, cancel_event, semaphore,
        ))

    while pending_count > 0:
        get_task = asyncio.create_task(event_queue.get())
        watch_task = asyncio.create_task(cancel_event.wait())

        done, _ = await asyncio.wait(
            [get_task, watch_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        if watch_task in done:
            get_task.cancel()
            break

        watch_task.cancel()
        item = get_task.result()

        if isinstance(item, dict) and item.get("_done"):
            pending_count -= 1
            if item.get("result"):
                results_out.append(item["result"])
        else:
            yield item


# ---- main stream ----

async def deep_research_stream(conversation_id: str, query: str, file_id: str = ""):
    lock_ok = await acquire_lock(conversation_id, ttl=settings.task_lock_timeout_seconds)
    if not lock_ok:
        yield _make_sse(json.dumps({"type": "error", "content": "当前会话有任务正在执行中，请稍后再试"}, ensure_ascii=False))
        yield _make_sse(json.dumps({"type": "complete"}, ensure_ascii=False))
        yield _make_sse("[DONE]")
        return

    if conversation_id in _running_tasks:
        await release_lock(conversation_id)
        yield _make_sse(json.dumps({"type": "error", "content": "当前会话有任务正在执行中，请稍后再试"}, ensure_ascii=False))
        yield _make_sse(json.dumps({"type": "complete"}, ensure_ascii=False))
        yield _make_sse("[DONE]")
        return

    cancel_event = asyncio.Event()
    _running_tasks[conversation_id] = cancel_event
    redis_listener_task = asyncio.create_task(listen_stop(conversation_id, cancel_event))
    semaphore = asyncio.Semaphore(settings.deep_research_max_concurrency)

    llm = build_llm()
    t0 = time.time()
    all_results: list[TaskResult] = []
    final_text = ""
    thinking_parts: list[str] = []
    references: list[dict] = []
    recommend_json = "[]"

    try:
        # Phase 1: Clarify
        yield _make_event("phase_start", phase="clarify", content="正在分析研究问题...")
        objective = await _phase_clarify(llm, query)
        yield _make_event("thinking", content=objective)
        yield _make_event("phase_end", phase="clarify", content=objective[:200])
        logger.info(f"[DeepResearch] Phase 1 完成, conversation={conversation_id}")

        # Phase 2: Planning
        yield _make_event("phase_start", phase="planning", content="正在拆解研究主题...")
        topics = await _phase_planning(llm, query, objective)
        yield _make_event("plan", steps=[
            {"id": t.id, "order": t.order, "title": t.title, "query": t.query}
            for t in topics
        ])
        yield _make_event("phase_end", phase="planning",
                          content=f"已生成 {len(topics)} 个研究子主题")
        logger.info(f"[DeepResearch] Phase 2 完成, {len(topics)} 个主题")

        # Phase 3: Execute-Critique loop
        current_topics = topics

        for iteration in range(1, settings.deep_research_max_iterations + 1):
            if cancel_event.is_set():
                raise _AgentStopped

            yield _make_event("phase_start", phase="execute", iteration=iteration,
                              content=f"第 {iteration} 轮研究执行中...")

            grouped = _group_by_order(current_topics)
            round_results: list[TaskResult] = []

            for order in sorted(grouped.keys()):
                if cancel_event.is_set():
                    raise _AgentStopped

                tasks = grouped[order]
                prev_ctx = _build_previous_context(all_results + round_results, order)

                async for event in _execute_order_group(
                    tasks, llm, prev_ctx, cancel_event, semaphore, round_results,
                ):
                    yield event

            all_results.extend(round_results)
            yield _make_event("phase_end", phase="execute", iteration=iteration,
                              content=f"第 {iteration} 轮执行完成，已收集 {len(all_results)} 个研究结果")

            if iteration < settings.deep_research_max_iterations:
                if not all_results:
                    break

                yield _make_event("phase_start", phase="critique",
                                  content="正在评估研究充分度...")
                critique = await _phase_critique(llm, query, all_results)
                yield _make_event("critique", decision=critique["decision"],
                                  content=critique.get("content", ""))

                if critique["decision"] == "SUFFICIENT":
                    logger.info(f"[DeepResearch] 研究充分，共 {iteration} 轮")
                    break

                new_topics = critique.get("new_topics", [])
                if not new_topics:
                    break
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
                yield _make_event("critique", decision="MAX_ROUNDS",
                                  content=f"已达最大迭代次数 {settings.deep_research_max_iterations}")
                break

        # Phase 4: Synthesis
        if not all_results:
            yield _make_sse(json.dumps({"type": "error", "content": "研究未获得有效结果"}, ensure_ascii=False))
            yield _make_sse(json.dumps({"type": "complete"}, ensure_ascii=False))
            yield _make_sse("[DONE]")
            return

        synthesis_result: dict = {}
        async for event in _phase_synthesize(llm, query, all_results, synthesis_result):
            yield event

        final_text = synthesis_result.get("final_text", "")
        thinking_parts = synthesis_result.get("thinking_parts", [])
        references = synthesis_result.get("references", [])
        recommend_json = synthesis_result.get("recommend_json", "[]")

        # Save to session store
        store.save_message(
            session_id=conversation_id,
            question=query,
            answer=final_text,
            thinking="\n".join(thinking_parts),
            reference=json.dumps(references, ensure_ascii=False),
            recommend=recommend_json,
            tools="tavily_search",
            agent_type="deep",
            fileid=file_id,
        )

    except _AgentStopped:
        yield _make_sse(json.dumps({"type": "error", "content": "用户已停止"}, ensure_ascii=False))
        yield _make_sse(json.dumps({"type": "complete"}, ensure_ascii=False))
        yield _make_sse("[DONE]")
        return
    except Exception as e:
        logger.error(f"深度研究异常: {e}")
        yield _make_sse(json.dumps({"type": "error", "content": str(e)}, ensure_ascii=False))
    finally:
        redis_listener_task.cancel()
        try:
            await redis_listener_task
        except asyncio.CancelledError:
            pass
        _running_tasks.pop(conversation_id, None)
        await release_lock(conversation_id)

    total_ms = int((time.time() - t0) * 1000)
    logger.info(f"[DeepResearch] 完成, 耗时={total_ms}ms, 研究数={len(all_results)}")

    yield _make_event("recommend", content=recommend_json)
    yield _make_sse(json.dumps({"type": "complete"}, ensure_ascii=False))
    yield _make_sse("[DONE]")
