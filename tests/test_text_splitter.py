from src.apis_agent.utils.text_splitter import split_text


class TestSplitText:
    def test_empty_string(self):
        assert split_text("") == []

    def test_whitespace_only(self):
        assert split_text("   \n\n  ") == []

    def test_single_short_paragraph(self):
        result = split_text("hello world")
        assert len(result) == 1
        assert result[0] == "hello world"

    def test_multiple_short_paragraphs(self):
        text = "line one\nline two\nline three"
        result = split_text(text)
        assert len(result) == 1
        assert "line one" in result[0]

    def test_long_paragraph_splitting(self):
        text = "x" * 600
        result = split_text(text, chunk_size=500, overlap=50)
        assert len(result) == 2
        assert len(result[0]) <= 500

    def test_exact_chunk_boundary(self):
        text = "hello\nworld"
        result = split_text(text, chunk_size=100, overlap=10)
        assert len(result) >= 1

    def test_multiple_paragraphs_over_chunk_size(self):
        para1 = "a" * 300
        para2 = "b" * 300
        text = f"{para1}\n{para2}"
        result = split_text(text, chunk_size=500, overlap=50)
        assert len(result) == 2

    def test_custom_chunk_size(self):
        text = "x" * 300
        result = split_text(text, chunk_size=200, overlap=20)
        assert all(len(chunk) <= 200 for chunk in result)

    def test_paragraphs_with_blank_lines(self):
        text = "para one\n\n\npara two\n\npara three"
        result = split_text(text)
        assert len(result) >= 1

    def test_single_char_paragraph(self):
        text = "a\nb\nc\nd\ne"
        result = split_text(text, chunk_size=2, overlap=0)
        assert len(result) >= 1

    def test_large_overlap(self):
        text = "x" * 1000
        result = split_text(text, chunk_size=500, overlap=100)
        assert len(result) >= 2
