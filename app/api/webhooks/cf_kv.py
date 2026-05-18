from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies.auth import AuthClaims, require_developer_role
from app.services.cf_kv_sync import run_cf_kv_sync

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/cf-kv-sync")
async def cf_kv_sync_webhook(
    claims: Annotated[AuthClaims, Depends(require_developer_role)],
) -> dict:
    return await run_cf_kv_sync(claims)
