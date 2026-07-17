"""Agent API 跨路由共享的请求模型。"""

from typing import Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(default="", min_length=0)
    query: str = Field(default="", min_length=0, description="(已废弃)")
    conversationId: str = Field(default="", min_length=0)
    fileIds: list[str] = Field(default_factory=list)
    online: bool = Field(default=True)
    userId: str = Field(default="")

    def get_message(self) -> str:
        return self.message or self.query

    def get_conversation_id(self) -> str:
        return self.conversationId or ""


class StopRequest(BaseModel):
    conversationId: str = Field(..., min_length=1)


class ShellConfirmRequest(BaseModel):
    confirmId: str = Field(..., min_length=1)
    approved: bool


class FeedbackRequest(BaseModel):
    conversationId: str = Field(..., min_length=1)
    rating: int = Field(..., ge=-1, le=1, description="1=点赞 -1=点踩")
    comment: str = Field(default="", max_length=500)


class TaskQueryRequest(BaseModel):
    taskId: str = Field(..., min_length=1)


class TaskResumeRequest(BaseModel):
    taskId: str = Field(..., min_length=1)
    action: Literal["approved", "rejected"] = Field(
        default="approved",
        description="approved=通过 rejected=拒绝",
    )
    comment: str = Field(default="", max_length=500)


class GatewaySwitchRequest(BaseModel):
    modelName: str = Field(..., min_length=1)
