from langchain_core.outputs import ChatGenerationChunk
from langchain_openai import ChatOpenAI

from src.dodo_agent.config.settings import settings


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


def build_llm():
    return ChatOpenAIWithReasoning(
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        streaming=True,
    )
