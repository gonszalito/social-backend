from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.api.dependencies.auth import AuthClaims, require_admin, require_developer_role
from app.integration.b2_client import b2_client
from app.integration.redis_client import get_redis
from app.services.cf_kv_sync import run_cf_kv_sync
from app.services.feature_flags import (
    admin_flag_names,
    enabled_to_redis_value,
    is_flag_on,
    redis_value_label,
)

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


class FlagUpdateRequest(BaseModel):
    enabled: bool
    reason: str | None = Field(default=None)


class FlagUpdateResponse(BaseModel):
    flag_name: str
    old_value: str
    new_value: str
    audit_path: str


@router.get("/flags")
async def list_flags(
    _claims: Annotated[AuthClaims, Depends(require_admin)],
    redis=Depends(get_redis),
) -> dict[str, Any]:
    names = admin_flag_names()
    flags: dict[str, dict[str, Any]] = {}
    try:
        if hasattr(redis, "mget"):
            values = await redis.mget(names)
        else:
            values = [await redis.get(name) for name in names]
        for name, raw in zip(names, values, strict=False):
            flags[name] = {
                "value": redis_value_label(raw) if raw is not None else None,
                "enabled": is_flag_on(raw),
            }
    except (RedisConnectionError, RedisTimeoutError) as e:
        raise HTTPException(status_code=503, detail=f"Redis unavailable: {e!s}")
    return {"flags": flags}


@router.post("/flags/{flag_name}", response_model=FlagUpdateResponse)
async def update_flag(
    flag_name: str,
    body: FlagUpdateRequest,
    claims: Annotated[AuthClaims, Depends(require_developer_role)],
    redis=Depends(get_redis),
) -> FlagUpdateResponse:
    if flag_name not in admin_flag_names():
        raise HTTPException(status_code=404, detail="Unknown flag_name")

    reason = body.reason if body.reason is not None else ""
    if not reason.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="reason must be a non-empty string",
        )

    admin_user_id = claims.sub or ""

    try:
        previous = await redis.get(flag_name)
    except (RedisConnectionError, RedisTimeoutError) as e:
        raise HTTPException(status_code=503, detail=f"Redis unavailable: {e!s}")

    old_value = redis_value_label(previous) if previous is not None else ""
    new_value = enabled_to_redis_value(body.enabled)
    ts = datetime.now(timezone.utc).isoformat()

    try:
        audit_path = await b2_client.write_feature_flag_audit(
            flag_name=flag_name,
            old_value=old_value,
            new_value=new_value,
            reason=reason.strip(),
            admin_user_id=admin_user_id,
            timestamp_iso=ts,
        )
    except OSError as e:
        raise HTTPException(
            status_code=503,
            detail=f"Audit storage failed: {e!s}",
        )

    try:
        await redis.set(flag_name, new_value)
    except (RedisConnectionError, RedisTimeoutError) as e:
        raise HTTPException(status_code=503, detail=f"Redis unavailable: {e!s}")

    await run_cf_kv_sync(claims)

    return FlagUpdateResponse(
        flag_name=flag_name,
        old_value=old_value,
        new_value=new_value,
        audit_path=audit_path,
    )
