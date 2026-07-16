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

from src.apis_agent.service.embedding_service import embed_texts, embedding_available
from src.apis_agent.config.settings import get_settings
from src.apis_agent.common.exceptions import FileTooLargeError, UnsupportedFileTypeError, InvalidMimeTypeError
from src.apis_agent.common.logger import logger
from src.apis_agent.document.document_event_bus import event_bus
from src.apis_agent.document.document_status import DocumentStatus, compute_file_hash
from src.apis_agent.storage.db import new_session
from src.apis_agent.storage.models.ai_file_info import AiFileInfo, FileInfoRepo
from src.apis_agent.storage.vector_store import vector_store
from src.apis_agent.utils.file_parser import parse_file, get_file_type, is_supported, validate_mime_type
from src.apis_agent.utils.text_splitter import split_text

logger = logging.getLogger("apis")


class FileService:

    def __init__(self):
        self._db_ok = new_session() is not None
        self._minio = self._build_minio()
        self._upload_dir = Path(get_settings().upload_dir)

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
        if not self._minio:
            return
        try:
            if not self._minio.bucket_exists(get_settings().minio_bucket):
                self._minio.make_bucket(get_settings().minio_bucket)
        except S3Error:
            pass

    # ---- upload ----

    async def upload(self, file: UploadFile, session_id: str = "") -> dict:
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
        dup_result = self._check_duplicate(file_hash, original_name)
        if dup_result:
            logger.info(f"[Document] 去重跳过: {original_name} (hash={file_hash[:16]})")
            return dup_result

        await event_bus.publish(file_id, DocumentStatus.UPLOADING.value, message="正在存储文件...", progress=10)

        # 存储
        minio_path = ""
        await event_bus.publish(file_id, DocumentStatus.STORING.value, message="存储中...", progress=20)

        local_path = self._save_local(file_id, original_name, content)
        try:
            if self._minio:
                try:
                    self._ensure_bucket()
                    date_prefix = datetime.now().strftime("%Y-%m-%d")
                    obj_name = f"upload_file/{date_prefix}/{original_name}"
                    self._minio.put_object(
                        get_settings().minio_bucket, obj_name,
                        data=io.BytesIO(content), length=file_size,
                    )
                    minio_path = obj_name
                except S3Error as e:
                    logger.error(f"MinIO 上传失败: {e}")

            # 文本解析
            await event_bus.publish(file_id, DocumentStatus.PARSING.value, message="正在解析文件内容...", progress=40)
            extracted_text = parse_file(local_path, original_name) if local_path else None

            # 文本分块 + 向量化
            embed_flag = 0
            if extracted_text and embedding_available() and vector_store.ready:
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

    def _check_duplicate(self, file_hash: str, filename: str) -> dict | None:
        """检查重复文件。返回非 None 表示已存在，跳过上传。"""
        if not self._db_ok:
            return None
        try:
            repo = FileInfoRepo()
            # 按文件名搜索已有记录（简单实现，后续可优化为哈希索引）
            rows, _ = repo.paginate(1, 1000)
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
        return vector_store.insert_chunks(file_id, chunks, vectors)

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

    def list_files(self, page: int = 1, size: int = 20) -> tuple[list[dict], int]:
        if not self._db_ok:
            return [], 0
        repo = FileInfoRepo()
        rows, total = repo.paginate(page, size, order_by=AiFileInfo.created_at.desc())
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

    def get_info(self, file_id: str) -> dict | None:
        if not self._db_ok:
            return None
        r = FileInfoRepo().find_by_file_id(file_id)
        if not r:
            return None
        return {
            "fileId": r.file_id, "fileName": r.file_name,
            "fileType": r.file_type, "fileSize": r.file_size,
            "status": r.status, "createdAt": str(r.created_at) if r.created_at else "",
            "conversationId": r.session_id or "",
        }

    # ---- content ----

    def get_content(self, file_id: str) -> dict | None:
        if not self._db_ok:
            return None
        r = FileInfoRepo().find_by_file_id(file_id)
        if not r:
            return None
        return {
            "fileId": r.file_id,
            "fileName": r.file_name,
            "extractedText": r.extracted_text or "",
        }

    # ---- delete ----

    def delete(self, file_id: str) -> bool:
        if self._db_ok:
            info = FileInfoRepo().find_by_file_id(file_id)
            if info:
                if self._minio and info.minio_path:
                    try:
                        self._minio.remove_object(get_settings().minio_bucket, info.minio_path)
                    except S3Error:
                        pass
                if vector_store.ready:
                    vector_store.delete_by_file(file_id)
                return FileInfoRepo().delete_by_file_id(file_id) > 0
        return False

    # ---- exists ----

    def exists(self, file_id: str) -> bool:
        if not self._db_ok:
            return False
        return FileInfoRepo().find_by_file_id(file_id) is not None


file_service = FileService()
