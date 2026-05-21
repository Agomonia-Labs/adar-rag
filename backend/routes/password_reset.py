# routes/password_reset.py
from __future__ import annotations
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr

from database.connection import get_db
from services.email import generate_reset_token, hash_token, reset_url, send_reset_email
from auth.service import hash_password
from services.limiter import ip_5_per_min, ip_10_per_min

router = APIRouter()

RESET_EXPIRE_HOURS = int(os.getenv("RESET_TOKEN_EXPIRE_HOURS", "1"))


class ForgotRequest(BaseModel):
    email: EmailStr


class ResetRequest(BaseModel):
    token:        str
    new_password: str


# ── POST /api/auth/forgot-password ────────────────────────────────────────────
@router.post("/forgot-password")
async def forgot_password(
    body: ForgotRequest,
    db=Depends(get_db),
    _rl=Depends(ip_5_per_min),          # 5 requests / min / IP
):
    user = await db.fetchrow(
        "SELECT id, email, full_name FROM users WHERE email = $1", body.email
    )

    if user:
        # Invalidate old unused tokens
        await db.execute(
            "UPDATE password_reset_tokens SET used=TRUE WHERE user_id=$1 AND used=FALSE",
            user["id"],
        )
        raw, hashed = generate_reset_token()
        expires_at  = datetime.now(timezone.utc) + timedelta(hours=RESET_EXPIRE_HOURS)
        await db.execute(
            "INSERT INTO password_reset_tokens (user_id, token_hash, expires_at) VALUES ($1,$2,$3)",
            user["id"], hashed, expires_at,
        )
        try:
            name = user["full_name"] or user["email"].split("@")[0]
            await send_reset_email(user["email"], name, reset_url(raw))
        except Exception:
            pass

    # Always same response — prevents user enumeration
    return {"message": "If an account with that email exists, a reset link has been sent."}


# ── POST /api/auth/reset-password ─────────────────────────────────────────────
@router.post("/reset-password")
async def reset_password(
    body: ResetRequest,
    db=Depends(get_db),
    _rl=Depends(ip_10_per_min),
):
    if len(body.new_password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    token_hash = hash_token(body.token)
    now        = datetime.now(timezone.utc)

    row = await db.fetchrow(
        """SELECT id, user_id FROM password_reset_tokens
           WHERE token_hash=$1 AND used=FALSE AND expires_at>$2""",
        token_hash, now,
    )
    if not row:
        raise HTTPException(400, "Invalid or expired reset token")

    await db.execute(
        "UPDATE users SET hashed_password=$1 WHERE id=$2",
        hash_password(body.new_password), row["user_id"],
    )
    await db.execute(
        "UPDATE password_reset_tokens SET used=TRUE WHERE id=$1", row["id"],
    )
    return {"message": "Password updated successfully. You can now sign in."}


# ── GET /api/auth/verify-reset-token ──────────────────────────────────────────
@router.get("/verify-reset-token")
async def verify_reset_token(token: str, db=Depends(get_db)):
    token_hash = hash_token(token)
    now        = datetime.now(timezone.utc)
    row = await db.fetchrow(
        "SELECT id FROM password_reset_tokens WHERE token_hash=$1 AND used=FALSE AND expires_at>$2",
        token_hash, now,
    )
    if not row:
        raise HTTPException(400, "Invalid or expired reset token")
    return {"valid": True}