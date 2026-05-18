from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any

from psycopg_pool import AsyncConnectionPool


@dataclass
class PostgresConfig:
    enabled: bool
    dsn: str | None
    min_size: int = 1
    max_size: int = 5


class PostgresClient:
    def __init__(self) -> None:
        self._pool: AsyncConnectionPool | None = None
        self._cfg = PostgresConfig(enabled=False, dsn=None)
        self._pool_lock = asyncio.Lock()

    def configure_from_env(self) -> None:
        enabled = os.getenv("GOBIG_POSTGRES_ENABLED", "0").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
            "enabled",
        }
        dsn = os.getenv("DATABASE_URL", "").strip() or None
        min_size = int(os.getenv("GOBIG_POSTGRES_POOL_MIN_SIZE", "1"))
        max_size = int(os.getenv("GOBIG_POSTGRES_POOL_MAX_SIZE", "5"))
        self._cfg = PostgresConfig(enabled=enabled, dsn=dsn, min_size=min_size, max_size=max_size)

    async def open(self) -> None:
        self.configure_from_env()
        if not self._cfg.enabled or not self._cfg.dsn:
            return
        await self._ensure_pool()

    async def _ensure_pool(self) -> None:
        self.configure_from_env()
        if not self._cfg.enabled or not self._cfg.dsn:
            raise RuntimeError("Postgres not enabled or DATABASE_URL is missing")
        if self._pool is not None:
            return
        async with self._pool_lock:
            if self._pool is not None:
                return
            self._pool = AsyncConnectionPool(
                conninfo=self._cfg.dsn,
                min_size=self._cfg.min_size,
                max_size=self._cfg.max_size,
                open=False,
            )
            await self._pool.open(wait=True)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @property
    def enabled(self) -> bool:
        self.configure_from_env()
        return bool(self._cfg.enabled and self._cfg.dsn)

    async def execute(self, query: str, params: tuple[Any, ...] = ()) -> None:
        await self._ensure_pool()
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, params)
            await conn.commit()

    async def fetchone(self, query: str, params: tuple[Any, ...] = ()) -> Any:
        await self._ensure_pool()
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, params)
                return await cur.fetchone()


postgres_client = PostgresClient()
