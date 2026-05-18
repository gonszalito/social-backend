from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter


class PlaybookExecutionError(RuntimeError):
    def __init__(self, message: str, *, exit_code: int | None = None) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class PlaybookRunResult:
    status: str
    started_at: str
    finished_at: str
    duration_ms: int
    command: str
    exit_code: int
    stdout: str
    stderr: str


ALLOWED_PLAYBOOKS: dict[str, str] = {
    "restart-celery": "restart-celery.sh",
    "refresh-faiss-cache": "refresh-faiss-cache.sh",
    "flush-redis-tier-cache": "flush-redis-tier-cache.sh",
    "scale-finland-workers": "scale-finland-workers.sh",
}


def _safe_output_excerpt(text: str, *, max_chars: int = 1000) -> str:
    cleaned = (text or "").strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return f"{cleaned[:max_chars]}...(truncated)"


def _resolve_playbook_script(playbook_name: str) -> Path:
    script_name = ALLOWED_PLAYBOOKS[playbook_name]
    base_dir = Path(os.environ.get("GOBIG_PLAYBOOKS_DIR", "scripts/playbooks")).resolve()
    script_path = (base_dir / script_name).resolve()
    if base_dir not in script_path.parents:
        raise PlaybookExecutionError("Resolved script is outside sandbox directory")
    if not script_path.is_file():
        raise PlaybookExecutionError(
            f"Playbook script missing for {playbook_name}",
        )
    return script_path


def _read_timeout_s() -> int:
    raw = os.environ.get("GOBIG_PLAYBOOK_TIMEOUT_S", "30").strip()
    try:
        timeout = int(raw)
    except ValueError as exc:
        raise PlaybookExecutionError("Invalid GOBIG_PLAYBOOK_TIMEOUT_S value") from exc
    if timeout <= 0:
        raise PlaybookExecutionError("GOBIG_PLAYBOOK_TIMEOUT_S must be > 0")
    return timeout


def _run_playbook_sync(script_path: Path, timeout_s: int) -> subprocess.CompletedProcess[str]:
    command = ["bash", str(script_path)]
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
            shell=False,
            cwd=str(script_path.parent),
        )
    except subprocess.TimeoutExpired as exc:
        raise PlaybookExecutionError("Playbook execution timed out") from exc


async def execute_playbook(playbook_name: str) -> PlaybookRunResult:
    script_path = _resolve_playbook_script(playbook_name)
    timeout_s = _read_timeout_s()
    command = ["bash", str(script_path)]

    started_perf = perf_counter()
    started_at = datetime.now(timezone.utc).isoformat()
    completed = await asyncio.to_thread(_run_playbook_sync, script_path, timeout_s)
    finished_at = datetime.now(timezone.utc).isoformat()
    duration_ms = int((perf_counter() - started_perf) * 1000)

    status = "succeeded" if completed.returncode == 0 else "failed"
    result = PlaybookRunResult(
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=duration_ms,
        command=shlex.join(command),
        exit_code=completed.returncode,
        stdout=_safe_output_excerpt(completed.stdout),
        stderr=_safe_output_excerpt(completed.stderr),
    )
    return result
