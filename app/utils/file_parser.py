"""兼容导出；新代码使用 app.modules.documents.parsing。"""

from app.modules.documents.parsing import (
    ALLOWED_MIME_TYPES,
    MAX_TEXT_LENGTH,
    SUPPORTED_TYPES,
    get_file_type,
    is_supported,
    parse_file,
    validate_mime_type,
)

__all__ = [
    "ALLOWED_MIME_TYPES",
    "MAX_TEXT_LENGTH",
    "SUPPORTED_TYPES",
    "get_file_type",
    "is_supported",
    "parse_file",
    "validate_mime_type",
]
