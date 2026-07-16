import asyncio
import json
import time
from abc import ABC, abstractmethod

from src.apis_agent.common.llm import build_llm
from src.apis_agent.common.langfuse_client import get_langfuse_callback
from src.apis_agent.common.trace_context import set_trace_context
from src.apis_agent.service.file_service import file_service
from src.apis_agent.service.rag_service import build_context
from src.apis_agent.service.session_service import store
from src.apis_agent.common.logger import logger
from src.apis_agent.common.redis import acquire_lock, listen_stop, release_lock
from src.apis_agent.common.exceptions import QueryTooLongError
from src.apis_agent.common.streaming import AgentStopped, make_event, make_sse
from src.apis_agent.config.settings import get_settings
from src.apis_agent.context.compressor import compress_layer_1, compress_layer_2
from src.apis_agent.context.token_counter import estimate_messages_tokens


# =============================================================================
# 工具函数
# =============================================================================

def _build_history_messages(history: list[dict]) -> list:
    """将数据库中的对话历史转换为 LangChain 消息格式 [(role, content), ...]"""
    msgs = []
    for h in history:
        msgs.append(("user", h["question"]))
        if h.get("answer"):
            msgs.append(("assistant", h["answer"]))
    return msgs


async def _process_chunks(agent, inputs: dict, cancel_event: asyncio.Event,
                       side_queue: asyncio.Queue | None = None, config: dict | None = None):
    """将 Agent 流式执行封装到独立 Task 中，通过 asyncio.Queue 解耦。

    核心设计：Agent 在独立 Task 中执行 astream_events，主循环同时监听
    chunk 到达、cancel_event 和 side_queue，取消时能通过 CancelledError 传播
    到 langgraph 内部，自动清理子任务。返回的 chunk 通过 yield 透传给调用方。

    side_queue 用于带外事件（如 shell 命令确认请求），以 {"_side_event": data}
    格式 yield 给调用方处理。

    config 透传给 agent.astream_events()，用于注入 callbacks（如 Langfuse）。
    """
    chunk_queue: asyncio.Queue = asyncio.Queue(maxsize=256)
    cfg = config or {}

    async def run_agent():
        try:
            async for chunk in agent.astream_events(inputs, version="v2", config=cfg):
                await chunk_queue.put(("chunk", chunk))
            await chunk_queue.put(("done", None))
        except asyncio.CancelledError:
            await chunk_queue.put(("cancelled", None))
        except Exception as e:
            await chunk_queue.put(("error", str(e)))

    agent_task = asyncio.create_task(run_agent())

    try:
        while True:
            get_task = asyncio.create_task(chunk_queue.get())
            watch_task = asyncio.create_task(cancel_event.wait())
            tasks = [get_task, watch_task]
            side_task = None
            if side_queue is not None:
                side_task = asyncio.create_task(side_queue.get())
                tasks.append(side_task)

            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

            for t in pending:
                t.cancel()

            # side_queue 事件：yield 给调用方，继续循环
            if side_task is not None and side_task in done:
                yield {"_side_event": side_task.result()}
                continue

            # 取消信号先到达：终止 agent_task，向上抛出 AgentStopped
            if watch_task in done:
                agent_task.cancel()
                try:
                    await agent_task
                except asyncio.CancelledError:
                    pass
                raise AgentStopped

            msg_type, data = get_task.result()

            if msg_type == "done":
                return
            elif msg_type == "cancelled":
                raise AgentStopped
            elif msg_type == "error":
                yield {"_error": data}
                return

            yield data

    finally:
        # 确保 agent_task 被清理
        if not agent_task.done():
            agent_task.cancel()
            try:
                await agent_task
            except asyncio.CancelledError:
                pass


# =============================================================================
# BaseAgent — 所有 Agent 的抽象基类
# =============================================================================

class BaseAgent(ABC):
    """提供锁/历史/压缩/保存/清理等公共基础设施，子类只需实现 run() 方法。

    公共职责：
    1. 分布式锁：Redis SETNX 防止同一会话并发执行，Redis 不可用时降级放行
    2. 任务注册：将 cancel_event 注册到类级别 _running_tasks，供 /stop 端点使用
    3. 停止监听：Redis Pub/Sub 跨实例停止信号 + 本地 asyncio.Event
    4. 上下文管理：加载历史 → Layer1 占位符压缩 → Layer2 后台 LLM 摘要
    5. 文件上下文：RAG 检索拼接文件内容到 prompt
    6. 消息持久化：执行完成后将问答对保存到 MySQL（含 token 用量）
    7. 资源清理：取消监听、移除任务、释放锁
    """

    # 类级别注册表：conversation_id → cancel_event，供 /stop 端点查找
    _running_tasks: dict[str, asyncio.Event] = {}

    def __init__(self, conversation_id: str, query: str, file_id: str = ""):
        if len(query) > get_settings().max_query_length:
            raise QueryTooLongError(get_settings().max_query_length)
        self.conversation_id = conversation_id
        self.query = query
        self.file_id = file_id
        self.cancel_event = asyncio.Event()           # 本地停止信号
        self._redis_listener_task: asyncio.Task | None = None  # 跨实例停止监听任务
        self._start_time = time.time()                # 计时起点，用于日志

    # =========================================================================
    # 公共基础设施方法
    # =========================================================================

    async def _try_start(self) -> tuple[bool, list[dict]]:
        """尝试获取分布式锁并注册任务。

        成功 → 返回 (True, [])，调用方继续执行。
        失败 → 返回 (False, [error_events])，调用方应 yield 这些事件后 return。

        两层防护：
        1. Redis 分布式锁（跨实例互斥）
        2. 本地 _running_tasks 字典（同进程兜底）
        """
        # 第一层：Redis 分布式锁
        lock_ok = await acquire_lock(self.conversation_id, ttl=get_settings().task_lock_timeout_seconds)
        if not lock_ok:
            return False, [
                make_sse(json.dumps({"type": "error", "content": "当前会话有任务正在执行中，请稍后再试"}, ensure_ascii=False)),
                make_sse(json.dumps({"type": "complete"}, ensure_ascii=False)),
                make_sse("[DONE]"),
            ]

        # 第二层：本地并发兜底
        if self.conversation_id in self._running_tasks:
            await release_lock(self.conversation_id)
            return False, [
                make_sse(json.dumps({"type": "error", "content": "当前会话有任务正在执行中，请稍后再试"}, ensure_ascii=False)),
                make_sse(json.dumps({"type": "complete"}, ensure_ascii=False)),
                make_sse("[DONE]"),
            ]

        # 注册任务 + 启动跨实例停止监听
        self._running_tasks[self.conversation_id] = self.cancel_event
        self._redis_listener_task = asyncio.create_task(
            listen_stop(self.conversation_id, self.cancel_event)
        )
        return True, []

    async def _load_messages(self) -> list:
        """加载上下文消息：历史 → 压缩 → 文件上下文拼接。

        流程：
        1. 从 store 加载最近 N 轮对话历史
        2. Layer 1 压缩：旧轮次搜索结果 → 占位符，长回答截断
        3. 估算 token，超 75% 阈值时触发 Layer 2 后台 LLM 摘要
        4. 如果有 fileId，异步 RAG 检索拼接文件内容
        """
        # 加载历史并转为消息列表
        history = store.load_history(self.conversation_id, limit=get_settings().max_history_rounds)
        messages = _build_history_messages(history)

        # Layer 1：占位符压缩（同步，不阻塞首 token）
        if get_settings().compression_enabled:
            messages = compress_layer_1(messages)

        # Token 估算 + Layer 2 触发
        pre_messages = messages + [("user", self.query)]
        token_count = estimate_messages_tokens(pre_messages)
        self._input_tokens = token_count
        threshold = int(get_settings().max_context_tokens * get_settings().compression_layer_2_threshold_ratio)
        if token_count > threshold:
            logger.info(f"Token 超阈值 ({token_count}/{get_settings().max_context_tokens})，触发 Layer 2 压缩（后台）")
            # 后台异步执行，不阻塞首 token
            asyncio.create_task(self._bg_compress(pre_messages))

        # 文件上下文拼接（异步 RAG 管线：查询重写 + 多路召回 + RRF + 动态裁剪）
        file_context = ""
        if self.file_id:
            content = file_service.get_content(self.file_id)
            if content and content.get("extractedText"):
                ctx = await build_context(self.query, self.file_id, content["extractedText"])
                if ctx:
                    file_context = (
                        "\n\n【参考以下文件内容回答问题，优先基于文件内容作答，若文件内容不足以回答再结合搜索】\n\n" + ctx
                    )

        return messages + [("user", self.query + file_context)]

    async def _bg_compress(self, messages: list):
        """后台异步执行 Layer 2 LLM 摘要压缩。失败不影响主流程。"""
        try:
            llm = build_llm()
            await compress_layer_2(messages, llm, get_settings().max_context_tokens)
        except Exception as e:
            logger.warning(f"Layer 2 压缩失败: {e}")

    def _save_message(self, answer="", thinking="", references="", recommend="", tools="", agent_type="chat"):
        """将本轮问答持久化到 MySQL，并记录 token 用量日志。"""
        output_tokens = estimate_messages_tokens([("assistant", answer)])
        store.save_message(
            session_id=self.conversation_id,
            question=self.query,
            answer=answer,
            thinking=thinking,
            reference=references,
            recommend=recommend,
            tools=tools,
            agent_type=agent_type,
            fileid=self.file_id,
        )
        input_tokens = getattr(self, "_input_tokens", 0)
        logger.info(f"Token 用量: 输入~{input_tokens}, 输出~{output_tokens}, 会话={self.conversation_id}")

    async def _cleanup(self):
        """释放资源：取消 Redis 监听 → 从注册表移除 → 释放分布式锁。"""
        if self._redis_listener_task:
            self._redis_listener_task.cancel()
            try:
                await self._redis_listener_task
            except asyncio.CancelledError:
                pass
        self._running_tasks.pop(self.conversation_id, None)
        await release_lock(self.conversation_id)

    def _build_trace_config(self, agent_type: str) -> dict:
        """构建包含 Langfuse callback 的 config，注入 trace 上下文到 contextvars。"""
        set_trace_context(session_id=self.conversation_id, agent_type=agent_type)
        lf_handler = get_langfuse_callback()
        if lf_handler is None:
            return {}
        return {"callbacks": [lf_handler]}

    # =========================================================================
    # 抽象方法 — 子类必须实现
    # =========================================================================

    @abstractmethod
    async def run(self):
        """子类实现各自 Agent 执行流程，yield SSE 格式的 dict。

        典型流程（ChatAgent）：
        _try_start → _load_messages → build_agent → _process_chunks(astream_events)
        → tag_parser 实时解析 → _save_message → recommend → complete → _cleanup
        """
        ...
