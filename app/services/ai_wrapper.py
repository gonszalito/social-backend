"""
HTTP client for gobig-ai-wrapper (Finland cluster).

NLP ingest (API-M1-03) calls :func:`enrich_nlp_batch`. Sentry triage (API-M2-02) will use
:func:`triage_sentry_error`. Until the real service URL exists, point ``GOBIG_AI_WRAPPER_URL`` at a
local mock (see ``scripts/mock_ai_wrapper.py``) or use pytest mocks.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

_DEFAULT_BASE = "http://localhost:8099"
_TIMEOUT_S = 30.0


def _base_url() -> str:
    return os.environ.get("GOBIG_AI_WRAPPER_URL", _DEFAULT_BASE).rstrip("/")


async def enrich_nlp_batch(batch_context: dict[str, Any], max_tokens: int = 2000) -> dict[str, Any]:
    """POST ``/nlp/enrich`` — used when ``gobig_nlp_processing`` is ON."""
    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
        resp = await client.post(
            f"{_base_url()}/nlp/enrich",
            json={"context": batch_context, "max_tokens": max_tokens},
        )
        resp.raise_for_status()
        return resp.json()


async def triage_sentry_error(
    stack_trace: str,
    context: str,
    *,
    max_tokens: int = 800,
) -> dict[str, Any]:
    """POST ``/sentry/triage`` — reserved for API-M2-02."""
    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
        resp = await client.post(
            f"{_base_url()}/sentry/triage",
            json={"stack_trace": stack_trace, "context": context, "max_tokens": max_tokens},
        )
        resp.raise_for_status()
        return resp.json()
