import hashlib
import hmac
import json

from app.api.webhooks import sentry as sentry_module


def _make_signature(payload: str, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_sentry_webhook_invalid_hmac_returns_400(client, monkeypatch):
    monkeypatch.setenv("SENTRY_WEBHOOK_SECRET", "sentry_secret")
    payload = {"id": "evt_invalid", "level": "error", "user_count": 2}
    response = client.post(
        "/webhooks/sentry",
        json=payload,
        headers={"sentry-hook-signature": "sha256=deadbeef"},
    )
    assert response.status_code == 400
    assert "Invalid signature" in response.json()["detail"]


def test_sentry_webhook_filters_non_actionable_events(client, monkeypatch):
    monkeypatch.setenv("SENTRY_WEBHOOK_SECRET", "sentry_secret")
    payload = {"id": "evt_ignore", "level": "warning", "user_count": 10}
    payload_s = json.dumps(payload)
    sig = _make_signature(payload_s, "sentry_secret")
    response = client.post(
        "/webhooks/sentry",
        content=payload_s,
        headers={"Content-Type": "application/json", "sentry-hook-signature": sig},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


def test_sentry_webhook_valid_error_sends_slack_under_processing_flow(client, fake_redis, monkeypatch):
    monkeypatch.setenv("SENTRY_WEBHOOK_SECRET", "sentry_secret")
    monkeypatch.setenv("SLACK_PLATFORM_HEALTH_WEBHOOK_URL", "https://example.invalid/slack")
    monkeypatch.setattr(sentry_module, "get_redis", lambda: fake_redis, raising=True)

    called = {"triage": 0, "slack": 0}

    async def _fake_triage(stack_trace: str, context: str, *, max_tokens: int = 800):
        called["triage"] += 1
        return {"root_cause": "db timeout", "suggested_fix": "increase pool"}

    async def _fake_slack_send(text: str) -> None:
        called["slack"] += 1
        assert "root_cause: db timeout" in text

    monkeypatch.setattr(sentry_module, "triage_sentry_error", _fake_triage, raising=True)
    monkeypatch.setattr(sentry_module, "send_slack_platform_health_message", _fake_slack_send, raising=True)

    payload = {
        "id": "evt_valid_1",
        "level": "error",
        "message": "API timeout spike",
        "user_count": 3,
        "stacktrace": "Traceback: ...",
    }
    payload_s = json.dumps(payload)
    sig = _make_signature(payload_s, "sentry_secret")
    response = client.post(
        "/webhooks/sentry",
        content=payload_s,
        headers={"Content-Type": "application/json", "sentry-hook-signature": sig},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["event_id"] == "evt_valid_1"
    assert called["triage"] == 1
    assert called["slack"] == 1


def test_sentry_webhook_duplicate_event_returns_200(client, fake_redis, monkeypatch):
    monkeypatch.setenv("SENTRY_WEBHOOK_SECRET", "sentry_secret")
    monkeypatch.setenv("SLACK_PLATFORM_HEALTH_WEBHOOK_URL", "https://example.invalid/slack")
    monkeypatch.setattr(sentry_module, "get_redis", lambda: fake_redis, raising=True)

    called = {"triage": 0}

    async def _fake_triage(stack_trace: str, context: str, *, max_tokens: int = 800):
        called["triage"] += 1
        return {"root_cause": "x", "suggested_fix": "y"}

    async def _fake_slack_send(text: str) -> None:
        return None

    monkeypatch.setattr(sentry_module, "triage_sentry_error", _fake_triage, raising=True)
    monkeypatch.setattr(sentry_module, "send_slack_platform_health_message", _fake_slack_send, raising=True)

    payload = {"id": "evt_dup_1", "level": "error", "user_count": 2, "message": "boom"}
    payload_s = json.dumps(payload)
    sig = _make_signature(payload_s, "sentry_secret")

    first = client.post(
        "/webhooks/sentry",
        content=payload_s,
        headers={"Content-Type": "application/json", "sentry-hook-signature": sig},
    )
    assert first.status_code == 200

    second = client.post(
        "/webhooks/sentry",
        content=payload_s,
        headers={"Content-Type": "application/json", "sentry-hook-signature": sig},
    )
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"
    assert called["triage"] == 1
