import logging
import os

logger = logging.getLogger("apis")

MAX_TEXT_LENGTH = 50000

SUPPORTED_TYPES = {
    "pdf", "doc", "docx", "txt", "png", "jpg", "jpeg", "gif", "bmp", "webp",
}

ALLOWED_MIME_TYPES = {
    "pdf":  ["application/pdf"],
    "doc":  ["application/msword"],
    "docx": ["application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
    "txt":  ["text/plain"],
    "png":  ["image/png"],
    "jpg":  ["image/jpeg"],
    "jpeg": ["image/jpeg"],
    "gif":  ["image/gif"],
    "bmp":  ["image/bmp", "image/x-bmp"],
    "webp": ["image/webp"],
}


def get_file_type(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower().lstrip(".")
    return ext


def is_supported(filename: str) -> bool:
    return get_file_type(filename) in SUPPORTED_TYPES


def validate_mime_type(filename: str, content_type: str | None) -> tuple[bool, str]:
    """验证文件的 MIME 类型是否与扩展名匹配。返回 (is_valid, expected_types_str)。"""
    ext = get_file_type(filename)
    allowed = ALLOWED_MIME_TYPES.get(ext, [])
    if not allowed:
        return False, ""
    if not content_type:
        return False, ", ".join(allowed)
    if content_type not in allowed:
        return False, ", ".join(allowed)
    return True, ""


def parse_file(file_path: str, filename: str) -> str | None:
    ext = get_file_type(filename)
    try:
        if ext == "pdf":
            return _parse_pdf(file_path)
        elif ext in ("doc", "docx"):
            return _parse_docx(file_path)
        elif ext == "txt":
            return _parse_txt(file_path)
        else:
            return _parse_image(file_path)
    except Exception as e:
        logger.error(f"解析文件失败 {filename}: {e}")
        return None


def _parse_pdf(file_path: str) -> str:
    import pdfplumber
    texts = []
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                texts.append(t)
    return "\n".join(texts)[:MAX_TEXT_LENGTH]


def _parse_docx(file_path: str) -> str:
    from docx import Document
    doc = Document(file_path)
    texts = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n".join(texts)[:MAX_TEXT_LENGTH]


def _parse_txt(file_path: str) -> str:
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()[:MAX_TEXT_LENGTH]


def _parse_image(file_path: str) -> str | None:
    from src.apis_agent.utils.image_recognition import recognize_image
    return recognize_image(file_path)
