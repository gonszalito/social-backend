from __future__ import annotations

import os
from typing import Any

import httpx


class FlowerProxyError(RuntimeError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _base_url() -> str:
    return os.getenv(
        "GOBIG_FLOWER_BASE_URL",
        "http://flower.gobig-prod.svc.cluster.local:5555",
    ).rstrip("/")


def _timeout_s() -> float:
    raw = os.getenv("GOBIG_FLOWER_TIMEOUT_S", "5").strip() or "5"
    return float(raw)


async def _request(method: str, path: str, *, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"{_base_url()}{path}"
    timeout = _timeout_s()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(method, url, json=json_body)
    except httpx.TimeoutException as exc:
        raise FlowerProxyError(503, "upstream_unreachable") from exc
    except httpx.HTTPError as exc:
        raise FlowerProxyError(503, "upstream_unreachable") from exc

    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        body: Any = response.json()
    else:
        body = {"raw": response.text}
    if response.status_code >= 400:
        raise FlowerProxyError(response.status_code, str(body))
    return {"status_code": response.status_code, "body": body}


async def get_workers() -> dict[str, Any]:
    return await _request("GET", "/api/workers")


async def get_queues() -> dict[str, Any]:
    return await _request("GET", "/api/queues")


async def get_dead_letter() -> dict[str, Any]:
    return await _request("GET", "/api/tasks?state=FAILURE")


async def retry_task(task_id: str) -> dict[str, Any]:
    return await _request("POST", f"/api/task/retry/{task_id}")
