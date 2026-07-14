from typing import TypeVar, Generic
from sqlalchemy import func, select, delete
from sqlalchemy.orm import Session
from src.storage.db import new_session

M = TypeVar("M")


class BaseRepository(Generic[M]):
    model: type[M]

    def __init__(self, session: Session | None = None):
        self._s = session or new_session()
        self._own_session = session is None

    def _execute(self):
        """子类可覆盖此方法获取 session，自动管理事务"""
        return self._s

    def save(self, entity: M) -> M:
        self._s.add(entity)
        self._s.flush()
        if self._own_session:
            self._s.commit()
        return entity

    def save_all(self, entities: list[M]) -> list[M]:
        self._s.add_all(entities)
        if self._own_session:
            self._s.commit()
        return entities

    def delete(self, entity: M) -> None:
        self._s.delete(entity)
        if self._own_session:
            self._s.commit()

    def delete_by(self, *where) -> int:
        result = self._s.execute(delete(self.model).where(*where))
        if self._own_session:
            self._s.commit()
        return result.rowcount

    def find_by(self, *where, order_by=None) -> list[M]:
        stmt = select(self.model).where(*where)
        if order_by is not None:
            stmt = stmt.order_by(order_by)
        return list(self._s.execute(stmt).scalars().all())

    def find_one(self, *where) -> M | None:
        return self._s.execute(select(self.model).where(*where)).scalars().first()

    def find_all(self, order_by=None, limit: int | None = None, offset: int | None = None) -> list[M]:
        stmt = select(self.model)
        if order_by is not None:
            stmt = stmt.order_by(order_by)
        if limit is not None:
            stmt = stmt.limit(limit)
        if offset is not None:
            stmt = stmt.offset(offset)
        return list(self._s.execute(stmt).scalars().all())

    def count(self, *where) -> int:
        return self._s.query(func.count()).select_from(self.model).where(*where).scalar()

    def paginate(self, page: int = 1, size: int = 20, *where, order_by=None) -> tuple[list[M], int]:
        total = self.count(*where)
        stmt = select(self.model).where(*where)
        if order_by is not None:
            stmt = stmt.order_by(order_by)
        stmt = stmt.offset((page - 1) * size).limit(size)
        rows = list(self._s.execute(stmt).scalars().all())
        return rows, total

    def close(self):
        if self._own_session:
            self._s.close()
