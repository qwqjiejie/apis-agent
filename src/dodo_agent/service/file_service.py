import io
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import UploadFile
from minio import Minio
from minio.error import S3Error

from src.dodo_agent.service.embedding_service import embed_texts, embedding_available
from src.dodo_agent.config.settings import settings
from src.dodo_agent.common.exceptions import FileTooLargeError, UnsupportedFileTypeError
from src.dodo_agent.common.logger import logger
from src.dodo_agent.storage.db import new_session
from src.dodo_agent.storage.models.ai_file_info import AiFileInfo, FileInfoRepo
from src.dodo_agent.storage.vector_store import vector_store
from src.dodo_agent.utils.file_parser import parse_file, get_file_type, is_supported
from src.dodo_agent.utils.text_splitter import split_text

logger = logging.getLogger("dodo")


class FileService:

    def __init__(self):
        self._db_ok = new_session() is not None
        self._minio = self._build_minio()
        self._upload_dir = Path(settings.upload_dir)

    def _build_minio(self) -> Minio | None:
        if not settings.minio_endpoint:
            return None
        try:
            return Minio(
                settings.minio_endpoint,
                access_key=settings.minio_access_key,
                secret_key=settings.minio_secret_key,
                secure=False,
            )
        except Exception:
            return None

    def _ensure_bucket(self):
        if not self._minio:
            return
        try:
            if not self._minio.bucket_exists(settings.minio_bucket):
                self._minio.make_bucket(settings.minio_bucket)
        except S3Error:
            pass

    # ---- upload ----

    def upload(self, file: UploadFile, conversation_id: str = "") -> dict:
        file_id = str(uuid.uuid4())
        original_name = file.filename or "unknown"
        file_type = get_file_type(original_name)
        if not is_supported(original_name):
            raise UnsupportedFileTypeError(file_type)

        content = file.file.read()
        file_size = len(content)
        if file_size > settings.max_upload_size_mb * 1024 * 1024:
            raise FileTooLargeError(settings.max_upload_size_mb)

        local_path = ""
        minio_path = ""

        local_path = self._save_local(file_id, original_name, content)
        if self._minio:
            try:
                self._ensure_bucket()
                date_prefix = datetime.now().strftime("%Y-%m-%d")
                obj_name = f"upload_file/{date_prefix}/{original_name}"
                self._minio.put_object(
                    settings.minio_bucket, obj_name,
                    data=io.BytesIO(content), length=file_size,
                )
                minio_path = obj_name
            except S3Error as e:
                logger.error(f"MinIO 上传失败: {e}")

        extracted_text = parse_file(local_path, original_name) if local_path else None

        embed_flag = 0
        if extracted_text and embedding_available() and vector_store.ready:
            embed_flag = self._vectorize(file_id, extracted_text)

        if local_path:
            self._remove_local(local_path)

        if self._db_ok:
            try:
                FileInfoRepo().save(AiFileInfo(
                    file_id=file_id,
                    file_name=original_name,
                    file_type=file_type,
                    file_size=file_size,
                    minio_path=minio_path or None,
                    extracted_text=extracted_text,
                    conversation_id=conversation_id or None,
                    status="SUCCESS" if extracted_text else "PENDING",
                    created_at=datetime.now(),
                    update_time=datetime.now(),
                    embed=embed_flag,
                ))
            except Exception as e:
                logger.error(f"保存文件记录失败: {e}")

        return {
            "fileId": file_id,
            "fileName": original_name,
            "fileType": file_type,
            "fileSize": file_size,
            "status": "SUCCESS" if extracted_text else "PENDING",
            "extractedText": extracted_text or "",
        }

    def _vectorize(self, file_id: str, text: str) -> int:
        chunks = split_text(text)
        if not chunks:
            return 0
        vectors = embed_texts(chunks)
        if not vectors:
            return 0
        return vector_store.insert_chunks(file_id, chunks, vectors)

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

    def list_files(self) -> list[dict]:
        if not self._db_ok:
            return []
        repo = FileInfoRepo()
        rows = repo.find_all(order_by=AiFileInfo.created_at.desc())
        return [
            {
                "fileId": r.file_id, "fileName": r.file_name,
                "fileType": r.file_type, "fileSize": r.file_size,
                "status": r.status, "createdAt": str(r.created_at) if r.created_at else "",
            }
            for r in rows
        ]

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
            "conversationId": r.conversation_id or "",
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
                        self._minio.remove_object(settings.minio_bucket, info.minio_path)
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
