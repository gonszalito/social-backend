import tempfile
import time
from pathlib import Path

import jwt


def _make_token(
    *,
    private_pem: str,
    jti: str,
    admin_role: str | None = None,
    exp_seconds: int = 3600,
) -> str:
    now = int(time.time())
    payload: dict[str, object] = {
        "sub": "user_123",
        "jti": jti,
        "exp": now + exp_seconds,
    }
    if admin_role is not None:
        payload["admin_role"] = admin_role
    return jwt.encode(payload, private_pem, algorithm="RS256")


def test_health_is_public(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_missing_authorization_is_401(client):
    r = client.get("/api/v1/admin/flags")
    assert r.status_code == 401


def test_business_role_can_list_flags(client, rsa_keys):
    token = _make_token(private_pem=rsa_keys["private_pem"], jti="t1", admin_role="Business")
    r = client.get("/api/v1/admin/flags", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    data = r.json()
    assert "flags" in data
    assert "gobig_nlp_processing" in data["flags"]


def test_developer_role_is_200(client, rsa_keys):
    token = _make_token(private_pem=rsa_keys["private_pem"], jti="t2", admin_role="Developer")
    r = client.get("/api/v1/admin/flags", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert "flags" in r.json()


def test_business_cannot_post_flag(client, rsa_keys, tmp_path, monkeypatch):
    monkeypatch.setenv("GOBIG_B2_FLAG_AUDIT_DIR", str(tmp_path))
    token = _make_token(private_pem=rsa_keys["private_pem"], jti="t1b", admin_role="Business")
    r = client.post(
        "/api/v1/admin/flags/gobig_nlp_processing",
        headers={"Authorization": f"Bearer {token}"},
        json={"enabled": True, "reason": "ops"},
    )
    assert r.status_code == 403


def test_revoke_blocks_token_immediately(client, rsa_keys):
    token = _make_token(private_pem=rsa_keys["private_pem"], jti="t3", admin_role="Developer")

    # Works before revoke
    r1 = client.get("/api/v1/admin/flags", headers={"Authorization": f"Bearer {token}"})
    assert r1.status_code == 200

    # Revoke
    r2 = client.post("/auth/revoke", json={"reason": "test"}, headers={"Authorization": f"Bearer {token}"})
    assert r2.status_code == 200
    assert r2.json()["revoked"] is True

    # Immediately blocked after revoke
    r3 = client.get("/api/v1/admin/flags", headers={"Authorization": f"Bearer {token}"})
    assert r3.status_code == 401
    assert r3.json()["detail"] == "Token revoked"


def test_dev_token_mint_disabled_by_default(client):
    r = client.get("/auth/dev-token")
    assert r.status_code == 403


def test_dev_token_mint_with_env(client, rsa_keys, monkeypatch):
    monkeypatch.setenv("GOBIG_DEV_ALLOW_TOKEN_MINT", "1")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as f:
        f.write(rsa_keys["private_pem"])
        key_path = f.name
    monkeypatch.setenv("GOBIG_IAM_PRIVATE_KEY_PATH", key_path)
    try:
        r = client.get("/auth/dev-token", params={"admin_role": "Developer", "ttl_seconds": 120})
    finally:
        Path(key_path).unlink(missing_ok=True)
    assert r.status_code == 200
    data = r.json()
    assert data["token_type"] == "Bearer"
    assert data["expires_in"] == 120
    token = data["access_token"]
    decoded = jwt.decode(token, rsa_keys["public_pem"], algorithms=["RS256"])
    assert decoded["admin_role"] == "Developer"
    assert decoded["sub"] == "dev"

    r2 = client.get(
        "/api/v1/admin/flags",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 200


def test_dev_token_unlimited_with_env(client, rsa_keys, monkeypatch):
    monkeypatch.setenv("GOBIG_DEV_ALLOW_TOKEN_MINT", "1")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as f:
        f.write(rsa_keys["private_pem"])
        key_path = f.name
    monkeypatch.setenv("GOBIG_IAM_PRIVATE_KEY_PATH", key_path)
    try:
        r = client.get("/auth/dev-token-unlimited", params={"admin_role": "Developer"})
    finally:
        Path(key_path).unlink(missing_ok=True)
    assert r.status_code == 200
    data = r.json()
    assert data["token_type"] == "Bearer"
    assert data["expires_in"] is None
    token = data["access_token"]
    decoded = jwt.decode(token, rsa_keys["public_pem"], algorithms=["RS256"])
    assert "exp" not in decoded
    assert decoded["admin_role"] == "Developer"
    assert decoded["sub"] == "dev"

    r2 = client.get(
        "/api/v1/admin/flags",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 200

