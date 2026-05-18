from __future__ import annotations

import asyncio
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.api.dependencies.auth import AuthClaims, require_admin
from app.integration.b2_client import b2_client
from app.integration.postgres_client import postgres_client
from app.integration.redis_client import get_redis
from app.services.flower_proxy import get_workers

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

POSTGRES_TIMEOUT_MS = 500
REDIS_TIMEOUT_MS = 200
FLOWER_TIMEOUT_MS = 2000
B2_TIMEOUT_MS = 1000


async def _check_postgres() -> str:
    await postgres_client.fetchone("SELECT 1")
    return "SELECT 1 ok"


async def _check_redis() -> str:
    redis = get_redis()
    await redis.ping()
    return "PING ok"


async def _check_flower() -> str:
    await get_workers()
    return "workers health ok"


async def _check_b2() -> str:
    await b2_client.load_b2_paths()
    return "b2 metadata reachable"


async def _run_check(
    check_name: str,
    threshold_ms: int,
    probe: Any,
) -> dict[str, Any]:
    started = time.perf_counter()
    timeout_s = threshold_ms / 1000.0

    try:
        result = await asyncio.wait_for(probe(), timeout=timeout_s)
    except TimeoutError:
        latency_ms = int((time.perf_counter() - started) * 1000)
        return {
            "status": "critical",
            "latency_ms": latency_ms,
            "message": f"{check_name} timed out",
            "threshold_ms": threshold_ms,
        }
    except Exception as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        return {
            "status": "critical",
            "latency_ms": latency_ms,
            "message": str(exc),
            "threshold_ms": threshold_ms,
        }

    latency_ms = int((time.perf_counter() - started) * 1000)

    message = str(result)
    degraded = False
    if isinstance(result, tuple) and len(result) == 2:
        message = str(result[0])
        degraded = bool(result[1])
    if isinstance(result, dict):
        message = str(result.get("message", "ok"))
        degraded = bool(result.get("degraded", False))

    check_status = "degraded" if degraded or latency_ms > threshold_ms else "healthy"
    return {
        "status": check_status,
        "latency_ms": latency_ms,
        "message": message,
        "threshold_ms": threshold_ms,
    }


def _aggregate_status(checks: dict[str, dict[str, Any]]) -> str:
    statuses = [item.get("status", "critical") for item in checks.values()]
    if any(s == "critical" for s in statuses):
        return "critical"
    if any(s == "degraded" for s in statuses):
        return "degraded"
    return "healthy"


@router.get("/system-status")
async def system_status(_claims: Annotated[AuthClaims, Depends(require_admin)]) -> dict[str, Any]:
    overall_started = time.perf_counter()
    postgres_check, redis_check, flower_check, b2_check = await asyncio.gather(
        _run_check("postgres", POSTGRES_TIMEOUT_MS, _check_postgres),
        _run_check("redis", REDIS_TIMEOUT_MS, _check_redis),
        _run_check("flower", FLOWER_TIMEOUT_MS, _check_flower),
        _run_check("b2", B2_TIMEOUT_MS, _check_b2),
    )
    total_latency_ms = int((time.perf_counter() - overall_started) * 1000)

    checks = {
        "postgres": postgres_check,
        "redis": redis_check,
        "flower": flower_check,
        "b2": b2_check,
    }
    return {
        "status": _aggregate_status(checks),
        "checks": checks,
        "latency_ms": total_latency_ms,
    }
