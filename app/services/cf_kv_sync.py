"""Cloudflare KV sync trigger with retry queue fallback."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from time import monotonic
from typing import Any

import httpx

from app.api.dependencies import auth as auth_module
from app.api.dependencies.auth import AuthClaims
from app.services.feature_flags import admin_flag_names, redis_value_label


def _cf_kv_endpoint(account_id: str, namespace_id: str, flag_name: str) -> str:
    base = os.getenv("GOBIG_CF_API_BASE_URL", "https://api.cloudflare.com/client/v4").rstrip("/")
    return (
        f"{base}/accounts/{account_id}/storage/kv/namespaces/{namespace_id}/values/{flag_name}"
    )


def _load_cf_config() -> dict[str, str]:
    return {
        "token": os.getenv("GOBIG_CF_API_TOKEN", "").strip(),
        "account_id": os.getenv("GOBIG_CF_ACCOUNT_ID", "").strip(),
        "namespace_id": os.getenv("GOBIG_CF_KV_NAMESPACE_ID", "").strip(),
        "retry_queue_key": os.getenv("GOBIG_CF_KV_RETRY_QUEUE_KEY", "cf_kv_retry_queue").strip()
        or "cf_kv_retry_queue",
        "timeout_s": os.getenv("GOBIG_CF_API_TIMEOUT_S", "10").strip() or "10",
    }


async def _push_retry_item(payload: dict[str, Any], retry_queue_key: str) -> None:
    redis = auth_module.get_redis()
    score = int(datetime.now(timezone.utc).timestamp())
    # Use sorted-set queue for stable ordering and easy inspection.
    await redis.zadd(retry_queue_key, {json.dumps(payload, separators=(",", ":")): score})


async def run_cf_kv_sync(claims: AuthClaims) -> dict[str, Any]:
    """Sync all admin flags to Cloudflare KV, queue on failure."""
    cfg = _load_cf_config()
    if not (cfg["token"] and cfg["account_id"] and cfg["namespace_id"]):
        return {
            "ok": False,
            "queued": False,
            "status": "skipped",
            "message": (
                "Cloudflare KV sync not configured; set GOBIG_CF_API_TOKEN, "
                "GOBIG_CF_ACCOUNT_ID, and GOBIG_CF_KV_NAMESPACE_ID"
            ),
        }

    redis = auth_module.get_redis()
    flags: dict[str, str] = {}
    for name in admin_flag_names():
        flags[name] = redis_value_label(await redis.get(name))

    started = monotonic()
    try:
        timeout_s = float(cfg["timeout_s"])
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            headers = {"Authorization": f"Bearer {cfg['token']}"}
            for name, value in flags.items():
                url = _cf_kv_endpoint(cfg["account_id"], cfg["namespace_id"], name)
                response = await client.put(url, headers=headers, content=value)
                response.raise_for_status()
        latency_ms = int((monotonic() - started) * 1000)
        return {"ok": True, "queued": False, "status": "synced", "latency_ms": latency_ms}
    except Exception as exc:
        retry_item = {
            "event": "cf_kv_sync_retry",
            "requested_by": claims.sub,
            "flags": flags,
            "error": str(exc),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await _push_retry_item(retry_item, cfg["retry_queue_key"])
        return {
            "ok": False,
            "queued": True,
            "status": "queued_retry",
            "retry_queue_key": cfg["retry_queue_key"],
        }
