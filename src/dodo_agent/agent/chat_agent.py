import asyncio
import json
import time

from langchain.agents import create_agent

from src.dodo_agent.agent.base_agent import BaseAgent, _process_chunks
from src.dodo_agent.common.llm import build_llm
from src.dodo_agent.common.logger import logger
from src.dodo_agent.common.streaming import AgentStopped, make_event, make_sse
from src.dodo_agent.common.tag_parser import StreamingTagParser
from src.dodo_agent.tool.bash_tool import bash_tool, set_shell_side_queue
from src.dodo_agent.tool.file_system_tools import read_file, write_file, edit_file, list_files, glob_files
from src.dodo_agent.tool.grep_tool import grep_tool
from src.dodo_agent.tool.skills_tool import load_skills
from src.dodo_agent.tool.tavily_search import tavily_search

# =============================================================================
# System Prompts
# =============================================================================

CHAT_SYSTEM_PROMPT = """你是一个智能问答助手，名字叫小豆豆，可以联网搜索实时信息来回答用户的问题。

回答规范：
- 使用中文回答
- 如果使用了搜索工具，在回答末尾列出参考来源（标题 + URL）
- 回答要清晰、准确、有条理
- 如果搜索结果不足以回答问题，诚实说明
- 回答结束后，在最后一行输出 <recommend>["问题1", "问题2", "问题3"]</recommend>，根据对话内容生成3个用户可能继续问的推荐问题"""

SKILLS_SYSTEM_PROMPT = """你是一个通用技能助手，名字叫小豆豆，拥有完整的工具集来处理各种任务。

可用工具类型：
- 搜索工具：联网搜索实时信息
- 文件系统工具：读写编辑文件、列出目录、匹配文件
- 代码搜索工具：正则表达式搜索代码内容
- Shell 工具：执行系统命令
- 技能工具：执行特定领域的技能任务

工作原则：
1. 分析用户意图，选择最合适的工具组合
2. 执行前评估风险：写文件和执行命令操作需确认影响范围
3. 工具执行结果不足以完成任务时，尝试替代方案或诚实说明
4. 输出使用中文，代码和命令保持原样"""

# =============================================================================
# Agent 工厂 — 模块级缓存，避免重复创建 LangChain Agent 实例
# =============================================================================

_chat_agent = None       # 聊天 Agent 缓存（仅搜索工具）
_skills_agent = None     # 技能 Agent 缓存（全套工具）


def _build_react_agent():
    """构建聊天 Agent — 仅携带 Tavily 搜索工具，使用 ReAct 模式。"""
    global _chat_agent
    if _chat_agent is None:
        _chat_agent = create_agent(build_llm(), [tavily_search], system_prompt=CHAT_SYSTEM_PROMPT)
        logger.info("Chat Agent 已缓存")
    return _chat_agent


def _build_skills_agent():
    """构建技能 Agent — 携带全套工具（搜索/文件系统/Bash/Grep/技能插件）。"""
    global _skills_agent
    if _skills_agent is None:
        tools = [
            tavily_search,
            read_file,
            write_file,
            edit_file,
            list_files,
            glob_files,
            grep_tool,
            bash_tool,
        ]
        skill_tools = load_skills()
        if skill_tools:
            logger.info(f"已加载 {len(skill_tools)} 个技能")
        tools.extend(skill_tools)
        _skills_agent = create_agent(build_llm(), tools, system_prompt=SKILLS_SYSTEM_PROMPT)
        logger.info("Skills Agent 已缓存")
    return _skills_agent


# =============================================================================
# ChatAgent — ReAct 模式对话 Agent
# =============================================================================

class ChatAgent(BaseAgent):
    """聊天 / 文件问答 / 技能助手 Agent。

    根据 agent_type 选择不同的工具集：
    - "chat":   仅 Tavily 搜索（调用 /agent/chat/stream 和 /agent/file/stream）
    - "skills": 全套工具（调用 /agent/skills/stream）
    """

    def __init__(self, conversation_id: str, query: str, file_id: str = "", agent_type: str = "chat"):
        super().__init__(conversation_id, query, file_id)
        self.agent_type = agent_type

    def _build_agent(self):
        """根据 agent_type 返回对应的预缓存 Agent 实例。"""
        if self.agent_type == "skills":
            return _build_skills_agent()
        return _build_react_agent()

    async def run(self):
        """模板方法流程：
        1. 获取锁 + 注册任务
        2. 加载上下文（历史 + 压缩 + 文件 RAG）
        3. 构建 Agent → 流式执行 → 实时解析 think/recommend 标签
        4. 提取工具调用和参考来源
        5. 持久化消息 → 推送推荐问题 → 发送完成信号
        6. 清理资源
        """
        # ---- 第一步：获取锁并注册任务 ----
        ok, error_events = await self._try_start()
        if not ok:
            for evt in error_events:
                yield evt
            return

        # ---- 初始化状态 ----
        tag_parser = StreamingTagParser()   # 流式标签解析器
        tools_used: set[str] = set()        # 本轮使用的工具集合
        references: list[dict] = []         # 搜索参考来源
        first_token_sent = False            # 首 token 延迟标记
        shell_queue: asyncio.Queue = asyncio.Queue()  # shell 确认事件队列
        set_shell_side_queue(shell_queue)

        try:
            # ---- 第二步：加载上下文 ----
            messages = self._load_messages()
            agent = self._build_agent()
            inputs = {"messages": messages}

            # ---- 第三步：流式执行 + 实时解析 ----
            async for chunk in _process_chunks(agent, inputs, self.cancel_event, side_queue=shell_queue):
                # 处理 agent_task 内部异常
                if isinstance(chunk, dict) and chunk.get("_error"):
                    yield make_sse(json.dumps({"type": "error", "content": chunk["_error"]}, ensure_ascii=False))
                    return

                # 处理 side_queue 事件（shell 命令确认请求）
                if isinstance(chunk, dict) and chunk.get("_side_event"):
                    side_data = chunk["_side_event"]
                    yield make_sse(json.dumps(side_data, ensure_ascii=False))
                    continue

                kind = chunk["event"]

                # --- 工具调用开始：通知前端展示工具调用状态 ---
                if kind == "on_tool_start":
                    name = chunk.get("name", "unknown")
                    tools_used.add(name)
                    yield make_event("tool_start", toolName=name, toolCallId=chunk.get("run_id", ""))

                # --- 工具调用结束：提取搜索结果中的参考来源 ---
                elif kind == "on_tool_end":
                    name = chunk.get("name", "unknown")
                    yield make_event("tool_end", toolName=name, toolCallId=chunk.get("run_id", ""))
                    output = chunk.get("data", {}).get("output", "")
                    if isinstance(output, str) and "SOURCES:" in output:
                        try:
                            src = output.split("SOURCES: ", 1)[1]
                            if "\n\nDETAILS:" in src:
                                src = src.split("\n\nDETAILS:")[0]
                            refs = json.loads(src)
                            if isinstance(refs, list):
                                references.extend(refs)
                                yield make_event("reference", content=refs)
                        except (json.JSONDecodeError, IndexError):
                            pass

                # --- LLM 流式输出：双轨解析 ---
                elif kind == "on_chat_model_stream":
                    data = chunk.get("data", {})
                    chunk_obj = data.get("chunk", "")

                    # 轨道1: reasoning_content（DeepSeek 等推理模型原生字段）
                    reasoning = None
                    if hasattr(chunk_obj, "additional_kwargs") and chunk_obj.additional_kwargs:
                        reasoning = chunk_obj.additional_kwargs.get("reasoning_content", "")
                    if reasoning:
                        tag_parser.thinking_parts.append(reasoning)
                        yield make_event("thinking", content=reasoning)

                    # 轨道2: 正文内容 → 流式标签解析（<think> / <recommend>）
                    content_text = chunk_obj.content if hasattr(chunk_obj, "content") and chunk_obj.content else ""
                    if content_text:
                        events = tag_parser.feed(content_text)
                        for evt_type, evt_content in events:
                            if not first_token_sent:
                                first_token_sent = True
                            yield make_event(evt_type, content=evt_content)

            # ---- 第四步：flush 残留 buffer ----
            flush_events = tag_parser.flush()
            for evt_type, evt_content in flush_events:
                yield make_event(evt_type, content=evt_content)

            # 后处理：清理残留标签，提取 recommend_json
            tag_parser.finalize()

            # ---- 第五步：持久化消息 ----
            self._save_message(
                answer=tag_parser.full_text,
                thinking="\n".join(tag_parser.thinking_parts),
                references=json.dumps(references, ensure_ascii=False),
                recommend=tag_parser.recommend_json,
                tools=",".join(tools_used),
                agent_type=self.agent_type,
            )

            # ---- 第六步：推送推荐问题 + 完成信号 ----
            yield make_event("recommend", content=tag_parser.recommend_json)
            yield make_sse(json.dumps({"type": "complete"}, ensure_ascii=False))
            yield make_sse("[DONE]")

        except AgentStopped:
            # 用户主动停止
            yield make_sse(json.dumps({"type": "error", "content": "用户已停止"}, ensure_ascii=False))
            yield make_sse(json.dumps({"type": "complete"}, ensure_ascii=False))
            yield make_sse("[DONE]")
        except asyncio.CancelledError:
            # 系统级取消
            yield make_sse(json.dumps({"type": "error", "content": "任务已取消"}, ensure_ascii=False))
        except Exception as e:
            logger.error(f"ChatAgent 异常: {e}")
            yield make_sse(json.dumps({"type": "error", "content": str(e)}, ensure_ascii=False))
        finally:
            # ---- 第七步：清理资源 ----
            set_shell_side_queue(None)
            await self._cleanup()
