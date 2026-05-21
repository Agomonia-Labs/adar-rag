#!/usr/bin/env python3
"""
Create or promote an admin user.

Usage (from backend/ folder):
    python scripts/create_admin.py

Usage (via Docker):
    docker compose exec backend python scripts/create_admin.py
"""
import asyncio, os, sys, getpass
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from database.connection import init_pool, get_pool
from auth.service import hash_password


async def main():
    print("\n── DocIntel Admin Setup ─────────────────")
    email    = input("Email:      ").strip()
    name     = input("Full name:  ").strip()
    password = getpass.getpass("Password:   ")
    confirm  = getpass.getpass("Confirm:    ")

    if password != confirm:
        print("✗ Passwords do not match")
        return
    if len(password) < 8:
        print("✗ Password must be at least 8 characters")
        return

    await init_pool()
    pool = get_pool()

    async with pool.acquire() as conn:
        existing = await conn.fetchrow("SELECT id, role FROM users WHERE email = $1", email)

        if existing:
            if existing["role"] == "admin":
                print(f"✓ {email} is already an admin")
            else:
                await conn.execute("UPDATE users SET role = 'admin' WHERE email = $1", email)
                print(f"✓ {email} promoted to admin")
        else:
            row = await conn.fetchrow(
                """
                INSERT INTO users (email, hashed_password, full_name, role)
                VALUES ($1, $2, $3, 'admin')
                RETURNING id
                """,
                email, hash_password(password), name,
            )
            print(f"✓ Admin account created  →  {email}  (id: {row['id']})")

    print("────────────────────────────────────────\n")


asyncio.run(main())