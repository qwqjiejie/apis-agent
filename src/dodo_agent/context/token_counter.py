import tiktoken

from src.dodo_agent.config.settings import get_settings

_ENCODING_CACHE: dict[str, tiktoken.Encoding] = {}
_DEFAULT_ENCODING = "o200k_base"


def _get_encoding(model: str | None = None) -> tiktoken.Encoding:
    name = model or get_settings().llm_model
    if name not in _ENCODING_CACHE:
        try:
            _ENCODING_CACHE[name] = tiktoken.encoding_for_model(name)
        except KeyError:
            _ENCODING_CACHE[name] = tiktoken.get_encoding(_DEFAULT_ENCODING)
    return _ENCODING_CACHE[name]


def count_tokens(text: str, model: str | None = None) -> int:
    enc = _get_encoding(model)
    return len(enc.encode(text))


def estimate_messages_tokens(messages: list, model: str | None = None) -> int:
    """估算 OpenAI 格式消息列表的 token 数。messages 为 [(role, content), ...] 或 [Message, ...] 格式。"""
    total = 0
    for msg in messages:
        total += 4  # 每条消息固定开销
        if isinstance(msg, (tuple, list)) and len(msg) == 2:
            role, content = msg
            total += len(_get_encoding(model).encode(str(role)))
            total += len(_get_encoding(model).encode(str(content)))
        elif hasattr(msg, "content"):
            content = getattr(msg, "content", "") or ""
            role = getattr(msg, "type", "unknown")
            total += len(_get_encoding(model).encode(str(role)))
            total += len(_get_encoding(model).encode(str(content)))
        else:
            total += len(_get_encoding(model).encode(str(msg)))
    total += 2
    return total


def estimate_text_tokens(text: str, model: str | None = None) -> int:
    return count_tokens(text, model)
