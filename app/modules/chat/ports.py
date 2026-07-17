"""对话模块对持久化能力的公共端口。"""

from abc import ABC, abstractmethod


class BaseStore(ABC):

    @abstractmethod
    def save_message(self, session_id: str, question: str, answer: str, *,
                     thinking: str = "", reference: str = "", recommend: str = "",
                     tools: str = "", agent_type: str = "chat", fileid: str = "",
                     user_id: str = "") -> None: ...

    @abstractmethod
    def load_history(self, session_id: str, limit: int = 20) -> list[dict]: ...

    @abstractmethod
    def list_sessions(
        self,
        page: int = 1,
        size: int = 100,
        user_id: str = "",
    ) -> tuple[list[dict], int]: ...

    @abstractmethod
    def get_session(self, session_id: str) -> dict | None: ...

    @abstractmethod
    def delete_session(self, session_id: str) -> bool: ...

    @abstractmethod
    def is_available(self) -> bool: ...
