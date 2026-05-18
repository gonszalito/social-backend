from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.dependencies.auth import AuthClaims, require_developer_role
from app.integration.b2_client import b2_client
from app.services.playbook_runner import (
    ALLOWED_PLAYBOOKS,
    PlaybookExecutionError,
    execute_playbook,
)

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


class PlaybookExecutionRequest(BaseModel):
    slack_approval_token: str = Field(min_length=1)


class PlaybookExecutionResponse(BaseModel):
    status: str
    playbook_name: str
    started_at: str
    finished_at: str
    duration_ms: int
    audit_paths: list[str]


@router.post(
    "/playbook/{playbook_name}",
    response_model=PlaybookExecutionResponse,
)
async def run_playbook(
    playbook_name: str,
    body: PlaybookExecutionRequest,
    claims: Annotated[AuthClaims, Depends(require_developer_role)],
) -> PlaybookExecutionResponse:
    if playbook_name not in ALLOWED_PLAYBOOKS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown playbook_name")

    try:
        expected_token = await b2_client.load_playbook_approval_token()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Approval token configuration unavailable: {exc!s}",
        )
    provided_token = body.slack_approval_token.strip()
    if not provided_token or not secrets.compare_digest(provided_token, expected_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid slack_approval_token")

    actor = claims.sub or "unknown"
    pre_audit = await b2_client.write_playbook_audit(
        actor=actor,
        playbook_name=playbook_name,
        status="requested",
        command="pending",
        exit_code=None,
        output_summary="Execution approved and queued",
    )

    try:
        result = await execute_playbook(playbook_name)
    except PlaybookExecutionError as exc:
        fail_audit = await b2_client.write_playbook_audit(
            actor=actor,
            playbook_name=playbook_name,
            status="failed",
            command="unresolved",
            exit_code=exc.exit_code,
            output_summary=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": "failed",
                "playbook_name": playbook_name,
                "audit_paths": [pre_audit, fail_audit],
                "error": str(exc),
            },
        )

    output_summary = result.stdout or result.stderr or "(no output)"
    post_audit = await b2_client.write_playbook_audit(
        actor=actor,
        playbook_name=playbook_name,
        status=result.status,
        command=result.command,
        exit_code=result.exit_code,
        output_summary=output_summary,
        started_at=result.started_at,
        finished_at=result.finished_at,
        duration_ms=result.duration_ms,
    )
    audit_paths = [pre_audit, post_audit]

    if result.status != "succeeded":
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": result.status,
                "playbook_name": playbook_name,
                "started_at": result.started_at,
                "finished_at": result.finished_at,
                "duration_ms": result.duration_ms,
                "audit_paths": audit_paths,
                "exit_code": result.exit_code,
            },
        )

    return PlaybookExecutionResponse(
        status=result.status,
        playbook_name=playbook_name,
        started_at=result.started_at,
        finished_at=result.finished_at,
        duration_ms=result.duration_ms,
        audit_paths=audit_paths,
    )
