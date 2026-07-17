import logging

from pymilvus import MilvusClient, DataType

from app.config.settings import get_settings
from app.service.embedding_service import embedding_dim

logger = logging.getLogger("apis")

COLLECTION_NAME = "file_chunks"

FIELD_ID = "id"
FIELD_FILE_ID = "file_id"
FIELD_CHUNK_IDX = "chunk_idx"
FIELD_TEXT = "text"
FIELD_VECTOR = "vector"


class VectorStore:

    def __init__(self):
        self._client: MilvusClient | None = None
        self._ready = False
        self._connect_error: str | None = None
        if get_settings().milvus_host:
            self._connect()

    def _connect(self):
        try:
            uri = f"http://{get_settings().milvus_host}:{get_settings().milvus_port}"
            token = f"{get_settings().milvus_user}:{get_settings().milvus_pass}" if get_settings().milvus_user else None

            self._client = MilvusClient(uri=uri, token=token)
            db_name = get_settings().milvus_db or "default"
            if db_name != "default":
                dbs = [d for d in self._client.list_databases()]
                if db_name not in dbs:
                    self._client.create_database(db_name)
                    logger.info(f"Milvus database '{db_name}' 已创建")
                self._client.use_database(db_name)
            self._ensure_collection()
            self._ready = True
            logger.info(f"Milvus 连接成功: {uri}, db={db_name}")
        except Exception as e:
            self._connect_error = str(e)
            self._client = None
            self._ready = False

    def check_milvus(self):
        """启动时强制检查 Milvus 连接，失败则抛出 MilvusError。"""
        if not get_settings().milvus_host:
            return
        if not self._ready:
            from app.common.exceptions import MilvusError
            raise MilvusError(f"Milvus 连接失败: {self._connect_error}")

    def _ensure_collection(self):
        if self._client.has_collection(COLLECTION_NAME):
            self._client.load_collection(COLLECTION_NAME)
            return
        dim = embedding_dim()
        try:
            schema = self._client.create_schema(
                auto_id=False,
                enable_dynamic_field=True,
            )
            schema.add_field(field_name="id", datatype=DataType.VARCHAR, is_primary=True, max_length=128)
            schema.add_field(field_name="vector", datatype=DataType.FLOAT_VECTOR, dim=dim)
            index_params = self._client.prepare_index_params()
            index_params.add_index(field_name="vector", index_type="IVF_FLAT", metric_type="COSINE", params={"nlist": 128})
            self._client.create_collection(
                collection_name=COLLECTION_NAME,
                schema=schema,
                index_params=index_params,
            )
            self._client.load_collection(COLLECTION_NAME)
            logger.info(f"Milvus collection '{COLLECTION_NAME}' 已创建, dim={dim}")
        except Exception as e:
            logger.warning(f"创建 collection 失败: {e}")

    @property
    def ready(self) -> bool:
        return self._ready

    def insert_chunks(self, file_id: str, chunks: list[str], vectors: list[list[float]]) -> int:
        if not self._ready:
            return 0
        data = []
        for i, (chunk, vec) in enumerate(zip(chunks, vectors)):
            data.append({
                "id": f"{file_id}_{i}",
                "vector": vec,
                FIELD_FILE_ID: file_id,
                FIELD_CHUNK_IDX: i,
                FIELD_TEXT: chunk[:2000],
            })
        try:
            self._client.insert(collection_name=COLLECTION_NAME, data=data)
            logger.info(f"Milvus 写入 {len(data)} 条, file_id={file_id}")
            return len(data)
        except Exception as e:
            logger.error(f"Milvus 写入失败: {e}")
            return 0

    def search(self, query_vector: list[float], top_k: int = 5) -> list[dict]:
        if not self._ready:
            return []
        try:
            self._client.load_collection(COLLECTION_NAME)
            results = self._client.search(
                collection_name=COLLECTION_NAME,
                data=[query_vector],
                limit=top_k,
                output_fields=[FIELD_FILE_ID, FIELD_TEXT, FIELD_CHUNK_IDX],
            )
            if not results or not results[0]:
                return []
            return [
                {"text": r["entity"].get(FIELD_TEXT, ""), "file_id": r["entity"].get(FIELD_FILE_ID, ""),
                 "score": r["distance"], "chunk_idx": r["entity"].get(FIELD_CHUNK_IDX, 0)}
                for r in results[0]
            ]
        except Exception as e:
            logger.error(f"Milvus 搜索失败: {e}")
            return []

    def delete_by_file(self, file_id: str):
        if not self._ready:
            return
        try:
            self._client.delete(collection_name=COLLECTION_NAME, filter=f'{FIELD_FILE_ID} == "{file_id}"')
        except Exception as e:
            logger.error(f"Milvus 删除失败: {e}")

    def close(self):
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass


vector_store = VectorStore()
