import gzip
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class UploadPathRule:
    upload_type: str
    alias: str
    format: str


class B2Client:
    """
    Minimal config loader used by the API layer.

    Production will load from Backblaze B2 (IAMConfigs path). For local/dev, we
    support loading from env to keep iteration unblocked.
    """

    async def load_iam_public_key_pem(self) -> str:
        pem = os.environ.get("GOBIG_IAM_PUBLIC_KEY_PEM")
        if pem:
            return pem

        local_path = os.environ.get("GOBIG_IAM_PUBLIC_KEY_PATH")
        if local_path:
            path = Path(local_path)
            if not path.is_absolute():
                # Resolve relative to project root (parent of app/)
                project_root = Path(__file__).resolve().parent.parent.parent
                path = project_root / path
            with open(path, "r", encoding="utf-8") as f:
                return f.read()

        raise RuntimeError(
            "Missing IAM public key. Set GOBIG_IAM_PUBLIC_KEY_PEM or "
            "GOBIG_IAM_PUBLIC_KEY_PATH (dev). In prod, load from B2 IAMConfigs."
        )

    async def load_b2_paths(self) -> dict[str, UploadPathRule]:
        """
        Load path resolutions at startup (never per-request).

        In prod this should be fetched from B2 (gobig-documentation/b2_paths.yaml).
        For local/dev we load from the repo path by default.
        """
        override = os.environ.get("GOBIG_B2_PATHS_YAML")
        path = Path(override) if override else Path("gobig-documentation/b2_paths.yaml")
        if not path.exists():
            raise RuntimeError(f"Missing b2 paths yaml at {path!s}")

        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        uploads: dict[str, Any] = (raw or {}).get("uploads") or {}
        rules: dict[str, UploadPathRule] = {}
        for upload_type, cfg in uploads.items():
            alias = str(cfg.get("alias", "")).strip()
            fmt = str(cfg.get("format", "")).strip()
            if not upload_type or not alias or not fmt:
                raise RuntimeError(f"Invalid b2_paths.yaml entry for {upload_type!r}")
            rules[upload_type] = UploadPathRule(upload_type=upload_type, alias=alias, format=fmt)
        if not rules:
            raise RuntimeError("b2_paths.yaml contained no uploads rules")
        return rules

    async def stage_nlp_batch(self, batch_id: str, payload: dict[str, Any]) -> None:
        """
        Stage NLP ingest payloads (flag OFF path): gzip-compressed JSON.

        Production should upload the same bytes to B2 staging. For local/dev we
        write ``{batch_id}.json.gz`` under ``GOBIG_NLP_STAGING_DIR``.
        """
        staging_dir = Path(os.environ.get("GOBIG_NLP_STAGING_DIR", "var/nlp-staging"))
        staging_dir.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        out = staging_dir / f"{batch_id}.json.gz"
        out.write_bytes(gzip.compress(raw, compresslevel=6))

    async def write_feature_flag_audit(
        self,
        *,
        flag_name: str,
        old_value: str,
        new_value: str,
        reason: str,
        admin_user_id: str,
        timestamp_iso: str | None = None,
    ) -> str:
        """
        Immutable audit record for feature-flag changes.

        Production: upload to a WORM / Object Lock bucket path (platform-owned).
        Local/tests: one JSON file per change under ``GOBIG_B2_FLAG_AUDIT_DIR``.
        """
        ts = timestamp_iso or datetime.now(timezone.utc).isoformat()
        audit_id = str(uuid.uuid4())
        record: dict[str, Any] = {
            "audit_id": audit_id,
            "flag_name": flag_name,
            "old_value": old_value,
            "new_value": new_value,
            "reason": reason,
            "admin_user_id": admin_user_id,
            "timestamp": ts,
        }
        audit_dir = Path(
            os.environ.get("GOBIG_B2_FLAG_AUDIT_DIR", "var/b2-audit/feature-flags"),
        )
        audit_dir.mkdir(parents=True, exist_ok=True)
        safe_ts = ts.replace(":", "-")
        path = audit_dir / f"{safe_ts}_{audit_id}.json"
        path.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
        return str(path)

    async def load_playbook_approval_token(self) -> str:
        """
        Load Slack approval token from B2 config source.

        Production should fetch from a protected B2 config object.
        Local/tests support env and file-based overrides.
        """
        token = os.environ.get("GOBIG_PLAYBOOK_APPROVAL_TOKEN")
        if token and token.strip():
            return token.strip()

        token_path = os.environ.get("GOBIG_PLAYBOOK_APPROVAL_TOKEN_PATH")
        if token_path:
            path = Path(token_path)
            if not path.is_absolute():
                project_root = Path(__file__).resolve().parent.parent.parent
                path = project_root / path
            if not path.exists():
                raise RuntimeError(f"Missing playbook approval token file at {path!s}")
            loaded = path.read_text(encoding="utf-8").strip()
            if not loaded:
                raise RuntimeError("Playbook approval token file is empty")
            return loaded

        raise RuntimeError(
            "Missing playbook approval token. Set GOBIG_PLAYBOOK_APPROVAL_TOKEN or "
            "GOBIG_PLAYBOOK_APPROVAL_TOKEN_PATH."
        )

    async def write_playbook_audit(
        self,
        *,
        actor: str,
        playbook_name: str,
        status: str,
        command: str,
        exit_code: int | None,
        output_summary: str,
        started_at: str | None = None,
        finished_at: str | None = None,
        duration_ms: int | None = None,
    ) -> str:
        ts = datetime.now(timezone.utc).isoformat()
        audit_id = str(uuid.uuid4())
        record: dict[str, Any] = {
            "audit_id": audit_id,
            "actor": actor,
            "playbook_name": playbook_name,
            "status": status,
            "command": command,
            "exit_code": exit_code,
            "output_summary": output_summary,
            "timestamp": ts,
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_ms": duration_ms,
        }
        audit_dir = Path(
            os.environ.get("GOBIG_B2_PLAYBOOK_AUDIT_DIR", "var/b2-audit/playbooks"),
        )
        audit_dir.mkdir(parents=True, exist_ok=True)
        safe_ts = ts.replace(":", "-")
        path = audit_dir / f"{safe_ts}_{audit_id}.json"
        path.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
        return str(path)

    async def write_job_retry_audit(
        self,
        *,
        actor: str,
        task_id: str,
        reason: str,
        timestamp_iso: str | None = None,
    ) -> str:
        ts = timestamp_iso or datetime.now(timezone.utc).isoformat()
        audit_id = str(uuid.uuid4())
        record: dict[str, Any] = {
            "audit_id": audit_id,
            "actor": actor,
            "task_id": task_id,
            "reason": reason,
            "timestamp": ts,
        }
        audit_dir = Path(
            os.environ.get("GOBIG_B2_JOB_AUDIT_DIR", "var/b2-audit/jobs"),
        )
        audit_dir.mkdir(parents=True, exist_ok=True)
        safe_ts = ts.replace(":", "-")
        path = audit_dir / f"{safe_ts}_{audit_id}.json"
        path.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
        return str(path)


b2_client = B2Client()

