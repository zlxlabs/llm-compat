from __future__ import annotations

from fastapi.testclient import TestClient

from collector.app import create_app


TEST_API_KEY = "test-secret-key"


def make_client(api_key: str = TEST_API_KEY) -> TestClient:
    app = create_app(":memory:", api_key=api_key)
    return TestClient(app)


def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_API_KEY}"}


class TestAuth:
    def test_write_without_key_returns_401(self):
        client = make_client()
        resp = client.post("/words", json={"word": "test"})
        assert resp.status_code == 401

    def test_write_with_wrong_key_returns_401(self):
        client = make_client()
        resp = client.post("/words", json={"word": "test"}, headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401

    def test_refusal_without_key_returns_401(self):
        client = make_client()
        resp = client.post("/refusals", json={"model": "x", "error_type": "y"})
        assert resp.status_code == 401

    def test_read_without_key_allowed(self):
        client = make_client()
        assert client.get("/words").status_code == 200
        assert client.get("/words/hash").status_code == 200
        assert client.get("/stats").status_code == 200

    def test_no_api_key_configured_allows_all(self):
        client = make_client(api_key="")
        resp = client.post("/words", json={"word": "open"})
        assert resp.status_code == 201


class TestWords:
    def test_add_word_then_list(self):
        client = make_client()
        resp = client.post("/words", json={"word": "测试敏感词"}, headers=auth_headers())
        assert resp.status_code == 201

        resp = client.get("/words")
        assert resp.status_code == 200
        data = resp.json()
        assert "测试敏感词" in data["words"]
        assert data["count"] == 1
        assert "hash" in data

    def test_add_duplicate_word_returns_409(self):
        client = make_client()
        client.post("/words", json={"word": "重复词"}, headers=auth_headers())
        resp = client.post("/words", json={"word": "重复词"}, headers=auth_headers())
        assert resp.status_code == 409

    def test_delete_word(self):
        client = make_client()
        client.post("/words", json={"word": "待删除"}, headers=auth_headers())
        resp = client.delete("/words/待删除", headers=auth_headers())
        assert resp.status_code == 204

        resp = client.get("/words")
        assert "待删除" not in resp.json()["words"]


class TestWordHash:
    def test_hash_changes_when_words_change(self):
        client = make_client()
        h1 = client.get("/words/hash").json()["hash"]

        client.post("/words", json={"word": "新词"}, headers=auth_headers())
        h2 = client.get("/words/hash").json()["hash"]
        assert h1 != h2

        client.delete("/words/新词", headers=auth_headers())
        h3 = client.get("/words/hash").json()["hash"]
        assert h3 == h1

    def test_hash_endpoint_returns_only_hash(self):
        client = make_client()
        data = client.get("/words/hash").json()
        assert "hash" in data
        assert "words" not in data


class TestRefusals:
    def test_report_refusal_full(self):
        client = make_client()
        resp = client.post("/refusals", json={
            "model": "deepseek-v4",
            "provider": "deepseek",
            "source_project": "project-a",
            "request_id": "a1b2c3d4",
            "input_preview": "这是一段测试文本",
            "response_preview": "我无法回答这个问题",
            "message_count": 3,
            "has_images": False,
            "detection_layer": "http_error",
            "http_status": 500,
            "finish_reason": None,
            "fallback_model": "gpt-4.1-mini",
            "fallback_chain": ["deepseek-v4", "gpt-4.1-mini"],
        }, headers=auth_headers())
        assert resp.status_code == 201

    def test_report_refusal_shows_in_stats(self):
        client = make_client()
        client.post("/refusals", json={
            "model": "deepseek-v4",
            "provider": "deepseek",
            "detection_layer": "http_error",
            "http_status": 500,
            "input_preview": "测试内容",
            "response_preview": "",
            "fallback_model": "gpt-4.1-mini",
        }, headers=auth_headers())
        data = client.get("/stats").json()
        recent = data["recent_refusals"][0]
        assert recent["model"] == "deepseek-v4"
        assert recent["detection_layer"] == "http_error"
        assert recent["http_status"] == 500
        assert recent["fallback_model"] == "gpt-4.1-mini"

    def test_report_refusal_minimal(self):
        client = make_client()
        resp = client.post("/refusals", json={
            "model": "deepseek-v4",
            "detection_layer": "keyword_match",
        }, headers=auth_headers())
        assert resp.status_code == 201

    def test_report_missing_model_returns_422(self):
        client = make_client()
        resp = client.post("/refusals", json={"detection_layer": "http_error"}, headers=auth_headers())
        assert resp.status_code == 422


class TestStats:
    def test_stats_empty(self):
        client = make_client()
        resp = client.get("/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_refusals"] == 0
        assert data["word_count"] == 0

    def test_stats_with_data(self):
        client = make_client()
        h = auth_headers()
        client.post("/refusals", json={"model": "deepseek-v4", "detection_layer": "http_error"}, headers=h)
        client.post("/refusals", json={"model": "deepseek-v4", "detection_layer": "keyword_match"}, headers=h)
        client.post("/refusals", json={"model": "gpt-4.1", "detection_layer": "http_error"}, headers=h)
        client.post("/words", json={"word": "词1"}, headers=h)
        client.post("/words", json={"word": "词2"}, headers=h)

        data = client.get("/stats").json()
        assert data["total_refusals"] == 3
        assert data["word_count"] == 2
        assert data["refusals_by_model"]["deepseek-v4"] == 2
        assert data["refusals_by_model"]["gpt-4.1"] == 1

    def test_stats_recent_refusals(self):
        client = make_client()
        client.post("/refusals", json={
            "model": "deepseek-v4",
            "detection_layer": "keyword_match",
            "input_preview": "测试内容",
        }, headers=auth_headers())
        data = client.get("/stats").json()
        assert len(data["recent_refusals"]) == 1
        assert data["recent_refusals"][0]["model"] == "deepseek-v4"
        assert data["recent_refusals"][0]["input_preview"] == "测试内容"


class TestWordsDelete:
    def test_delete_nonexistent_word_returns_404(self):
        client = make_client()
        resp = client.delete("/words/不存在的词", headers=auth_headers())
        assert resp.status_code == 404
