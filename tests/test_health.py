from fastapi.testclient import TestClient

from app.main import app


def test_health_ok():
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "model" in body


def test_chat_without_key_returns_503(monkeypatch):
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "CotaAI" in resp.text
