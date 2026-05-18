import asyncio
import json
from pathlib import Path

import jwt
import pytest
from app.services import cf_kv_sync as cf_kv_sync_module


def _make_token(*, private_pem: str, jti: str, admin_role: str, exp_seconds: int = 3600) -> str:
    import time

    now = int(time.time())
    payload: dict[str, object] = {
        "sub": "admin_tester",
        "jti": jti,
        "exp": now + exp_seconds,
        "admin_role": admin_role,
    }
    return jwt.encode(payload, private_pem, algorithm="RS256")


def test_post_flag_requires_reason(client, rsa_keys, tmp_path, monkeypatch):
    monkeypatch.setenv("GOBIG_B2_FLAG_AUDIT_DIR", str(tmp_path))
    token = _make_token(
        private_pem=rsa_keys["private_pem"],
        jti="flag-post-2",
        admin_role="Developer",
    )
    r = client.post(
        "/api/v1/admin/flags/gobig_nlp_processing",
        headers={"Authorization": f"Bearer {token}"},
        json={"enabled": True},
    )
    assert r.status_code == 400
    r2 = client.post(
        "/api/v1/admin/flags/gobig_nlp_processing",
        headers={"Authorization": f"Bearer {token}"},
        json={"enabled": True, "reason": "   "},
    )
    assert r2.status_code == 400


def test_post_unknown_flag_404(client, rsa_keys, tmp_path, monkeypatch):
    monkeypatch.setenv("GOBIG_B2_FLAG_AUDIT_DIR", str(tmp_path))
    token = _make_token(
        private_pem=rsa_keys["private_pem"],
        jti="flag-post-3",
        admin_role="Developer",
    )
    r = client.post(
        "/api/v1/admin/flags/not_a_real_flag",
        headers={"Authorization": f"Bearer {token}"},
        json={"enabled": True, "reason": "x"},
    )
    assert r.status_code == 404


def test_post_flag_writes_audit_redis_and_lists(
    client,
    fake_redis,
    rsa_keys,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("GOBIG_B2_FLAG_AUDIT_DIR", str(tmp_path))
    token = _make_token(
        private_pem=rsa_keys["private_pem"],
        jti="flag-post-4",
        admin_role="Developer",
    )
    r = client.post(
        "/api/v1/admin/flags/gobig_nlp_processing",
        headers={"Authorization": f"Bearer {token}"},
        json={"enabled": True, "reason": "enable nlp for demo"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["flag_name"] == "gobig_nlp_processing"
    assert body["old_value"] == ""
    assert body["new_value"] == "1"
    assert Path(body["audit_path"]).is_file()

    audit = json.loads(Path(body["audit_path"]).read_text(encoding="utf-8"))
    assert audit["flag_name"] == "gobig_nlp_processing"
    assert audit["old_value"] == ""
    assert audit["new_value"] == "1"
    assert audit["reason"] == "enable nlp for demo"
    assert audit["admin_user_id"] == "admin_tester"
    assert "timestamp" in audit

    raw = asyncio.run(fake_redis.get("gobig_nlp_processing"))
    assert raw == "1"

    r2 = client.get("/api/v1/admin/flags", headers={"Authorization": f"Bearer {token}"})
    assert r2.status_code == 200
    entry = r2.json()["flags"]["gobig_nlp_processing"]
    assert entry["enabled"] is True
    assert entry["value"] == "1"


def test_cf_kv_sync_webhook_developer_only(client, rsa_keys):
    token = _make_token(
        private_pem=rsa_keys["private_pem"],
        jti="cfkv-1",
        admin_role="Developer",
    )
    r = client.post("/webhooks/cf-kv-sync", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is False
    assert body.get("status") == "skipped"


def test_cf_kv_sync_forbidden_for_business(client, rsa_keys):
    token = _make_token(
        private_pem=rsa_keys["private_pem"],
        jti="cfkv-2",
        admin_role="Business",
    )
    r = client.post("/webhooks/cf-kv-sync", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_cf_kv_sync_calls_cloudflare_workers_api(client, rsa_keys, monkeypatch):
    monkeypatch.setenv("GOBIG_CF_API_TOKEN", "token_123")
    monkeypatch.setenv("GOBIG_CF_ACCOUNT_ID", "acc_123")
    monkeypatch.setenv("GOBIG_CF_KV_NAMESPACE_ID", "ns_123")
    monkeypatch.setenv("GOBIG_CF_API_BASE_URL", "https://api.cloudflare.test/client/v4")

    calls: list[dict[str, str]] = []

    class _OkResponse:
        def raise_for_status(self) -> None:
            return None

    class _Client:
        def __init__(self, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def put(self, url: str, headers: dict[str, str], content: str):
            calls.append({"url": url, "auth": headers.get("Authorization", ""), "content": content})
            return _OkResponse()

    monkeypatch.setattr(cf_kv_sync_module.httpx, "AsyncClient", _Client, raising=True)

    token = _make_token(private_pem=rsa_keys["private_pem"], jti="cfkv-3", admin_role="Developer")
    r = client.post("/webhooks/cf-kv-sync", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["status"] == "synced"
    assert calls
    assert calls[0]["url"].endswith("/accounts/acc_123/storage/kv/namespaces/ns_123/values/gobig_nlp_processing")
    assert calls[0]["auth"] == "Bearer token_123"


def test_cf_kv_sync_queues_retry_on_cloudflare_failure(client, fake_redis, rsa_keys, monkeypatch):
    monkeypatch.setenv("GOBIG_CF_API_TOKEN", "token_123")
    monkeypatch.setenv("GOBIG_CF_ACCOUNT_ID", "acc_123")
    monkeypatch.setenv("GOBIG_CF_KV_NAMESPACE_ID", "ns_123")
    monkeypatch.setenv("GOBIG_CF_KV_RETRY_QUEUE_KEY", "cf_retry_q")

    class _Client:
        def __init__(self, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def put(self, url: str, headers: dict[str, str], content: str):
            raise RuntimeError("cloudflare unavailable")

    monkeypatch.setattr(cf_kv_sync_module.httpx, "AsyncClient", _Client, raising=True)

    token = _make_token(private_pem=rsa_keys["private_pem"], jti="cfkv-4", admin_role="Developer")
    r = client.post("/webhooks/cf-kv-sync", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["queued"] is True
    assert body["status"] == "queued_retry"

    size = asyncio.run(fake_redis.zcard("cf_retry_q"))
    assert size == 1
    payloads = asyncio.run(fake_redis.zrange("cf_retry_q", 0, -1))
    queue_item = json.loads(payloads[0])
    assert queue_item["event"] == "cf_kv_sync_retry"
    assert queue_item["requested_by"] == "admin_tester"
    assert "cloudflare unavailable" in queue_item["error"]
