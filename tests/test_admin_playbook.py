import json
from pathlib import Path

import jwt


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


def _make_playbook_script(base_dir: Path, script_name: str, body: str) -> None:
    base_dir.mkdir(parents=True, exist_ok=True)
    script_path = base_dir / script_name
    script_path.write_text(body, encoding="utf-8")


def test_playbook_rejects_invalid_approval_token(client, rsa_keys, tmp_path, monkeypatch):
    scripts_dir = tmp_path / "playbooks"
    _make_playbook_script(scripts_dir, "restart-celery.sh", "#!/usr/bin/env bash\necho should-not-run\n")

    audit_dir = tmp_path / "audit"
    monkeypatch.setenv("GOBIG_PLAYBOOKS_DIR", str(scripts_dir))
    monkeypatch.setenv("GOBIG_PLAYBOOK_APPROVAL_TOKEN", "expected-token")
    monkeypatch.setenv("GOBIG_B2_PLAYBOOK_AUDIT_DIR", str(audit_dir))

    token = _make_token(private_pem=rsa_keys["private_pem"], jti="playbook-1", admin_role="Developer")
    r = client.post(
        "/api/v1/admin/playbook/restart-celery",
        headers={"Authorization": f"Bearer {token}"},
        json={"slack_approval_token": "wrong-token"},
    )
    assert r.status_code == 403
    assert not list(audit_dir.glob("*.json"))


def test_playbook_unknown_name_returns_404(client, rsa_keys, monkeypatch):
    monkeypatch.setenv("GOBIG_PLAYBOOK_APPROVAL_TOKEN", "expected-token")
    token = _make_token(private_pem=rsa_keys["private_pem"], jti="playbook-2", admin_role="Developer")
    r = client.post(
        "/api/v1/admin/playbook/not-real",
        headers={"Authorization": f"Bearer {token}"},
        json={"slack_approval_token": "expected-token"},
    )
    assert r.status_code == 404


def test_playbook_success_writes_pre_and_post_audits(client, rsa_keys, tmp_path, monkeypatch):
    scripts_dir = tmp_path / "playbooks"
    _make_playbook_script(
        scripts_dir,
        "restart-celery.sh",
        "#!/usr/bin/env bash\necho celery restarted\n",
    )

    audit_dir = tmp_path / "audit"
    monkeypatch.setenv("GOBIG_PLAYBOOKS_DIR", str(scripts_dir))
    monkeypatch.setenv("GOBIG_PLAYBOOK_APPROVAL_TOKEN", "expected-token")
    monkeypatch.setenv("GOBIG_B2_PLAYBOOK_AUDIT_DIR", str(audit_dir))
    monkeypatch.setenv("GOBIG_PLAYBOOK_TIMEOUT_S", "5")

    token = _make_token(private_pem=rsa_keys["private_pem"], jti="playbook-3", admin_role="Developer")
    r = client.post(
        "/api/v1/admin/playbook/restart-celery",
        headers={"Authorization": f"Bearer {token}"},
        json={"slack_approval_token": "expected-token"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "succeeded"
    assert body["playbook_name"] == "restart-celery"
    assert len(body["audit_paths"]) == 2

    pre = json.loads(Path(body["audit_paths"][0]).read_text(encoding="utf-8"))
    post = json.loads(Path(body["audit_paths"][1]).read_text(encoding="utf-8"))
    assert pre["status"] == "requested"
    assert post["status"] == "succeeded"
    assert post["exit_code"] == 0
    assert "celery restarted" in post["output_summary"]


def test_playbook_failure_returns_non_2xx_with_audits(client, rsa_keys, tmp_path, monkeypatch):
    scripts_dir = tmp_path / "playbooks"
    _make_playbook_script(
        scripts_dir,
        "restart-celery.sh",
        "#!/usr/bin/env bash\necho failed to restart >&2\nexit 7\n",
    )

    audit_dir = tmp_path / "audit"
    monkeypatch.setenv("GOBIG_PLAYBOOKS_DIR", str(scripts_dir))
    monkeypatch.setenv("GOBIG_PLAYBOOK_APPROVAL_TOKEN", "expected-token")
    monkeypatch.setenv("GOBIG_B2_PLAYBOOK_AUDIT_DIR", str(audit_dir))

    token = _make_token(private_pem=rsa_keys["private_pem"], jti="playbook-4", admin_role="Developer")
    r = client.post(
        "/api/v1/admin/playbook/restart-celery",
        headers={"Authorization": f"Bearer {token}"},
        json={"slack_approval_token": "expected-token"},
    )
    assert r.status_code == 500
    detail = r.json()["detail"]
    assert detail["status"] == "failed"
    assert len(detail["audit_paths"]) == 2

    pre = json.loads(Path(detail["audit_paths"][0]).read_text(encoding="utf-8"))
    post = json.loads(Path(detail["audit_paths"][1]).read_text(encoding="utf-8"))
    assert pre["status"] == "requested"
    assert post["status"] == "failed"
    assert post["exit_code"] == 7
