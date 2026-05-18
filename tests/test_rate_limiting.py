import time

import jwt


def _make_token(
    *,
    private_pem: str,
    jti: str,
    admin_role: str | None = None,
    exp_seconds: int = 3600,
    sub: str = "rate_limit_user",
) -> str:
    now = int(time.time())
    payload: dict[str, object] = {
        "sub": sub,
        "jti": jti,
        "exp": now + exp_seconds,
    }
    if admin_role is not None:
        payload["admin_role"] = admin_role
    return jwt.encode(payload, private_pem, algorithm="RS256")


def test_mobile_rate_limit_101st_request_returns_429_with_retry_after(client):
    payload = {"upload_type": "avatar", "content_type": "image/png"}
    for _ in range(100):
        response = client.post("/api/v1/storage/presign", json=payload)
        assert response.status_code == 200

    denied = client.post("/api/v1/storage/presign", json=payload)
    assert denied.status_code == 429
    assert denied.json()["endpoint_category"] == "mobile"
    retry_after = denied.headers.get("Retry-After")
    assert retry_after is not None
    assert retry_after.isdigit()
    assert int(retry_after) >= 1


def test_admin_rate_limit_31st_request_returns_429_with_retry_after(client, rsa_keys):
    token = _make_token(
        private_pem=rsa_keys["private_pem"],
        jti="rate-admin-1",
        admin_role="Developer",
        sub="admin_rl_user",
    )
    headers = {"Authorization": f"Bearer {token}"}

    for _ in range(30):
        response = client.get("/api/v1/admin/flags", headers=headers)
        assert response.status_code == 200

    denied = client.get("/api/v1/admin/flags", headers=headers)
    assert denied.status_code == 429
    assert denied.json()["endpoint_category"] == "admin"
    retry_after = denied.headers.get("Retry-After")
    assert retry_after is not None
    assert retry_after.isdigit()
    assert int(retry_after) >= 1


def test_webhook_routes_bypass_rate_limiting(client):
    for _ in range(120):
        response = client.post("/webhooks/stripe", json={})
        assert response.status_code != 429


def test_rate_limit_hits_prometheus_counter(client, rsa_keys):
    token = _make_token(
        private_pem=rsa_keys["private_pem"],
        jti="rate-admin-2",
        admin_role="Developer",
        sub="admin_metrics_user",
    )
    headers = {"Authorization": f"Bearer {token}"}

    for _ in range(31):
        client.get("/api/v1/admin/flags", headers=headers)

    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert 'rate_limit_hits_total{endpoint_category="admin"}' in metrics.text
