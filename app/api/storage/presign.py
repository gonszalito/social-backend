import os
import uuid
from typing import Literal
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.core.storage_config import get_storage_config

router = APIRouter(prefix="/api/v1/storage", tags=["storage"])


UploadType = Literal["voice_log", "attempt_photo", "avatar", "taste_profile"]

MAX_SIZE_MB: dict[str, int] = {
    "voice_log": 5,
    "attempt_photo": 10,
    "avatar": 2,
    "taste_profile": 1,
}


class PresignRequest(BaseModel):
    upload_type: UploadType
    content_type: str = Field(min_length=1)


class PresignResponse(BaseModel):
    alias: str
    max_size_mb: int
    format: str
    presigned_url: str


@router.post("/presign", response_model=PresignResponse)
async def presign_upload(req: PresignRequest) -> PresignResponse:
    cfg = get_storage_config()
    rule = cfg.upload_rules.get(req.upload_type)
    if rule is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid upload_type")

    max_size_mb = MAX_SIZE_MB[req.upload_type]

    # Upload gateway (B2-backed) — multipart POST `file=@...` to this URL. Env avoids exposing
    # bucket/provider names in the JSON response. See `GOBIG_PRESIGN_BASE_URL`, `GOBIG_UPLOAD_API_PATH`.
    base = os.environ.get("GOBIG_PRESIGN_BASE_URL", "https://upload.gobig.local").rstrip("/")
    path = (os.environ.get("GOBIG_UPLOAD_API_PATH") or "/api/upload").strip() or "/api/upload"
    if not path.startswith("/"):
        path = f"/{path}"
    upload_id = uuid.uuid4().hex
    query = urlencode({"upload_type": req.upload_type, "upload_id": upload_id})
    presigned_url = f"{base}{path}?{query}"

    lowered = presigned_url.lower()
    if "backblaze" in lowered or "/b2" in lowered or "b2." in lowered:
        raise HTTPException(status_code=500, detail="Invalid presign url configuration")

    return PresignResponse(
        alias=rule.alias,
        max_size_mb=max_size_mb,
        format=rule.format,
        presigned_url=presigned_url,
    )

