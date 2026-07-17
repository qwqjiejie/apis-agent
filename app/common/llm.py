import logging
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


def _create_raw_llm() -> ChatOpenAIWithReasoning:
    s = get_settings()
    return ChatOpenAIWithReasoning(
        model=s.llm_model,
        api_key=s.llm_api_key,
        base_url=s.llm_base_url,
        streaming=True,
    )


def build_llm(gateway=None):
    """构建 LLM，应用运行期间使用组合根中已注册的模型网关。"""
    if gateway is None:
        from app.bootstrap.container import get_application_container

        container = get_application_container(required=False)
        gateway = container.model_gateway if container is not None else None

    if gateway is None or not gateway.get_model_chain():
        return _create_raw_llm()

    from app.gateway.middleware import GatewayModelWrapper
    from app.gateway.types import ModelRole

    return GatewayModelWrapper(gateway, ModelRole.CHAT)
