from app.common.logger import logger
from app.config.settings import get_settings


def compress_layer_1(messages: list) -> list:
    """对旧轮次的搜索结果和长回答做占位符压缩，保留最近 N 轮完整内容。"""
    keep = get_settings().compression_layer_1_keep_recent_rounds
    rounds = _split_rounds(messages)
    if len(rounds) <= keep:
        return messages

    result = []
    for i, round_msgs in enumerate(rounds):
        if i >= len(rounds) - keep:
            result.extend(round_msgs)
        else:
            for role, content in round_msgs:
                if role == "assistant" and _is_search_result(content):
                    result.append((role, f"[搜索工具输出已压缩，原始 {len(content)} 字符]"))
                elif role == "assistant" and len(content) > 800:
                    result.append((role, content[:300] + f"\n[回答已压缩，原始 {len(content)} 字符]"))
                else:
                    result.extend(round_msgs)
                    break
    return result


def _is_search_result(content: str) -> bool:
    return "SEARCH_RESULTS:" in content or "SOURCES:" in content or "tavily_search" in content


def _split_rounds(messages: list) -> list[list]:
    """将消息列表按 user 消息分割为轮次列表。"""
    rounds = []
    current = []
    for msg in messages:
        role = msg[0] if isinstance(msg, (tuple, list)) else getattr(msg, "type", "")
        if role == "user" and current:
            rounds.append(current)
            current = []
        current.append(msg)
    if current:
        rounds.append(current)
    return rounds


async def compress_layer_2(messages: list, llm, max_tokens: int) -> list:
    """用 LLM 将旧轮次摘要为一句话，注入为 system 消息。"""
    keep = get_settings().compression_layer_1_keep_recent_rounds
    rounds = _split_rounds(messages)
    if len(rounds) <= keep:
        return messages

    old_rounds = rounds[:len(rounds) - keep]
    recent_rounds = rounds[len(rounds) - keep:]

    summary_text = _rounds_to_text(old_rounds)
    if not summary_text:
        return messages

    try:
        summary_prompt = (
            "请将以下对话历史压缩为一段简短的摘要（不超过200字），保留关键事实和上下文：\n\n" + summary_text
        )
        response = await llm.ainvoke(summary_prompt)
        summary = response.content if hasattr(response, "content") else str(response)
        summary_msg = ("system", f"[历史对话摘要] {summary}")
        result = [summary_msg]
        for r in recent_rounds:
            result.extend(r)
        logger.info(f"Layer 2 压缩完成: {len(old_rounds)} 轮 -> 1 条摘要")
        return result
    except Exception as e:
        logger.warning(f"Layer 2 压缩失败，保留原始消息: {e}")
        return messages


def _rounds_to_text(rounds: list[list]) -> str:
    parts = []
    for r in rounds:
        for role, content in r:
            label = "用户" if role == "user" else "助手"
            text = content[:500] if len(content) > 500 else content
            parts.append(f"{label}: {text}")
    return "\n".join(parts)
