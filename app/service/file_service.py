"""兼容导出；新代码使用 app.modules.documents.service。"""

from app.modules.documents.service import FileService, file_service

__all__ = ["FileService", "file_service"]
