import os
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status

from app.core.storage_config import get_storage_config

router = APIRouter(tags=["storage"])

UploadType = Literal["voice_log", "attempt_photo", "avatar", "taste_profile"]

MAX_SIZE_MB: dict[str, int] = {
    "voice_log": 5,
    "attempt_photo": 10,
    "avatar": 2,
    "taste_profile": 1,
}


@router.post("/api/upload")
async def upload_via_presign(
    upload_type: UploadType = Query(...),
    upload_id: str = Query(..., min_length=1),
    file: UploadFile = File(...),
) -> dict[str, str | int]:
    cfg = get_storage_config()
    rule = cfg.upload_rules.get(upload_type)
    if rule is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid upload_type")

    content = await file.read()
    size_bytes = len(content)
    max_size_bytes = MAX_SIZE_MB[upload_type] * 1024 * 1024
    if size_bytes > max_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large for {upload_type}. Max {MAX_SIZE_MB[upload_type]}MB.",
        )

    uploads_root = Path(os.environ.get("GOBIG_UPLOAD_STAGING_DIR", "var/uploads"))
    destination_dir = uploads_root / upload_type
    destination_dir.mkdir(parents=True, exist_ok=True)

    original_name = Path(file.filename or "upload.bin").name
    stored_filename = f"{upload_id}__{original_name}"
    stored_path = destination_dir / stored_filename
    stored_path.write_bytes(content)

    return {
        "status": "uploaded",
        "upload_id": upload_id,
        "upload_type": upload_type,
        "alias": rule.alias,
        "filename": original_name,
        "size_bytes": size_bytes,
        "stored_path": str(stored_path),
    }
