"""Dev-only RS256 JWT minting. Disabled unless GOBIG_DEV_ALLOW_TOKEN_MINT is set."""

from __future__ import annotations

import os
import time
import uuid

import jwt
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/auth", tags=["auth"])


def _mint_enabled() -> bool:
    v = os.environ.get("GOBIG_DEV_ALLOW_TOKEN_MINT", "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _load_private_key() -> str:
    path = os.environ.get("GOBIG_IAM_PRIVATE_KEY_PATH", "./keys/iam_private.pem")
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        raise HTTPException(
            status_code=503,
            detail=f"Cannot read private key at {path}",
        ) from None


def _validated_role(admin_role: str | None) -> str | None:
    if admin_role is None:
        return None
    if admin_role not in ("Developer", "Business"):
        raise HTTPException(
            status_code=422,
            detail='admin_role must be "Developer" or "Business" if provided',
        )
    return admin_role


@router.get("/dev-token")
async def mint_dev_token(
    user_id: str = Query("dev", description="User ID for the token subject (sub claim)"),
    admin_role: str | None = Query(
        None,
        description='Optional. Use "Developer" for /api/v1/admin/flags, or "Business" for 403 demos.',
    ),
    ttl_seconds: int = Query(3600, ge=60, le=86400),
) -> dict[str, str | int | None]:
    if not _mint_enabled():
        raise HTTPException(
            status_code=403,
            detail="Token minting disabled (set GOBIG_DEV_ALLOW_TOKEN_MINT=1 for local use only)",
        )

    private_pem = _load_private_key()
    role = _validated_role(admin_role)

    payload: dict[str, str | int] = {
        "sub": user_id,
        "jti": str(uuid.uuid4()),
        "exp": int(time.time()) + ttl_seconds,
    }
    if role is not None:
        payload["admin_role"] = role

    token = jwt.encode(payload, private_pem, algorithm="RS256")
    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": ttl_seconds,
        "user_id": user_id,
    }


@router.get("/dev-token-unlimited")
async def mint_dev_token_unlimited(
    user_id: str = Query("dev", description="User ID for the token subject (sub claim)"),
    admin_role: str | None = Query(
        None,
        description='Optional. Use "Developer" for /api/v1/admin/flags, or "Business" for 403 demos.',
    ),
) -> dict[str, str | int | None]:
    if not _mint_enabled():
        raise HTTPException(
            status_code=403,
            detail="Token minting disabled (set GOBIG_DEV_ALLOW_TOKEN_MINT=1 for local use only)",
        )

    private_pem = _load_private_key()
    role = _validated_role(admin_role)

    payload: dict[str, str] = {
        "sub": user_id,
        "jti": str(uuid.uuid4()),
    }
    if role is not None:
        payload["admin_role"] = role

    token = jwt.encode(payload, private_pem, algorithm="RS256")
    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": None,
        "user_id": user_id,
    }
