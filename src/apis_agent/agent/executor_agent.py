import asyncio
import json

from langchain.agents import create_agent

from src.apis_agent.common.llm import build_llm
from src.apis_agent.common.logger import logger
from src.apis_agent.common.streaming import make_event, make_sse
from src.apis_agent.harness.subagent_discovery import discover_specialists
from src.apis_agent.tool import TOOL_REGISTRY

EXECUTOR_PROMPT = """你是一个后台任务执行器，负责自主规划和执行复杂任务。

可用工具：
- 搜索工具 (tavily_search): 联网搜索信息
- 其他注册工具

执行原则：
1. 分析任务需求，制定执行步骤
2. 逐步执行，每步完成后评估结果
3. 如果某步骤失败，尝试替代方案
4. 所有步骤完成后，生成最终报告

当前可用的子Agent（可通过委派工具使用）：
{subagents}

使用中文输出，清晰、有条理。"""


class ExecutorAgent:
    """后台任务执行 Agent。

    不继承 BaseAgent（不在 HTTP 请求上下文中运行），
    而是作为独立 Agent 在 TaskExecutor 的后台 asyncio.Task 中执行。
    """

    def __init__(self, snapshot, plan_text: str = ""):
        self.snapshot = snapshot
        self.plan_text = plan_text
        self.cancel_event = snapshot.cancel_event

    async def run(self):
        yield {"_task_status": "running"}

        try:
            llm = build_llm()
            specialists = discover_specialists()

            # 构建工具列表：注册工具 + 子Agent 作为可委派工具
            tools = list(TOOL_REGISTRY.values())

            subagent_desc = "\n".join(
                f"- {s['name']}: {s['description']}" for s in specialists
            ) or "(无)"

            agent = create_agent(
                llm,
                tools,
                system_prompt=EXECUTOR_PROMPT.format(subagents=subagent_desc),
            )

            # 构建输入：计划 + 原始查询
            query = self.snapshot.query
            if self.plan_text:
                query = f"执行计划：\n{self.plan_text[:500]}\n\n原始任务：{query}"

            inputs = {"messages": [("user", query)]}

            full_text = ""
            async for chunk in agent.astream_events(inputs, version="v2"):
                if self.cancel_event.is_set():
                    yield make_sse(json.dumps({"type": "text", "content": "\n\n[任务已取消]"}, ensure_ascii=False))
                    return

                kind = chunk["event"]

                if kind == "on_tool_start":
                    yield make_event("tool_start", toolName=chunk.get("name", ""))

                elif kind == "on_tool_end":
                    yield make_event("tool_end", toolName=chunk.get("name", ""))

                elif kind == "on_chat_model_stream":
                    data = chunk.get("data", {})
                    chunk_obj = data.get("chunk", "")
                    if hasattr(chunk_obj, "content") and chunk_obj.content:
                        text = chunk_obj.content
                        full_text += text
                        yield make_event("text", content=text)

            self.snapshot.result = full_text
            yield {"_task_status": "completed"}

        except asyncio.CancelledError:
            yield {"_task_status": "cancelled"}
        except Exception as e:
            logger.error(f"[ExecutorAgent] 异常: {e}")
            self.snapshot.error = str(e)
            yield make_sse(json.dumps({"type": "error", "content": str(e)}, ensure_ascii=False))
