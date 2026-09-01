from __future__ import annotations

import hashlib
import ipaddress
import secrets
from datetime import datetime, timedelta, timezone


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def secret_hint(value: str) -> str:
    return f"...{value[-6:]}"


async def register_secret(db, client_id: str, raw_secret: str, *, name: str, created_by: str | None,
                          expires_at=None) -> str:
    return str(await db.fetchval(
        """INSERT INTO oauth_service_client_secrets
           (client_id,secret_hash,secret_hint,name,created_by,expires_at)
           VALUES($1,$2,$3,$4,$5::uuid,$6) RETURNING id""",
        client_id, hash_secret(raw_secret), secret_hint(raw_secret), name, created_by, expires_at,
    ))


async def verify_secret(db, client_id: str, raw_secret: str, primary_hash: str | None = None) -> bool:
    candidate = hash_secret(raw_secret)
    row = await db.fetchrow(
        """SELECT id FROM oauth_service_client_secrets
           WHERE client_id=$1 AND secret_hash=$2 AND revoked_at IS NULL
             AND (expires_at IS NULL OR expires_at>NOW())""", client_id, candidate,
    )
    if row:
        await db.execute("UPDATE oauth_service_client_secrets SET last_used_at=NOW() WHERE id=$1", row["id"])
        return True
    return bool(primary_hash and secrets.compare_digest(str(primary_hash), candidate))


async def rotate_secret(db, client_id: str, *, created_by: str, overlap_hours: int = 24,
                        name: str = "Rotated secret") -> tuple[str, str, datetime]:
    raw = secrets.token_urlsafe(48)
    expiry = datetime.now(timezone.utc) + timedelta(hours=max(1, min(overlap_hours, 168)))
    async with db.transaction():
        primary_hash = await db.fetchval(
            "SELECT client_secret_hash FROM oauth_service_clients WHERE client_id=$1 FOR UPDATE", client_id,
        )
        if primary_hash:
            await db.execute(
                """INSERT INTO oauth_service_client_secrets
                   (client_id,secret_hash,secret_hint,name,created_by,expires_at)
                   VALUES($1,$2,'legacy','Legacy primary secret',$3::uuid,$4)
                   ON CONFLICT(client_id,secret_hash) DO NOTHING""",
                client_id, str(primary_hash), created_by, expiry,
            )
        await db.execute(
            """UPDATE oauth_service_client_secrets SET expires_at=LEAST(COALESCE(expires_at,$2),$2)
               WHERE client_id=$1 AND revoked_at IS NULL""", client_id, expiry,
        )
        secret_id = await register_secret(db, client_id, raw, name=name, created_by=created_by)
        await db.execute(
            "UPDATE oauth_service_clients SET client_secret_hash=$2,updated_at=NOW() WHERE client_id=$1",
            client_id, hash_secret(raw),
        )
    return secret_id, raw, expiry


async def ip_allowed(db, client_id: str, remote_ip: str | None) -> bool:
    rows = await db.fetch("SELECT cidr::text AS cidr FROM oauth_service_ip_allowlists WHERE client_id=$1", client_id)
    if not rows:
        return True
    if not remote_ip:
        return False
    try:
        address = ipaddress.ip_address(remote_ip)
    except ValueError:
        return False
    return any(address in ipaddress.ip_network(str(row["cidr"]), strict=False) for row in rows)
