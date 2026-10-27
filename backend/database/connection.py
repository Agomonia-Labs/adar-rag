# database/connection.py
from __future__ import annotations

import asyncio
import logging
import os

import asyncpg

_pool: asyncpg.Pool | None = None
_pool_lock = asyncio.Lock()
log = logging.getLogger(__name__)

DATABASE_URL  = os.getenv("DATABASE_URL", "postgresql://docintel:docintel_secret@localhost:5432/docintel")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1536"))


def pool_sizes() -> tuple[int, int]:
    min_size = max(0, int(os.getenv("DB_POOL_MIN_SIZE", "1")))
    max_size = max(1, int(os.getenv("DB_POOL_MAX_SIZE", "5")))
    if min_size > max_size:
        min_size = max_size
    return min_size, max_size


async def init_pool() -> asyncpg.Pool:
    global _pool
    if _pool is not None:
        return _pool

    async with _pool_lock:
        if _pool is not None:
            return _pool

        min_size, max_size = pool_sizes()
        attempts = max(1, int(os.getenv("DB_POOL_CONNECT_ATTEMPTS", "5")))
        delay = max(0.0, float(os.getenv("DB_POOL_RETRY_DELAY_SECONDS", "1")))
        timeout = max(1.0, float(os.getenv("DB_POOL_CONNECT_TIMEOUT_SECONDS", "15")))

        for attempt in range(1, attempts + 1):
            try:
                pool = await asyncio.wait_for(
                    asyncpg.create_pool(
                        DATABASE_URL,
                        min_size=min_size,
                        max_size=max_size,
                    ),
                    timeout=timeout,
                )
                _pool = pool
                return pool
            except Exception:
                if attempt == attempts:
                    raise
                wait_seconds = min(delay * (2 ** (attempt - 1)), 10.0)
                log.warning(
                    "Database pool initialization failed (attempt %s/%s); retrying in %.1fs",
                    attempt,
                    attempts,
                    wait_seconds,
                    exc_info=True,
                )
                await asyncio.sleep(wait_seconds)

        raise RuntimeError("Database pool initialization exhausted all attempts")


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool not initialised")
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def get_db():
    """FastAPI dependency — yields a connection from the pool."""
    pool = await init_pool()
    async with pool.acquire() as conn:
        yield conn
