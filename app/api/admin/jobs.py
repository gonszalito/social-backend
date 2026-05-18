from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.dependencies.auth import AuthClaims, require_admin
from app.integration.b2_client import b2_client
from app.services.flower_proxy import (
    FlowerProxyError,
    get_dead_letter,
    get_queues,
    get_workers,
    retry_task,
)

router = APIRouter(prefix="/api/v1/admin/jobs", tags=["admin"])


class RetryTaskRequest(BaseModel):
    reason: str | None = Field(default=None)


@router.get("/workers")
async def workers(_claims: Annotated[AuthClaims, Depends(require_admin)]) -> dict[str, Any]:
    try:
        result = await get_workers()
    except FlowerProxyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return result["body"]


@router.get("/queues")
async def queues(_claims: Annotated[AuthClaims, Depends(require_admin)]) -> dict[str, Any]:
    try:
        result = await get_queues()
    except FlowerProxyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return result["body"]


@router.get("/dead-letter")
async def dead_letter(_claims: Annotated[AuthClaims, Depends(require_admin)]) -> dict[str, Any]:
    try:
        result = await get_dead_letter()
    except FlowerProxyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return result["body"]


@router.post("/{task_id}/retry")
async def retry(
    task_id: str,
    body: RetryTaskRequest,
    claims: Annotated[AuthClaims, Depends(require_admin)],
) -> dict[str, Any]:
    reason = (body.reason or "").strip()
    if not reason:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="reason must be a non-empty string",
        )

    actor = claims.sub or "unknown"
    timestamp = datetime.now(timezone.utc).isoformat()
    await b2_client.write_job_retry_audit(
        actor=actor,
        task_id=task_id,
        reason=reason,
        timestamp_iso=timestamp,
    )

    try:
        result = await retry_task(task_id)
    except FlowerProxyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return result["body"]
