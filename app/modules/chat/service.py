"""对话持久化和标题生成用例。"""

from app.common.llm import _create_raw_llm
from app.modules.chat.sessions import store
from sqlalchemy import text

from app.infrastructure.postgres.database import session_scope


def save_session(
    session_id: str,
    question: str,
    answer: str,
    user_id: str = "",
    tools: str = "",
) -> None:
    store.save_message(
        session_id=session_id,
        question=question,
        answer=answer,
        user_id=user_id or "",
        agent_type="triage",
        tools=tools,
    )


def update_session_title(session_id: str, title: str) -> None:
    with session_scope() as db:
        db.execute(
            text(
                "UPDATE agentx_session "
                "SET title = :title WHERE session_id = :session_id"
            ),
            {"title": title[:40], "session_id": session_id},
        )


async def generate_title(question: str, answer: str) -> str:
    llm = _create_raw_llm()
    prompt = (
        "请根据以下对话生成5-15字简短标题，只输出标题: "
        f"用户:{question[:200]} 助手:{answer[:200]}"
    )
    try:
        response = await llm.ainvoke(prompt)
        content = response.content if hasattr(response, "content") else str(response)
        title = content.strip().replace("\n", "")[:15]
        return title or "新对话"
    except Exception:
        return "新对话"
