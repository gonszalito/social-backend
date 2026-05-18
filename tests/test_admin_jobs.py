import json
from pathlib import Path

import jwt

from app.api.admin import jobs as jobs_module
from app.services.flower_proxy import FlowerProxyError


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


def test_jobs_routes_require_admin(client, rsa_keys, monkeypatch):
    async def _fake_workers():
        return {"status_code": 200, "body": {"workers": []}}

    token = _make_token(private_pem=rsa_keys["private_pem"], jti="jobs-1", admin_role="Business")
    monkeypatch.setattr(jobs_module, "get_workers", _fake_workers, raising=True)
    r = client.get("/api/v1/admin/jobs/workers", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


def test_retry_requires_reason(client, rsa_keys):
    token = _make_token(private_pem=rsa_keys["private_pem"], jti="jobs-2", admin_role="Developer")
    r = client.post(
        "/api/v1/admin/jobs/task-123/retry",
        headers={"Authorization": f"Bearer {token}"},
        json={"reason": "   "},
    )
    assert r.status_code == 400


def test_retry_writes_audit_before_flower_call(client, rsa_keys, tmp_path, monkeypatch):
    audit_dir = tmp_path / "job-audit"
    monkeypatch.setenv("GOBIG_B2_JOB_AUDIT_DIR", str(audit_dir))

    called = {"value": False}

    async def _fake_retry(task_id: str):
        called["value"] = True
        return {"status_code": 200, "body": {"task_id": task_id, "status": "retried"}}

    monkeypatch.setattr(jobs_module, "retry_task", _fake_retry, raising=True)

    token = _make_token(private_pem=rsa_keys["private_pem"], jti="jobs-3", admin_role="Developer")
    r = client.post(
        "/api/v1/admin/jobs/task-123/retry",
        headers={"Authorization": f"Bearer {token}"},
        json={"reason": "operator approved retry"},
    )
    assert r.status_code == 200
    assert called["value"] is True
    files = list(audit_dir.glob("*.json"))
    assert len(files) == 1
    audit = json.loads(Path(files[0]).read_text(encoding="utf-8"))
    assert audit["task_id"] == "task-123"
    assert audit["reason"] == "operator approved retry"
    assert audit["actor"] == "admin_tester"


def test_queues_returns_503_when_flower_unreachable(client, rsa_keys, monkeypatch):
    async def _fail():
        raise FlowerProxyError(503, "upstream_unreachable")

    monkeypatch.setattr(jobs_module, "get_queues", _fail, raising=True)
    token = _make_token(private_pem=rsa_keys["private_pem"], jti="jobs-4", admin_role="Developer")
    r = client.get("/api/v1/admin/jobs/queues", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 503
    assert r.json()["detail"] == "upstream_unreachable"
