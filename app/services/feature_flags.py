"""Admin-managed feature flags (Redis-backed)."""

from __future__ import annotations

import os


def admin_flag_names() -> tuple[str, ...]:
    raw = os.environ.get("GOBIG_ADMIN_FLAG_NAMES", "gobig_nlp_processing")
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return tuple(dict.fromkeys(parts))


def enabled_to_redis_value(enabled: bool) -> str:
    return "1" if enabled else "0"


def redis_value_label(value: str | None) -> str:
    if value is None:
        return ""
    return value.strip()


def is_flag_on(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "on", "yes", "enabled"}
