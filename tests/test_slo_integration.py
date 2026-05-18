from __future__ import annotations

import time
import uuid
from urllib.parse import parse_qs, urlparse

import jwt
import pytest


def _make_token(*, private_pem: str, jti: str, sub: str = "slo-user", exp_seconds: int = 3600) -> str:
    now = int(time.time())
    payload: dict[str, object] = {
        "sub": sub,
        "jti": jti,
        "exp": now + exp_seconds,
    }
    return jwt.encode(payload, private_pem, algorithm="RS256")


@pytest.mark.slow
def test_locust_library_mode_p95_under_200ms(http_base_url, rsa_keys, monkeypatch, tmp_path):
    pytest.importorskip("locust")
    gevent = pytest.importorskip("gevent")
    pytest.importorskip("gevent.monkey")

    from locust import HttpUser, between, task
    from locust.env import Environment

    # Keep load realistic without being blocked by anonymous mobile limiter on public routes.
    from app.core import rate_limit as rate_limit_module

    monkeypatch.setattr(rate_limit_module, "MOBILE_LIMIT", 100000, raising=True)
    monkeypatch.setenv("GOBIG_PRESIGN_BASE_URL", http_base_url)
    monkeypatch.setenv("GOBIG_UPLOAD_API_PATH", "/api/upload")
    monkeypatch.setenv("GOBIG_NLP_STAGING_DIR", str(tmp_path / "nlp-staging"))

    auth_token = _make_token(private_pem=rsa_keys["private_pem"], jti="slo-load-token", sub="slo-load-user")

    class GoBigSLOUser(HttpUser):
        wait_time = between(0.05, 0.2)
        host = http_base_url

        def on_start(self) -> None:
            self._headers = {"Authorization": f"Bearer {auth_token}"}

        @task(3)
        def social_profile(self) -> None:
            self.client.get("/social/profile/slo-load-user", headers=self._headers, name="/social/profile/{user_id}")

        @task(2)
        def presign_then_upload(self) -> None:
            presign = self.client.post(
                "/api/v1/storage/presign",
                json={"upload_type": "avatar", "content_type": "image/png"},
                name="/api/v1/storage/presign",
            )
            if presign.status_code != 200:
                return
            upload_url = presign.json().get("presigned_url", "")
            parsed = urlparse(upload_url)
            query = parse_qs(parsed.query)
            upload_type = (query.get("upload_type") or ["avatar"])[0]
            upload_id = (query.get("upload_id") or [uuid.uuid4().hex])[0]
            self.client.post(
                f"/api/upload?upload_type={upload_type}&upload_id={upload_id}",
                files={"file": ("avatar.png", b"png-bytes", "image/png")},
                name="/api/upload",
            )

        @task(1)
        def nlp_ingest(self) -> None:
            batch_id = f"slo-batch-{uuid.uuid4().hex}"
            payload = {
                "batch_id": batch_id,
                "recipes": [
                    {"recipe_id": "r1", "text": "egg salad", "confidence": 0.92},
                ],
            }
            self.client.post("/api/v1/nlp/ingest", json=payload, name="/api/v1/nlp/ingest")

    env = Environment(user_classes=[GoBigSLOUser])
    env.create_local_runner()

    users = 30
    duration_s = 60
    env.runner.start(user_count=users, spawn_rate=10)
    gevent.sleep(duration_s)
    env.runner.quit()

    tracked_names = {
        "/social/profile/{user_id}",
        "/api/v1/storage/presign",
        "/api/upload",
        "/api/v1/nlp/ingest",
    }

    seen_names: set[str] = set()
    for (_method, name), entry in env.stats.entries.items():
        if name not in tracked_names:
            continue
        seen_names.add(name)
        p95 = entry.get_response_time_percentile(0.95)
        assert p95 < 200, f"{name} p95={p95}ms exceeded 200ms"

    assert seen_names == tracked_names, f"Missing load stats for endpoints: {tracked_names - seen_names}"
