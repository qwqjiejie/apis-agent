"""兼容导出；新代码使用 app.modules.documents.status。"""

from app.modules.documents.status import DocumentStatus, compute_file_hash

__all__ = ["DocumentStatus", "compute_file_hash"]
