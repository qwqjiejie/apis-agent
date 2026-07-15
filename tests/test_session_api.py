from fastapi.testclient import TestClient

from src.dodo_agent.api.main import app
from src.dodo_agent.service.session_service import store

client = TestClient(app)


class TestSessionCreate:
    def test_create_session(self):
        resp = client.post("/session")
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 200
        assert data["data"]["conversationId"].startswith("sess_")
        assert len(data["data"]["conversationId"]) > 20

    def test_create_unique_ids(self):
        resp1 = client.post("/session")
        resp2 = client.post("/session")
        id1 = resp1.json()["data"]["conversationId"]
        id2 = resp2.json()["data"]["conversationId"]
        assert id1 != id2

    def test_token_format(self):
        """验证 session token 使用安全的随机格式生成"""
        for _ in range(5):
            resp = client.post("/session")
            cid = resp.json()["data"]["conversationId"]
            assert cid.startswith("sess_")
            token = cid[len("sess_"):]
            assert len(token) == 32


class TestSessionList:
    def test_list_returns_records(self):
        resp = client.get("/session/list")
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 200
        assert isinstance(data["data"]["records"], list)

    def test_list_pagination(self):
        resp = client.get("/session/list?pageNum=1&pageSize=10")
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["current"] == 1
        assert data["data"]["size"] == 10


class TestSessionGet:
    def test_nonexistent_returns_error(self):
        resp = client.get("/session/nonexistent_id")
        assert resp.json()["code"] == 404


class TestSessionDelete:
    def test_nonexistent_returns_error(self):
        resp = client.delete("/session/nonexistent_id")
        assert resp.json()["code"] == 404


class TestSessionWithData:
    """测试会话有数据时的完整生命周期"""

    def test_session_lifecycle(self):
        # 创建会话
        create_resp = client.post("/session")
        cid = create_resp.json()["data"]["conversationId"]

        # 存入消息（模拟 agent 执行后的持久化）
        store.save_message(
            session_id=cid,
            question="测试问题",
            answer="测试回答",
            agent_type="chat",
        )

        # 查询会话 — 能找到
        resp = client.get(f"/session/{cid}")
        assert resp.json()["code"] == 200
        assert resp.json()["data"]["conversationId"] == cid

        # 列表中有该会话
        list_resp = client.get("/session/list")
        records = list_resp.json()["data"]["records"]
        cids = [r["conversationId"] for r in records]
        assert cid in cids

        # 删除会话
        del_resp = client.delete(f"/session/{cid}")
        assert del_resp.json()["code"] == 200

        # 删除后查不到
        get_resp = client.get(f"/session/{cid}")
        assert get_resp.json()["code"] == 404
