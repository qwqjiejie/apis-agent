import pytest
from datetime import datetime
from sqlalchemy import create_engine, String, Integer, Text, DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session

from app.infrastructure.postgres.repository import BaseRepository


class TestBase(DeclarativeBase):
    __test__ = False


class TestEntity(TestBase):
    __tablename__ = "test_entity"
    __test__ = False

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(DateTime)


class TestEntityRepo(BaseRepository[TestEntity]):
    __test__ = False
    model = TestEntity

    def find_by_name(self, name: str) -> list[TestEntity]:
        return self.find_by(TestEntity.name == name)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    TestBase.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def repo(session):
    return TestEntityRepo(session=session)


class TestBaseRepositorySave:
    def test_save_single(self, repo, session):
        entity = TestEntity(name="test", value="hello")
        saved = repo.save(entity)
        assert saved.id is not None
        assert saved.name == "test"

    def test_save_all(self, repo, session):
        entities = [
            TestEntity(name="a", value="1"),
            TestEntity(name="b", value="2"),
        ]
        saved = repo.save_all(entities)
        assert len(saved) == 2
        session.flush()
        assert saved[0].id is not None


class TestBaseRepositoryFind:
    def test_find_all(self, repo, session):
        repo.save(TestEntity(name="a"))
        repo.save(TestEntity(name="b"))
        rows = repo.find_all()
        assert len(rows) == 2

    def test_find_all_with_limit(self, repo, session):
        repo.save(TestEntity(name="a"))
        repo.save(TestEntity(name="b"))
        repo.save(TestEntity(name="c"))
        rows = repo.find_all(limit=2)
        assert len(rows) == 2

    def test_find_all_with_order_by(self, repo, session):
        repo.save(TestEntity(name="b"))
        repo.save(TestEntity(name="a"))
        rows = repo.find_all(order_by=TestEntity.name.asc())
        assert rows[0].name == "a"
        assert rows[1].name == "b"

    def test_find_by(self, repo, session):
        repo.save(TestEntity(name="foo"))
        repo.save(TestEntity(name="bar"))
        rows = repo.find_by(TestEntity.name == "foo")
        assert len(rows) == 1
        assert rows[0].name == "foo"

    def test_find_one_exists(self, repo, session):
        repo.save(TestEntity(name="target"))
        row = repo.find_one(TestEntity.name == "target")
        assert row is not None
        assert row.name == "target"

    def test_find_one_not_exists(self, repo, session):
        row = repo.find_one(TestEntity.name == "missing")
        assert row is None


class TestBaseRepositoryDelete:
    def test_delete_entity(self, repo, session):
        entity = repo.save(TestEntity(name="to_delete"))
        repo.delete(entity)
        assert repo.find_one(TestEntity.name == "to_delete") is None

    def test_delete_by(self, repo, session):
        repo.save(TestEntity(name="batch_a"))
        repo.save(TestEntity(name="batch_a"))
        repo.save(TestEntity(name="batch_b"))
        count = repo.delete_by(TestEntity.name == "batch_a")
        assert count == 2
        assert len(repo.find_by(TestEntity.name == "batch_b")) == 1


class TestBaseRepositoryCount:
    def test_count_empty(self, repo, session):
        assert repo.count() == 0

    def test_count_with_rows(self, repo, session):
        repo.save(TestEntity(name="a"))
        repo.save(TestEntity(name="b"))
        assert repo.count() == 2

    def test_count_with_where(self, repo, session):
        repo.save(TestEntity(name="x"))
        repo.save(TestEntity(name="y"))
        repo.save(TestEntity(name="x"))
        assert repo.count(TestEntity.name == "x") == 2


class TestBaseRepositoryPaginate:
    def test_paginate(self, repo, session):
        for i in range(10):
            repo.save(TestEntity(name=f"item_{i}"))
        rows, total = repo.paginate(page=1, size=3)
        assert len(rows) == 3
        assert total == 10

    def test_paginate_last_page(self, repo, session):
        for i in range(5):
            repo.save(TestEntity(name=f"item_{i}"))
        rows, total = repo.paginate(page=2, size=3)
        assert len(rows) == 2
        assert total == 5
