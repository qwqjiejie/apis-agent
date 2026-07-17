"""对话持久化和标题生成用例。"""

from app.common.llm import _create_raw_llm
from app.service.session_service import store
from app.storage.db import new_session


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
    db = new_session()
    try:
        db.execute(
            "UPDATE agentx_session SET title = %s WHERE session_id = %s",
            (title[:40], session_id),
        )
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


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
