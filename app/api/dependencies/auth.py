from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

import jwt
from fastapi import Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.integration.redis_client import get_redis


AdminRole = Literal["Business", "Developer"]


class AuthClaims(BaseModel):
    model_config = ConfigDict(extra="allow")

    sub: str | None = None
    jti: str
    exp: int | None = None
    admin_role: AdminRole | None = None


@dataclass
class AuthService:
    _public_key_pem: str | None = None

    def set_public_key_pem(self, pem: str) -> None:
        self._public_key_pem = pem

    def _require_key(self) -> str:
        if not self._public_key_pem:
            raise RuntimeError("Auth public key not loaded (startup ordering issue).")
        return self._public_key_pem

    async def verify_bearer_token(self, authorization: str | None) -> AuthClaims:
        if not authorization:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing Authorization header",
            )

        parts = authorization.split(" ", 1)
        if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid Authorization header",
            )

        token = parts[1].strip()
        try:
            payload = jwt.decode(
                token,
                key=self._require_key(),
                algorithms=["RS256"],
                options={
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_nbf": True,
                    "verify_iat": False,
                    "require": ["jti"],
                },
            )
        except jwt.ExpiredSignatureError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token expired",
            )
        except jwt.InvalidTokenError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
            )

        claims = AuthClaims.model_validate(payload)

        redis = get_redis()
        try:
            blocked = await redis.get(f"jwt:{claims.jti}")
        except (RedisConnectionError, RedisTimeoutError) as e:
            raise HTTPException(status_code=503, detail=f"Redis unavailable: {e!s}")
        if blocked is not None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token revoked",
            )

        return claims


auth_service = AuthService()


async def require_auth(
    request: Request,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> AuthClaims:
    cached = getattr(request.state, "claims", None)
    if isinstance(cached, AuthClaims):
        return cached
    return await auth_service.verify_bearer_token(authorization)


async def require_admin(claims: Annotated[AuthClaims, Depends(require_auth)]) -> AuthClaims:
    if claims.admin_role not in ("Business", "Developer"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return claims


async def require_developer_role(
    claims: Annotated[AuthClaims, Depends(require_auth)],
) -> AuthClaims:
    if claims.admin_role != "Developer":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return claims

