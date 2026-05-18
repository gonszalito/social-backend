"""Prometheus metrics middleware and /metrics endpoint (API-M1-06)."""
from __future__ import annotations

import re
import time
from collections.abc import Awaitable, Callable

from prometheus_client import Counter, Histogram, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_LATENCY = Histogram(
    "request_latency_seconds",
    "Request latency in seconds",
    ["method", "endpoint", "status_code"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.2, 0.5, 1.0, 2.5],
)
RATE_LIMIT_HITS = Counter(
    "rate_limit_hits_total",
    "Denied requests due to rate limiting",
    ["endpoint_category"],
)

# Paths to exclude from histogram recording
EXCLUDED_PATHS = {"/metrics", "/health"}

# Match UUIDs (with optional hyphens) or numeric path segments
_PATH_PARAM_RE = re.compile(r"^[0-9a-fA-F\-]{36}$|^\d+$")


def _normalize_endpoint(path: str) -> str:
    """Normalize endpoint path to reduce cardinality (e.g. /users/123 -> /users/{id})."""
    if not path or path == "/":
        return "/"
    segments = [s for s in path.rstrip("/").split("/") if s]
    normalized = [seg if not _PATH_PARAM_RE.match(seg) else "{id}" for seg in segments]
    return "/" + "/".join(normalized) if normalized else "/"


class PrometheusMiddleware(BaseHTTPMiddleware):
    """Record request_latency_seconds for each request, excluding /metrics and /health."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        if path in EXCLUDED_PATHS:
            return await call_next(request)

        start = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - start

        endpoint = _normalize_endpoint(path)
        status = str(response.status_code)
        REQUEST_LATENCY.labels(
            method=request.method,
            endpoint=endpoint,
            status_code=status,
        ).observe(duration)

        return response


def metrics_endpoint() -> Response:
    """Return Prometheus metrics in text format."""
    from starlette.responses import PlainTextResponse

    return PlainTextResponse(
        generate_latest().decode("utf-8"),
        media_type="text/plain; charset=utf-8; version=0.0.4",
    )


def inc_rate_limit_hit(endpoint_category: str) -> None:
    RATE_LIMIT_HITS.labels(endpoint_category=endpoint_category).inc()
