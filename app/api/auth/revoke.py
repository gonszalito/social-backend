import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.api.dependencies.auth import AuthClaims, require_auth
from app.integration.redis_client import get_redis

router = APIRouter(prefix="/auth", tags=["auth"])


class RevokeRequest(BaseModel):
    reason: str | None = None


@router.post("/revoke")
async def revoke_token(
    _body: RevokeRequest,
    claims: Annotated[AuthClaims, Depends(require_auth)],
) -> dict:
    redis = get_redis()

    ttl_seconds: int | None = None
    if claims.exp is not None:
        ttl_seconds = max(0, int(claims.exp - time.time()))

    key = f"jwt:{claims.jti}"
    try:
        if ttl_seconds is None:
            await redis.set(key, "1")
        else:
            await redis.set(key, "1", ex=ttl_seconds)
    except (RedisConnectionError, RedisTimeoutError) as e:
        raise HTTPException(status_code=503, detail=f"Redis unavailable: {e!s}")

    return {"revoked": True}

