import hashlib
from enum import Enum


class DocumentStatus(str, Enum):
    """文档处理管线状态。"""
    UPLOADING = "uploading"       # 正在接收上传
    STORING = "storing"           # 正在存储 (MinIO/本地)
    PARSING = "parsing"           # 正在解析文本 (PDF/DOCX/OCR)
    SPLITTING = "splitting"       # 正在文本分块
    INDEXING = "indexing"         # 正在写入 Milvus
    READY = "ready"               # 处理完成
    FAILED = "failed"             # 处理失败
    SKIPPED = "skipped"           # 去重跳过


def compute_file_hash(data: bytes) -> str:
    """计算文件内容的 SHA-256 哈希。"""
    return hashlib.sha256(data).hexdigest()
