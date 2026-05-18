from __future__ import annotations

from dataclasses import dataclass

from app.integration.b2_client import UploadPathRule


@dataclass
class StorageConfig:
    upload_rules: dict[str, UploadPathRule]


storage_config: StorageConfig | None = None


def set_storage_config(cfg: StorageConfig) -> None:
    global storage_config
    storage_config = cfg


def get_storage_config() -> StorageConfig:
    if storage_config is None:
        raise RuntimeError("Storage config not loaded (startup ordering issue).")
    return storage_config

