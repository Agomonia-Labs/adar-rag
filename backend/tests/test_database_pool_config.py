import asyncio

import pytest

import database.connection as connection
from database.connection import pool_sizes


def test_pool_sizes_use_bounded_defaults(monkeypatch):
    monkeypatch.delenv("DB_POOL_MIN_SIZE", raising=False)
    monkeypatch.delenv("DB_POOL_MAX_SIZE", raising=False)
    assert pool_sizes() == (1, 5)


def test_pool_sizes_are_configurable(monkeypatch):
    monkeypatch.setenv("DB_POOL_MIN_SIZE", "2")
    monkeypatch.setenv("DB_POOL_MAX_SIZE", "8")
    assert pool_sizes() == (2, 8)


def test_pool_minimum_cannot_exceed_maximum(monkeypatch):
    monkeypatch.setenv("DB_POOL_MIN_SIZE", "10")
    monkeypatch.setenv("DB_POOL_MAX_SIZE", "3")
    assert pool_sizes() == (3, 3)


@pytest.mark.asyncio
async def test_init_pool_retries_transient_failure(monkeypatch):
    expected_pool = object()
    attempts = 0

    async def fake_create_pool(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("database temporarily unavailable")
        return expected_pool

    monkeypatch.setattr(connection, "_pool", None)
    monkeypatch.setattr(connection.asyncpg, "create_pool", fake_create_pool)
    monkeypatch.setenv("DB_POOL_CONNECT_ATTEMPTS", "2")
    monkeypatch.setenv("DB_POOL_RETRY_DELAY_SECONDS", "0")

    assert await connection.init_pool() is expected_pool
    assert attempts == 2


@pytest.mark.asyncio
async def test_init_pool_serializes_concurrent_callers(monkeypatch):
    expected_pool = object()
    attempts = 0

    async def fake_create_pool(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        await asyncio.sleep(0)
        return expected_pool

    monkeypatch.setattr(connection, "_pool", None)
    monkeypatch.setattr(connection.asyncpg, "create_pool", fake_create_pool)

    pools = await asyncio.gather(connection.init_pool(), connection.init_pool())

    assert pools == [expected_pool, expected_pool]
    assert attempts == 1
