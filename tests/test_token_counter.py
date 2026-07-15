from src.dodo_agent.context.token_counter import count_tokens, estimate_messages_tokens


class TestCountTokens:
    def test_empty_string(self):
        n = count_tokens("")
        assert n >= 0

    def test_simple_text(self):
        n = count_tokens("hello world")
        assert n > 0

    def test_chinese_text(self):
        n = count_tokens("你好世界")
        assert n > 0

    def test_long_text(self):
        n = count_tokens("x" * 1000)
        assert n > 0

    def test_english_vs_chinese(self):
        """中文每个字符约占 1-2 token，英文多字符组成 1 token"""
        en = count_tokens("hello world this is a test")
        zh = count_tokens("你好世界这是一个测试")
        assert en > 0
        assert zh > 0


class TestEstimateMessagesTokens:
    def test_single_user_message(self):
        msgs = [("user", "hello")]
        n = estimate_messages_tokens(msgs)
        assert n > 0

    def test_multiple_messages(self):
        msgs = [
            ("user", "hello"),
            ("assistant", "hi there"),
            ("user", "how are you"),
        ]
        n = estimate_messages_tokens(msgs)
        assert n > 0

    def test_empty_messages(self):
        n = estimate_messages_tokens([])
        assert n == 2

    def test_message_with_empty_content(self):
        msgs = [("user", "")]
        n = estimate_messages_tokens(msgs)
        assert n > 0

    def test_non_tuple_message(self):
        msgs = ["plain text message"]
        n = estimate_messages_tokens(msgs)
        assert n > 0
