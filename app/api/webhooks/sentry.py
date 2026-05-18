from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Annotated

import httpx
from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.integration.redis_client import get_redis
from app.services.ai_wrapper import triage_sentry_error

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

_SEVERITY_RANK = {
    "debug": 10,
    "info": 20,
    "warning": 30,
    "error": 40,
    "fatal": 50,
}


class SentryEvent(BaseModel):
    event_id: str = Field(alias="id")
    level: str = "error"
    message: str | None = None
    culprit: str | None = None
    user_count: int = 0
    stacktrace: str | None = None
    exception: dict | None = None
    tags: dict | None = None
    extra: dict | None = None


def _normalize_signature(signature: str | None) -> str | None:
    if not signature:
        return None
    sig = signature.strip()
    if sig.startswith("sha256="):
        return sig.split("=", 1)[1]
    return sig


def verify_sentry_signature(payload: bytes, signature: str | None, secret: str) -> bool:
    if not signature or not secret:
        return False
    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    provided = _normalize_signature(signature)
    if not provided:
        return False
    return hmac.compare_digest(expected, provided)


def _is_actionable(level: str, user_count: int) -> bool:
    return _SEVERITY_RANK.get(level.lower(), 0) >= _SEVERITY_RANK["error"] and user_count > 1


def _extract_stack_trace(event: SentryEvent) -> str:
    if event.stacktrace:
        return event.stacktrace
    if isinstance(event.exception, dict):
        values = event.exception.get("values") or []
        if values and isinstance(values[0], dict):
            return json.dumps(values[0], separators=(",", ":"))
    return "No stack trace provided"


def _build_context(event: SentryEvent) -> str:
    context = {
        "event_id": event.event_id,
        "level": event.level,
        "message": event.message,
        "culprit": event.culprit,
        "user_count": event.user_count,
        "tags": event.tags or {},
        "extra": event.extra or {},
    }
    return json.dumps(context, separators=(",", ":"))


async def send_slack_platform_health_message(text: str) -> None:
    webhook_url = os.getenv("SLACK_PLATFORM_HEALTH_WEBHOOK_URL", "").strip()
    if not webhook_url:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SLACK_PLATFORM_HEALTH_WEBHOOK_URL is not configured",
        )
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(webhook_url, json={"text": text})
        resp.raise_for_status()


@router.post("/sentry")
async def sentry_webhook(
    request: Request,
    sentry_hook_signature: Annotated[str | None, Header(alias="sentry-hook-signature")] = None,
) -> dict:
    secret = os.getenv("SENTRY_WEBHOOK_SECRET", "").strip()
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SENTRY_WEBHOOK_SECRET is not configured",
        )

    raw_body = await request.body()
    if not verify_sentry_signature(raw_body, sentry_hook_signature, secret):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid signature")

    try:
        payload = json.loads(raw_body)
        event = SentryEvent.model_validate(payload)
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid sentry payload: {exc!s}",
        )

    if not _is_actionable(event.level, event.user_count):
        return {"status": "ignored", "event_id": event.event_id, "reason": "below_threshold"}

    redis = get_redis()
    key = f"sentry_event:{event.event_id}"
    try:
        existing = await redis.get(key)
        if existing is not None:
            return {"status": "duplicate", "event_id": event.event_id}
        await redis.set(key, "processed", ex=86400)
    except (RedisConnectionError, RedisTimeoutError) as exc:
        raise HTTPException(status_code=503, detail=f"Redis unavailable: {exc!s}")

    try:
        triage = await triage_sentry_error(
            stack_trace=_extract_stack_trace(event),
            context=_build_context(event),
        )
        root_cause = triage.get("root_cause", "unknown")
        suggested_fix = triage.get("suggested_fix", "none")
        slack_text = (
            "Sentry Alert (#platform-health)\n"
            f"event_id: {event.event_id}\n"
            f"level: {event.level}\n"
            f"user_count: {event.user_count}\n"
            f"message: {event.message or 'n/a'}\n"
            f"root_cause: {root_cause}\n"
            f"suggested_fix: {suggested_fix}"
        )
        await send_slack_platform_health_message(slack_text)
    except HTTPException:
        await redis.delete(key)
        raise
    except Exception as exc:
        await redis.delete(key)
        raise HTTPException(status_code=503, detail=f"Sentry processing failed: {exc!s}")

    return {"status": "ok", "event_id": event.event_id, "triage": triage}
