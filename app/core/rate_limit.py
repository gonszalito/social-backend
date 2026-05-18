from __future__ import annotations

import math
import time
from collections.abc import Awaitable, Callable

from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.prometheus import inc_rate_limit_hit
from app.integration.redis_client import get_redis

WINDOW_SECONDS = 60
MOBILE_LIMIT = 100
ADMIN_LIMIT = 30
RATE_LIMIT_KEY_PREFIX = "rate_limit"

WEBHOOK_PREFIX = "/webhooks"
ADMIN_PREFIX = "/api/v1/admin"
EXCLUDED_PATHS = {"/health", "/metrics", "/docs", "/redoc", "/openapi.json"}


def categorize_endpoint(path: str) -> str | None:
    if path in EXCLUDED_PATHS:
        return None
    if path.startswith(WEBHOOK_PREFIX):
        return "webhook"
    if path.startswith(ADMIN_PREFIX):
        return "admin"
    return "mobile"


def identity_from_request(request: Request, category: str) -> str:
    claims = getattr(request.state, "claims", None)
    subject = getattr(claims, "sub", None) if claims is not None else None
    if subject:
        return str(subject)

    client = request.client.host if request.client else "unknown"
    return f"anon:{category}:{client}"


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        category = categorize_endpoint(request.url.path)
        if category is None or category == "webhook":
            return await call_next(request)

        limit = ADMIN_LIMIT if category == "admin" else MOBILE_LIMIT
        identity = identity_from_request(request, category)

        now_ms = int(time.time() * 1000)
        window_start_ms = now_ms - (WINDOW_SECONDS * 1000)
        member = f"{now_ms}:{time.monotonic_ns()}"
        key = f"{RATE_LIMIT_KEY_PREFIX}:{category}:{identity}"

        redis = get_redis()
        try:
            await redis.zremrangebyscore(key, 0, window_start_ms)
            await redis.zadd(key, {member: now_ms})
            await redis.expire(key, WINDOW_SECONDS + 1)
            hits = await redis.zcard(key)
        except (RedisConnectionError, RedisTimeoutError) as exc:
            return JSONResponse(status_code=503, content={"detail": f"Redis unavailable: {exc!s}"})

        if hits <= limit:
            return await call_next(request)

        try:
            oldest = await redis.zrange(key, 0, 0, withscores=True)
        except (RedisConnectionError, RedisTimeoutError) as exc:
            return JSONResponse(status_code=503, content={"detail": f"Redis unavailable: {exc!s}"})

        retry_after = WINDOW_SECONDS
        if oldest:
            oldest_ms = int(oldest[0][1])
            retry_after = max(1, math.ceil((oldest_ms + WINDOW_SECONDS * 1000 - now_ms) / 1000))

        inc_rate_limit_hit(category)

        return JSONResponse(
            status_code=429,
            headers={"Retry-After": str(retry_after)},
            content={
                "detail": "Rate limit exceeded",
                "endpoint_category": category,
                "limit": limit,
                "window_seconds": WINDOW_SECONDS,
                "retry_after_seconds": retry_after,
            },
        )
