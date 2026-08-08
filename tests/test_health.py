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
    from app import settings as settings_module

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings_module.get_settings.cache_clear()
    client = TestClient(app)
    resp = client.post("/chat", json={"message": "oi"})
    assert resp.status_code in (502, 503)
    settings_module.get_settings.cache_clear()
