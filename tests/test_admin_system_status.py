import asyncio
import time

import jwt

from app.api.admin import system_status as system_status_module


def _make_token(*, private_pem: str, jti: str, admin_role: str, exp_seconds: int = 3600) -> str:
    now = int(time.time())
    payload: dict[str, object] = {
        "sub": "admin_system_status_user",
        "jti": jti,
        "exp": now + exp_seconds,
        "admin_role": admin_role,
    }
    return jwt.encode(payload, private_pem, algorithm="RS256")


def test_system_status_requires_admin_auth(client):
    r = client.get("/api/v1/admin/system-status")
    assert r.status_code == 401


def test_system_status_healthy_when_all_checks_pass(client, rsa_keys, monkeypatch):
    async def _ok(name: str):
        return f"{name} ok"

    monkeypatch.setattr(system_status_module, "_check_postgres", lambda: _ok("postgres"), raising=True)
    monkeypatch.setattr(system_status_module, "_check_redis", lambda: _ok("redis"), raising=True)
    monkeypatch.setattr(system_status_module, "_check_flower", lambda: _ok("flower"), raising=True)
    monkeypatch.setattr(system_status_module, "_check_b2", lambda: _ok("b2"), raising=True)

    token = _make_token(private_pem=rsa_keys["private_pem"], jti="sys-1", admin_role="Developer")
    r = client.get("/api/v1/admin/system-status", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "healthy"
    assert set(body["checks"].keys()) == {"postgres", "redis", "flower", "b2"}
    assert isinstance(body["latency_ms"], int)


def test_system_status_degraded_when_probe_reports_slow_or_intermittent(client, rsa_keys, monkeypatch):
    async def _ok():
        return "ok"

    async def _degraded():
        return {"message": "intermittent latency", "degraded": True}

    monkeypatch.setattr(system_status_module, "_check_postgres", _ok, raising=True)
    monkeypatch.setattr(system_status_module, "_check_redis", _degraded, raising=True)
    monkeypatch.setattr(system_status_module, "_check_flower", _ok, raising=True)
    monkeypatch.setattr(system_status_module, "_check_b2", _ok, raising=True)

    token = _make_token(private_pem=rsa_keys["private_pem"], jti="sys-2", admin_role="Developer")
    r = client.get("/api/v1/admin/system-status", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "degraded"
    assert body["checks"]["redis"]["status"] == "degraded"


def test_system_status_critical_on_timeout_or_error(client, rsa_keys, monkeypatch):
    async def _ok():
        return "ok"

    async def _timeout():
        await asyncio.sleep(0.25)
        return "late"

    monkeypatch.setattr(system_status_module, "_check_postgres", _ok, raising=True)
    monkeypatch.setattr(system_status_module, "_check_redis", _timeout, raising=True)
    monkeypatch.setattr(system_status_module, "_check_flower", _ok, raising=True)
    monkeypatch.setattr(system_status_module, "_check_b2", _ok, raising=True)

    token = _make_token(private_pem=rsa_keys["private_pem"], jti="sys-3", admin_role="Developer")
    r = client.get("/api/v1/admin/system-status", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "critical"
    assert body["checks"]["redis"]["status"] == "critical"


def test_system_status_latency_tracks_slowest_check_not_sum(client, rsa_keys, monkeypatch):
    async def _sleep(ms: int):
        await asyncio.sleep(ms / 1000.0)
        return f"slept {ms}ms"

    monkeypatch.setattr(system_status_module, "_check_postgres", lambda: _sleep(70), raising=True)
    monkeypatch.setattr(system_status_module, "_check_redis", lambda: _sleep(40), raising=True)
    monkeypatch.setattr(system_status_module, "_check_flower", lambda: _sleep(110), raising=True)
    monkeypatch.setattr(system_status_module, "_check_b2", lambda: _sleep(20), raising=True)

    token = _make_token(private_pem=rsa_keys["private_pem"], jti="sys-4", admin_role="Developer")
    r = client.get("/api/v1/admin/system-status", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    # Concurrency target: wall clock should be near the slowest check (~110ms), not sum (~240ms).
    assert body["latency_ms"] >= 90
    assert body["latency_ms"] < 200
