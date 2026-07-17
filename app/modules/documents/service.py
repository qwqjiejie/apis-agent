import asyncio
import io
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import UploadFile
from minio import Minio
from minio.error import S3Error

from app.service.embedding_service import embed_texts, embedding_available
from app.config.settings import get_settings
from app.common.exceptions import FileTooLargeError, UnsupportedFileTypeError, InvalidMimeTypeError
from app.common.logger import logger
from app.modules.documents.events import event_bus
from app.modules.documents.status import DocumentStatus, compute_file_hash
from app.storage.models.ai_file_info import AiFileInfo, FileInfoRepo
from app.storage.vector_store import vector_store
from app.modules.documents.chunking import split_text
from app.modules.documents.parsing import (
    get_file_type,
    is_supported,
    parse_file,
    validate_mime_type,
)

logger = logging.getLogger("apis")


class FileService:

    def __init__(
        self,
        *,
        vector_store_instance=None,
        minio_client: Minio | None = None,
        db_available: bool = True,
    ):
        self._db_ok = db_available
        self._minio = minio_client
        self._minio_initialized = minio_client is not None
        self._vector_store = vector_store_instance or vector_store
        self._upload_dir = Path(get_settings().upload_dir)

    def configure(self, *, minio_client: Minio | None, db_available: bool = True):
        """由应用生命周期注入已初始化的基础设施。"""
        self._db_ok = db_available
        self._minio = minio_client
        self._minio_initialized = True

    def _get_minio(self) -> Minio | None:
        if not self._minio_initialized:
            self._minio = self._build_minio()
            self._minio_initialized = True
        return self._minio

    def _build_minio(self) -> Minio | None:
        if not get_settings().minio_host:
            return None
        try:
            return Minio(
                f"{get_settings().minio_host}:{get_settings().minio_port}",
                access_key=get_settings().minio_access_key,
                secret_key=get_settings().minio_secret_key,
                secure=False,
            )
        except Exception:
            return None

    def _ensure_bucket(self):
        minio = self._get_minio()
        if not minio:
            return
        try:
            if not minio.bucket_exists(get_settings().minio_bucket):
                minio.make_bucket(get_settings().minio_bucket)
        except S3Error:
            pass

    # ---- upload ----

    async def upload(
        self,
        file: UploadFile,
        session_id: str = "",
        *,
        user_id: str,
    ) -> dict:
        original_name = file.filename or "unknown"
        file_type = get_file_type(original_name)
        if not is_supported(original_name):
            raise UnsupportedFileTypeError(file_type)

        mime_valid, expected_mime = validate_mime_type(original_name, file.content_type)
        if not mime_valid:
            raise InvalidMimeTypeError(expected_mime, file.content_type or "")

        content = file.file.read()
        file_size = len(content)
        if file_size > get_settings().max_upload_size_mb * 1024 * 1024:
            raise FileTooLargeError(get_settings().max_upload_size_mb)

        # SHA-256 去重检查
        file_hash = compute_file_hash(content)
        file_id = str(uuid.uuid4())
        dup_result = self._check_duplicate(file_hash, original_name, user_id)
        if dup_result:
            logger.info(f"[Document] 去重跳过: {original_name} (hash={file_hash[:16]})")
            return dup_result

        await event_bus.publish(file_id, DocumentStatus.UPLOADING.value, message="正在存储文件...", progress=10)

        # 存储
        minio_path = ""
        await event_bus.publish(file_id, DocumentStatus.STORING.value, message="存储中...", progress=20)

        local_path = self._save_local(file_id, original_name, content)
        try:
            minio = self._get_minio()
            if minio:
                try:
                    self._ensure_bucket()
                    date_prefix = datetime.now().strftime("%Y-%m-%d")
                    obj_name = f"upload_file/{date_prefix}/{original_name}"
                    minio.put_object(
                        get_settings().minio_bucket, obj_name,
                        data=io.BytesIO(content), length=file_size,
                    )
                    minio_path = obj_name
                except S3Error as e:
                    logger.error(f"MinIO 上传失败: {e}")

            # 文本解析（PDF 优先走 MinerU，否则用默认解析器）
            await event_bus.publish(file_id, DocumentStatus.PARSING.value, message="正在解析文件内容...", progress=40)
            extracted_text = None
            if local_path:
                if file_type == "pdf":
                    from app.readers.mineru_reader import mineru_reader
                    logger.info(f"[FileService] 尝试 MinerU 解析: {original_name}")
                    extracted_text = await mineru_reader.parse(local_path)
                if not extracted_text:
                    extracted_text = parse_file(local_path, original_name)

            # 文本分块 + 向量化
            embed_flag = 0
            if extracted_text and embedding_available() and self._vector_store.ready:
                await event_bus.publish(file_id, DocumentStatus.SPLITTING.value, message="正在文本分块...", progress=60)
                await event_bus.publish(file_id, DocumentStatus.INDEXING.value, message="正在写入向量索引...", progress=75)
                embed_flag = await self._vectorize_async(file_id, extracted_text)
        finally:
            self._remove_local(local_path)

        final_status = DocumentStatus.READY if extracted_text else DocumentStatus.FAILED

        if self._db_ok:
            try:
                now = datetime.now(timezone.utc)
                FileInfoRepo().save(AiFileInfo(
                    file_id=file_id,
                    file_name=original_name,
                    file_type=file_type,
                    file_size=file_size,
                    file_hash=file_hash,
                    minio_path=minio_path or None,
                    extracted_text=extracted_text,
                    status=final_status.value,
                    embed=bool(embed_flag),
                    session_id=session_id or None,
                    user_id=user_id,
                    created_at=now,
                    updated_at=now,
                ))
            except Exception as e:
                logger.error(f"保存文件记录失败: {e}")

        await event_bus.publish(file_id, final_status.value, message="处理完成", progress=100)

        return {
            "fileId": file_id,
            "fileName": original_name,
            "fileType": file_type,
            "fileSize": file_size,
            "fileHash": file_hash,
            "status": final_status.value,
            "extractedText": extracted_text or "",
        }

    def _check_duplicate(
        self,
        file_hash: str,
        filename: str,
        user_id: str,
    ) -> dict | None:
        """检查重复文件。返回非 None 表示已存在，跳过上传。"""
        if not self._db_ok:
            return None
        try:
            repo = FileInfoRepo()
            # 按文件名搜索已有记录（简单实现，后续可优化为哈希索引）
            rows, _ = repo.paginate(1, 1000, AiFileInfo.user_id == user_id)
            for r in rows:
                if r.file_name == filename:
                    return {  # 同名文件当作替换处理 — 删除旧记录
                        "fileId": r.file_id,
                        "fileName": r.file_name,
                        "fileType": r.file_type,
                        "fileSize": r.file_size,
                        "fileHash": file_hash,
                        "status": DocumentStatus.SKIPPED.value,
                        "message": "同名文件已存在，请先删除再上传",
                    }
                    logger.info(f"[Document] 同名文件 '{filename}'，需删除后重新上传")
        except Exception:
            pass
        return None

    def _vectorize(self, file_id: str, text: str) -> int:
        chunks = split_text(text)
        if not chunks:
            return 0
        vectors = embed_texts(chunks)
        if not vectors:
            return 0
        return self._vector_store.insert_chunks(file_id, chunks, vectors)

    async def _vectorize_async(self, file_id: str, text: str) -> int:
        """异步向量化（在线程池中执行，避免阻塞事件循环）。"""
        return await asyncio.to_thread(self._vectorize, file_id, text)

    def _remove_local(self, path: str):
        try:
            os.remove(path)
        except OSError:
            pass

    def _save_local(self, file_id: str, filename: str, content: bytes) -> str:
        self._upload_dir.mkdir(parents=True, exist_ok=True)
        ext = os.path.splitext(filename)[1]
        save_path = self._upload_dir / f"{file_id}{ext}"
        save_path.write_bytes(content)
        return str(save_path)

    # ---- list ----

    def list_files(
        self,
        page: int = 1,
        size: int = 20,
        *,
        user_id: str,
    ) -> tuple[list[dict], int]:
        if not self._db_ok:
            return [], 0
        repo = FileInfoRepo()
        rows, total = repo.paginate(
            page,
            size,
            AiFileInfo.user_id == user_id,
            order_by=AiFileInfo.created_at.desc(),
        )
        records = [
            {
                "fileId": r.file_id, "fileName": r.file_name,
                "fileType": r.file_type, "fileSize": r.file_size,
                "status": r.status, "createdAt": str(r.created_at) if r.created_at else "",
            }
            for r in rows
        ]
        return records, total

    # ---- info ----

    def get_info(self, file_id: str, *, user_id: str) -> dict | None:
        if not self._db_ok:
            return None
        r = FileInfoRepo().find_one(
            AiFileInfo.file_id == file_id,
            AiFileInfo.user_id == user_id,
        )
        if not r:
            return None
        return {
            "fileId": r.file_id, "fileName": r.file_name,
            "fileType": r.file_type, "fileSize": r.file_size,
            "status": r.status, "createdAt": str(r.created_at) if r.created_at else "",
            "conversationId": r.session_id or "",
        }

    # ---- content ----

    def get_content(self, file_id: str, *, user_id: str) -> dict | None:
        if not self._db_ok:
            return None
        r = FileInfoRepo().find_one(
            AiFileInfo.file_id == file_id,
            AiFileInfo.user_id == user_id,
        )
        if not r:
            return None
        return {
            "fileId": r.file_id,
            "fileName": r.file_name,
            "extractedText": r.extracted_text or "",
        }

    # ---- delete ----

    def delete(self, file_id: str, *, user_id: str) -> bool:
        if self._db_ok:
            info = FileInfoRepo().find_one(
                AiFileInfo.file_id == file_id,
                AiFileInfo.user_id == user_id,
            )
            if info:
                minio = self._get_minio()
                if minio and info.minio_path:
                    try:
                        minio.remove_object(get_settings().minio_bucket, info.minio_path)
                    except S3Error:
                        pass
                if self._vector_store.ready:
                    self._vector_store.delete_by_file(file_id)
                return FileInfoRepo().delete_by(
                    AiFileInfo.file_id == file_id,
                    AiFileInfo.user_id == user_id,
                ) > 0
        return False

    # ---- exists ----

    def exists(self, file_id: str, *, user_id: str) -> bool:
        if not self._db_ok:
            return False
        return FileInfoRepo().find_one(
            AiFileInfo.file_id == file_id,
            AiFileInfo.user_id == user_id,
        ) is not None


file_service = FileService()
