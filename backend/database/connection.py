# database/connection.py
from __future__ import annotations
import os, asyncpg

_pool: asyncpg.Pool | None = None

DATABASE_URL  = os.getenv("DATABASE_URL", "postgresql://docintel:docintel_secret@localhost:5432/docintel")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1536"))


async def init_pool() -> asyncpg.Pool:
    global _pool
    _pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=20)
    return _pool


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
    async with get_pool().acquire() as conn:
        yield conn
