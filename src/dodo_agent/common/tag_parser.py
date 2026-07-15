import re

THINK_PATTERN = re.compile(r"<think>(.*?)</think>", re.DOTALL)
RECOMMEND_PATTERN = re.compile(r"<recommend>(.*?)</recommend>", re.DOTALL)


class StreamingTagParser:
    """流式解析 <think> 和 <recommend> 标签，分离思考内容和最终回答。"""

    def __init__(self):
        self.think_buffer = ""
        self.recommend_buffer = ""
        self.thinking_parts: list[str] = []
        self.full_text = ""
        self.recommend_json = ""

    def feed(self, text: str) -> list[tuple[str, str]]:
        """喂入文本块，返回解析出的事件列表 [(type, content), ...]。
        type 为 "thinking" 或 "text"。
        """
        events: list[tuple[str, str]] = []
        self.think_buffer += text

        while True:
            m = THINK_PATTERN.search(self.think_buffer)
            if not m:
                break
            think_content = m.group(1)
            if think_content.strip():
                self.thinking_parts.append(think_content)
                events.append(("thinking", think_content))
            self.think_buffer = self.think_buffer[:m.start()] + self.think_buffer[m.end():]

        if "<think>" in self.think_buffer:
            tag_pos = self.think_buffer.rfind("<think>")
            text_part = self.think_buffer[:tag_pos]
            if text_part:
                events.extend(self._emit_text(text_part))
            self.think_buffer = self.think_buffer[tag_pos:]
        else:
            if self.think_buffer:
                events.extend(self._emit_text(self.think_buffer))
            self.think_buffer = ""

            if self.recommend_buffer and "</recommend>" in self.recommend_buffer:
                m = RECOMMEND_PATTERN.search(self.recommend_buffer)
                if m:
                    self.recommend_json = m.group(1).strip()
                self.recommend_buffer = ""

        return events

    def _emit_text(self, text: str) -> list[tuple[str, str]]:
        """处理文本块，检查 recommend 标签边界。"""
        if self.recommend_buffer:
            self.recommend_buffer += text
            return []

        if "<recommend" in text:
            idx = text.find("<recommend")
            before = text[:idx]
            self.recommend_buffer += text[idx:]
            if before:
                self.full_text += before
                return [("text", before)]
            return []

        self.full_text += text
        return [("text", text)]

    def flush(self) -> list[tuple[str, str]]:
        """刷新剩余 buffer，返回未处理的事件。"""
        events: list[tuple[str, str]] = []

        if self.recommend_buffer:
            m = RECOMMEND_PATTERN.search(self.recommend_buffer)
            if m and not self.recommend_json:
                self.recommend_json = m.group(1).strip()
            self.recommend_buffer = ""

        if self.think_buffer.strip():
            clean = THINK_PATTERN.sub("", self.think_buffer).strip()
            if clean:
                self.full_text += clean
                events.append(("text", clean))

        return events

    def finalize(self) -> str:
        """后处理 full_text，移除残留的 recommend 标签，提取 recommend_json。"""
        if not self.recommend_json:
            m = RECOMMEND_PATTERN.search(self.full_text)
            if m:
                self.recommend_json = m.group(1).strip()
                self.full_text = RECOMMEND_PATTERN.sub("", self.full_text).strip()
        if not self.recommend_json:
            self.recommend_json = "[]"
        return self.recommend_json
