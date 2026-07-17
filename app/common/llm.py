import logging
import time

from langchain_core.outputs import ChatGenerationChunk
from langchain_openai import ChatOpenAI

from app.config.settings import get_settings

logger = logging.getLogger("apis")


class ChatOpenAIWithReasoning(ChatOpenAI):
    """保留 reasoning_content，用于 DeepSeek 等推理模型。"""

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: dict | None,
    ) -> ChatGenerationChunk | None:
        generation_chunk = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if generation_chunk is not None:
            choices = chunk.get("choices", []) or chunk.get("chunk", {}).get("choices", [])
            if choices:
                delta = choices[0].get("delta", {})
                reasoning = delta.get("reasoning_content", "")
                if reasoning and hasattr(generation_chunk, "message"):
                    generation_chunk.message.additional_kwargs["reasoning_content"] = reasoning
        return generation_chunk


class GatewayLLM:
    """网管包装器 — 在 LLM 调用前检查熔断器，记录成功/失败。

    使用组合模式包装原始 LLM，对调用方透明。
    """

    def __init__(self, model_name: str, llm):
        self._name = model_name
        self._llm = llm

    @property
    def name(self) -> str:
        return self._name

    async def ainvoke(self, *args, **kwargs):
        from app.gateway.model_gateway import model_gateway

        if not await model_gateway.before_request(self._name):
            raise RuntimeError(f"模型 {self._name} 已熔断，请稍后重试")

        start = time.monotonic()
        try:
            result = await self._llm.ainvoke(*args, **kwargs)
            latency = (time.monotonic() - start) * 1000
            await model_gateway.record_success(self._name, latency)
            return result
        except Exception as e:
            await model_gateway.record_failure(self._name, str(e))
            raise

    async def astream(self, *args, **kwargs):
        from app.gateway.model_gateway import model_gateway

        if not await model_gateway.before_request(self._name):
            raise RuntimeError(f"模型 {self._name} 已熔断，请稍后重试")

        start = time.monotonic()
        try:
            async for chunk in self._llm.astream(*args, **kwargs):
                yield chunk
            latency = (time.monotonic() - start) * 1000
            await model_gateway.record_success(self._name, latency)
        except Exception as e:
            await model_gateway.record_failure(self._name, str(e))
            raise

    def __getattr__(self, name):
        return getattr(self._llm, name)


def _create_raw_llm() -> ChatOpenAIWithReasoning:
    s = get_settings()
    return ChatOpenAIWithReasoning(
        model=s.llm_model,
        api_key=s.llm_api_key,
        base_url=s.llm_base_url,
        streaming=True,
    )


def build_llm():
    """构建 LLM 实例。

    若网管已注册模型，返回 GatewayLLM 包装器（含熔断保护）；
    否则返回原始 LLM 并自动注册到网关。
    """
    from app.gateway.model_gateway import model_gateway

    llm = _create_raw_llm()
    model_name = get_settings().llm_model

    active = model_gateway.get_active_name()
    if active and active in model_gateway._models:
        wrapped = model_gateway._models[active]
        if hasattr(wrapped, "_llm"):
            return wrapped
        return wrapped

    # 首次调用：注册到网关
    wrapped = GatewayLLM(model_name, llm)
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(model_gateway.register(model_name, wrapped, is_primary=True))
    except RuntimeError:
        pass
    return wrapped
