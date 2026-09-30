#!/usr/bin/env python3
"""
Create a pre-verified demo/reviewer account for App Store review (or
TestFlight external testers) -- a plain 'user' role account, inserted
with is_verified already TRUE so it skips the email-verification-link
step that App Review can't complete. Mirrors create_admin.py's pattern,
minus the admin role.

This alone does not skip the OTP/MFA challenge on login -- add this same
email to the MFA_BYPASS_EMAILS env var in scripts/deploy-backend.sh (see
the comment near the bottom of that file) and redeploy, or login will
still send a one-time code to an inbox App Review can't check.

Usage (from backend/ folder, with DATABASE_URL pointed at PRODUCTION):
    python create_reviewer_account.py
"""
import asyncio, os, sys, getpass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from database.connection import init_pool, get_pool
from auth.service import hash_password


async def main():
    print("\n── DocIntel Reviewer Account ────────────")
    email    = input("Email:      ").strip()
    name     = input("Full name:  ").strip() or "App Review"
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
        existing = await conn.fetchrow("SELECT id FROM users WHERE email = $1", email)

        if existing:
            await conn.execute(
                "UPDATE users SET hashed_password = $1, is_verified = TRUE WHERE email = $2",
                hash_password(password), email,
            )
            print(f"✓ {email} already existed -- password reset and marked verified")
        else:
            row = await conn.fetchrow(
                """
                INSERT INTO users (email, hashed_password, full_name, role, is_verified)
                VALUES ($1, $2, $3, 'user', TRUE)
                RETURNING id
                """,
                email, hash_password(password), name,
            )
            print(f"✓ Reviewer account created  →  {email}  (id: {row['id']})")

    print("\nNext: add this email to MFA_BYPASS_EMAILS in scripts/deploy-backend.sh")
    print("and redeploy, or login will still trigger an OTP email code.")
    print("────────────────────────────────────────\n")


asyncio.run(main())
