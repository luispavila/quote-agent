from fastapi.testclient import TestClient

from app.main import app


def test_health_ok():
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "model" in body


def test_wa_webhook_requires_token(monkeypatch):
    from app import settings as settings_module

    monkeypatch.setenv("WA_SHARED_TOKEN", "segredo-de-teste-123")
    settings_module.get_settings.cache_clear()
    client = TestClient(app)

    resp = client.post("/webhooks/wa", json={"event": "connection.update", "status": "connected"})
    assert resp.status_code == 401

    resp = client.post(
        "/webhooks/wa",
        json={"event": "connection.update", "status": "connected"},
        headers={"x-wa-token": "segredo-de-teste-123"},
    )
    assert resp.status_code == 200
    settings_module.get_settings.cache_clear()


def test_chat_without_key_returns_503(monkeypatch):
    from app import settings as settings_module

    # env var vazia sobrepõe o .env local (dev pode ter chaves reais no arquivo)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("FEATHERLESS_API_KEY", "")
    settings_module.get_settings.cache_clear()
    client = TestClient(app)
    resp = client.post("/chat", json={"message": "oi"})
    assert resp.status_code == 503
    settings_module.get_settings.cache_clear()
