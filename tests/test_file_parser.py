from src.apis_agent.utils.file_parser import get_file_type, is_supported, validate_mime_type


class TestGetFileType:
    def test_lowercase_extension(self):
        assert get_file_type("document.pdf") == "pdf"

    def test_uppercase_extension(self):
        assert get_file_type("IMAGE.PNG") == "png"

    def test_mixed_case_extension(self):
        assert get_file_type("Report.DocX") == "docx"

    def test_no_extension(self):
        assert get_file_type("README") == ""

    def test_multiple_dots(self):
        assert get_file_type("archive.tar.gz") == "gz"

    def test_hidden_file(self):
        assert get_file_type(".gitignore") == ""

    def test_path_with_dirs(self):
        assert get_file_type("/path/to/file.txt") == "txt"


class TestIsSupported:
    def test_pdf(self):
        assert is_supported("doc.pdf") is True

    def test_docx(self):
        assert is_supported("report.docx") is True

    def test_txt(self):
        assert is_supported("notes.txt") is True

    def test_png(self):
        assert is_supported("screenshot.png") is True

    def test_jpg(self):
        assert is_supported("photo.jpg") is True

    def test_jpeg(self):
        assert is_supported("photo.jpeg") is True

    def test_exe(self):
        assert is_supported("virus.exe") is False

    def test_py(self):
        assert is_supported("script.py") is False

    def test_empty_extension(self):
        assert is_supported("noext") is False


class TestValidateMimeType:
    def test_pdf_correct_mime(self):
        valid, _ = validate_mime_type("doc.pdf", "application/pdf")
        assert valid is True

    def test_pdf_wrong_mime(self):
        valid, msg = validate_mime_type("doc.pdf", "text/plain")
        assert valid is False

    def test_png_correct_mime(self):
        valid, _ = validate_mime_type("img.png", "image/png")
        assert valid is True

    def test_jpg_correct_mime(self):
        valid, _ = validate_mime_type("img.jpg", "image/jpeg")
        assert valid is True

    def test_jpeg_correct_mime(self):
        valid, _ = validate_mime_type("img.jpeg", "image/jpeg")
        assert valid is True

    def test_docx_correct_mime(self):
        valid, _ = validate_mime_type("doc.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        assert valid is True

    def test_txt_correct_mime(self):
        valid, _ = validate_mime_type("readme.txt", "text/plain")
        assert valid is True

    def test_none_content_type(self):
        valid, msg = validate_mime_type("doc.pdf", None)
        assert valid is False
        assert "application/pdf" in msg

    def test_unsupported_extension(self):
        valid, msg = validate_mime_type("script.py", "text/x-python")
        assert valid is False
        assert msg == ""

    def test_bmp_correct_mime(self):
        valid, _ = validate_mime_type("img.bmp", "image/bmp")
        assert valid is True

    def test_webp_correct_mime(self):
        valid, _ = validate_mime_type("img.webp", "image/webp")
        assert valid is True

    def test_gif_correct_mime(self):
        valid, _ = validate_mime_type("anim.gif", "image/gif")
        assert valid is True
