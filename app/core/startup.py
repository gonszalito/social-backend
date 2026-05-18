from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from app.integration.b2_client import b2_client
from app.integration.postgres_client import postgres_client
from app.api.dependencies.auth import auth_service
from app.core.storage_config import StorageConfig, set_storage_config


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    public_key_pem = await b2_client.load_iam_public_key_pem()
    auth_service.set_public_key_pem(public_key_pem)

    upload_rules = await b2_client.load_b2_paths()
    set_storage_config(StorageConfig(upload_rules=upload_rules))
    await postgres_client.open()
    try:
        yield
    finally:
        await postgres_client.close()

