from fastapi.testclient import TestClient

from src.apis_agent.api.main import app
from src.apis_agent.service.session_service import store

client = TestClient(app)

API = "/api/v1"


class TestSessionCreate:
    def test_create_session(self):
        resp = client.post(f"{API}/session")
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 200
        assert data["data"]["conversationId"].startswith("sess_")
        assert len(data["data"]["conversationId"]) > 20

    def test_create_unique_ids(self):
        resp1 = client.post(f"{API}/session")
        resp2 = client.post(f"{API}/session")
        id1 = resp1.json()["data"]["conversationId"]
        id2 = resp2.json()["data"]["conversationId"]
        assert id1 != id2

    def test_token_format(self):
        for _ in range(5):
            resp = client.post(f"{API}/session")
            cid = resp.json()["data"]["conversationId"]
            assert cid.startswith("sess_")
            token = cid[len("sess_"):]
            assert len(token) == 32


class TestSessionList:
    def test_list_returns_records(self):
        resp = client.post(f"{API}/session/list", json={"pageNum": 1, "pageSize": 100})
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 200
        assert isinstance(data["data"]["records"], list)

    def test_list_pagination(self):
        resp = client.post(f"{API}/session/list", json={"pageNum": 1, "pageSize": 10})
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["current"] == 1
        assert data["data"]["size"] == 10

    def test_list_max_page_size(self):
        resp = client.post(f"{API}/session/list", json={"pageNum": 1, "pageSize": 101})
        assert resp.status_code == 422


class TestSessionDetail:
    def test_nonexistent_returns_error(self):
        resp = client.post(f"{API}/session/detail", json={"conversationId": "nonexistent_id"})
        assert resp.json()["code"] == 404


class TestSessionDelete:
    def test_nonexistent_returns_error(self):
        resp = client.post(f"{API}/session/delete", json={"conversationId": "nonexistent_id"})
        assert resp.json()["code"] == 404


class TestSessionWithData:
    def test_session_lifecycle(self):
        create_resp = client.post(f"{API}/session")
        cid = create_resp.json()["data"]["conversationId"]

        store.save_message(
            session_id=cid,
            question="测试问题",
            answer="测试回答",
            agent_type="chat",
        )

        resp = client.post(f"{API}/session/detail", json={"conversationId": cid})
        assert resp.json()["code"] == 200
        assert resp.json()["data"]["conversationId"] == cid

        list_resp = client.post(f"{API}/session/list", json={"pageNum": 1, "pageSize": 100})
        records = list_resp.json()["data"]["records"]
        cids = [r["conversationId"] for r in records]
        assert cid in cids

        del_resp = client.post(f"{API}/session/delete", json={"conversationId": cid})
        assert del_resp.json()["code"] == 200

        get_resp = client.post(f"{API}/session/detail", json={"conversationId": cid})
        assert get_resp.json()["code"] == 404
