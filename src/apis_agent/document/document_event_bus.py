import asyncio
import logging

logger = logging.getLogger("apis")


class DocumentEventBus:
    """文档处理进度 SSE 推送。

    每个 file_id 对应一个 asyncio.Queue，处理管线各阶段向队列推送事件。
    前端通过 /file/progress SSE 端点订阅。
    """

    def __init__(self):
        self._subscribers: dict[str, asyncio.Queue] = {}

    def subscribe(self, file_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=32)
        self._subscribers[file_id] = q
        return q

    def unsubscribe(self, file_id: str):
        self._subscribers.pop(file_id, None)

    async def publish(self, file_id: str, status: str, message: str = "",
                      progress: int = 0, **extra):
        """向订阅者推送进度事件。"""
        q = self._subscribers.get(file_id)
        if q is None:
            return
        event = {"type": "doc_progress", "fileId": file_id, "status": status,
                 "message": message, "progress": progress, **extra}
        try:
            await q.put(event)
        except asyncio.QueueFull:
            pass


event_bus = DocumentEventBus()
