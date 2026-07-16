import asyncio
import json
import time

from langchain.agents import create_agent

from src.apis_agent.agent.base_agent import BaseAgent, _process_chunks
from src.apis_agent.common.llm import build_llm
from src.apis_agent.common.logger import logger
from src.apis_agent.common.streaming import AgentStopped, make_event, make_sse
from src.apis_agent.common.tag_parser import StreamingTagParser
from src.apis_agent.harness.subagent_discovery import discover_specialists
from src.apis_agent.harness.task_executor import task_executor
from src.apis_agent.tool import TOOL_REGISTRY

TRIAGE_PROMPT = """你是一个智能调度助手，负责判断用户请求的复杂度并分流处理。

可用操作：
1. **直接回答** — 简单问题，直接调用工具回答
2. **后台任务** — 复杂/耗时任务，委托给后台执行器处理。输出标记：
   <delegate>true</delegate>
   并在 <plan>标签中描述执行计划

判断标准：
- 简单问题（直接回答）：事实查询、简单问答、单一信息搜索
- 复杂任务（后台执行）：多步骤研究、长文档生成、需要多个工具协作的任务

当前可用的子Agent：
{subagents}

使用中文回答。如果判断为简单问题，直接回答；如果判断为复杂任务，输出 <delegate>true</delegate> 和简要计划。"""


class TriageAgent(BaseAgent):
    """分流 Agent — 简单问题直接回答，复杂任务委托后台执行。

    使用 LangChain ReAct Agent 进行意图判断，通过标签输出决定分流路径。
    """

    def __init__(self, conversation_id: str, query: str, file_id: str = ""):
        super().__init__(conversation_id, query, file_id)

    def _build_agent(self):
        specialists = discover_specialists()
        subagent_desc = "\n".join(
            f"- {s['name']}: {s['description']}" for s in specialists
        ) or "(无可用子Agent)"

        return create_agent(
            build_llm(),
            list(TOOL_REGISTRY.values()),
            system_prompt=TRIAGE_PROMPT.format(subagents=subagent_desc),
        )

    async def run(self):
        ok, error_events = await self._try_start()
        if not ok:
            for evt in error_events:
                yield evt
            return

        tag_parser = StreamingTagParser()
        tools_used: set[str] = set()
        references: list[dict] = []
        delegated = False
        trace_config = self._build_trace_config("triage")

        try:
            messages = await self._load_messages()
            agent = self._build_agent()
            inputs = {"messages": messages}

            async for chunk in _process_chunks(agent, inputs, self.cancel_event, config=trace_config):
                if isinstance(chunk, dict) and chunk.get("_error"):
                    yield make_sse(json.dumps({"type": "error", "content": chunk["_error"]}, ensure_ascii=False))
                    return

                kind = chunk["event"]

                if kind == "on_tool_start":
                    tools_used.add(chunk.get("name", "unknown"))
                    yield make_event("tool_start", toolName=chunk.get("name", ""))

                elif kind == "on_tool_end":
                    yield make_event("tool_end", toolName=chunk.get("name", ""))
                    output = chunk.get("data", {}).get("output", "")
                    if isinstance(output, str) and "SOURCES:" in output:
                        try:
                            refs = json.loads(output.split("SOURCES: ", 1)[1].split("\n\nDETAILS:")[0])
                            if isinstance(refs, list):
                                references.extend(refs)
                                yield make_event("reference", content=refs)
                        except (json.JSONDecodeError, IndexError):
                            pass

                elif kind == "on_chat_model_stream":
                    data = chunk.get("data", {})
                    chunk_obj = data.get("chunk", "")

                    reasoning = None
                    if hasattr(chunk_obj, "additional_kwargs") and chunk_obj.additional_kwargs:
                        reasoning = chunk_obj.additional_kwargs.get("reasoning_content", "")
                    if reasoning:
                        tag_parser.thinking_parts.append(reasoning)
                        yield make_event("thinking", content=reasoning)

                    content_text = chunk_obj.content if hasattr(chunk_obj, "content") and chunk_obj.content else ""
                    if content_text:
                        events = tag_parser.feed(content_text)
                        for evt_type, evt_content in events:
                            yield make_event(evt_type, content=evt_content)

            tag_parser.flush()
            tag_parser.finalize()

            # 检测是否需要委托后台任务
            delegated = "<delegate>true</delegate>" in tag_parser.full_text.lower()

            if delegated:
                task_id = await task_executor.submit(
                    self.conversation_id,
                    self.query,
                    lambda snap: self._execute_background(snap, tag_parser.full_text),
                )
                yield make_sse(json.dumps({
                    "type": "task_delegated",
                    "taskId": task_id,
                    "message": "任务已提交后台执行",
                }, ensure_ascii=False))
            else:
                self._save_message(
                    answer=tag_parser.full_text,
                    thinking="\n".join(tag_parser.thinking_parts),
                    references=json.dumps(references, ensure_ascii=False),
                    recommend="[]",
                    tools=",".join(tools_used),
                    agent_type="triage",
                )

            yield make_event("recommend", content="[]")
            yield make_sse(json.dumps({"type": "complete"}, ensure_ascii=False))
            yield make_sse("[DONE]")

        except AgentStopped:
            yield make_sse(json.dumps({"type": "error", "content": "用户已停止"}, ensure_ascii=False))
            yield make_sse(json.dumps({"type": "complete"}, ensure_ascii=False))
            yield make_sse("[DONE]")
        except Exception as e:
            logger.error(f"TriageAgent 异常: {e}")
            yield make_sse(json.dumps({"type": "error", "content": str(e)}, ensure_ascii=False))
        finally:
            await self._cleanup()

    async def _execute_background(self, snapshot, plan_text: str):
        """后台执行：创建 Executor Agent 并流式执行。"""
        from src.apis_agent.agent.executor_agent import ExecutorAgent

        executor = ExecutorAgent(snapshot, plan_text)
        async for event in executor.run():
            yield event
