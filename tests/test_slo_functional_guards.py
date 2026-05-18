from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path

import jwt

from app.api.webhooks import sentry as sentry_module


def _make_admin_token(*, private_pem: str, jti: str, admin_role: str, exp_seconds: int = 3600) -> str:
    now = int(time.time())
    payload: dict[str, object] = {
        "sub": "slo-admin",
        "jti": jti,
        "exp": now + exp_seconds,
        "admin_role": admin_role,
    }
    return jwt.encode(payload, private_pem, algorithm="RS256")


def _sentry_sig(payload: str, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_guard_stripe_hmac_verification(client, monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_guard")
    payload = {"id": "evt_guard_invalid", "type": "checkout.session.completed", "data": {"object": {}}}
    r = client.post(
        "/webhooks/stripe",
        json=payload,
        headers={"Stripe-Signature": "t=1,v1=invalid"},
    )
    assert r.status_code == 400


def test_guard_sentry_actionable_event_runs_triage_and_slack(client, fake_redis, monkeypatch):
    monkeypatch.setenv("SENTRY_WEBHOOK_SECRET", "sentry_guard")
    monkeypatch.setenv("SLACK_PLATFORM_HEALTH_WEBHOOK_URL", "https://example.invalid/slack")
    monkeypatch.setattr(sentry_module, "get_redis", lambda: fake_redis, raising=True)

    called = {"triage": 0, "slack": 0}

    async def _fake_triage(stack_trace: str, context: str, *, max_tokens: int = 800):
        called["triage"] += 1
        return {"root_cause": "timeout", "suggested_fix": "raise pool"}

    async def _fake_slack_send(text: str) -> None:
        called["slack"] += 1
        assert "root_cause: timeout" in text

    monkeypatch.setattr(sentry_module, "triage_sentry_error", _fake_triage, raising=True)
    monkeypatch.setattr(sentry_module, "send_slack_platform_health_message", _fake_slack_send, raising=True)

    payload = {"id": "evt_guard_sentry", "level": "error", "message": "boom", "user_count": 3}
    payload_s = json.dumps(payload)
    sig = _sentry_sig(payload_s, "sentry_guard")
    r = client.post(
        "/webhooks/sentry",
        content=payload_s,
        headers={"Content-Type": "application/json", "sentry-hook-signature": sig},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert called["triage"] == 1
    assert called["slack"] == 1


def test_guard_feature_flag_mutation_writes_audit_artifact(client, rsa_keys, tmp_path, monkeypatch):
    monkeypatch.setenv("GOBIG_B2_FLAG_AUDIT_DIR", str(tmp_path))
    token = _make_admin_token(private_pem=rsa_keys["private_pem"], jti="guard-flag-1", admin_role="Developer")
    r = client.post(
        "/api/v1/admin/flags/gobig_nlp_processing",
        headers={"Authorization": f"Bearer {token}"},
        json={"enabled": True, "reason": "slo guard"},
    )
    assert r.status_code == 200
    audit_path = Path(r.json()["audit_path"])
    assert audit_path.exists()


def test_guard_playbook_rejects_invalid_approval_token(client, rsa_keys, tmp_path, monkeypatch):
    scripts_dir = tmp_path / "playbooks"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "restart-celery.sh").write_text("#!/usr/bin/env bash\necho no-run\n", encoding="utf-8")
    monkeypatch.setenv("GOBIG_PLAYBOOKS_DIR", str(scripts_dir))
    monkeypatch.setenv("GOBIG_PLAYBOOK_APPROVAL_TOKEN", "expected-token")
    monkeypatch.setenv("GOBIG_B2_PLAYBOOK_AUDIT_DIR", str(tmp_path / "playbook-audit"))

    token = _make_admin_token(private_pem=rsa_keys["private_pem"], jti="guard-playbook-1", admin_role="Developer")
    r = client.post(
        "/api/v1/admin/playbook/restart-celery",
        headers={"Authorization": f"Bearer {token}"},
        json={"slack_approval_token": "bad-token"},
    )
    assert r.status_code == 403
