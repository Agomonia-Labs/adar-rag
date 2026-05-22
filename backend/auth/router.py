# auth/router.py
from __future__ import annotations
import hashlib, secrets, os
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr
from typing import Annotated

from auth.service       import hash_password, verify_password, create_access_token
from auth.dependencies  import CurrentUser
from database.connection import get_db
from services.limiter   import ip_3_per_min, ip_10_per_min
from services.audit     import audit, ip_from, ua_from
from services.notifications import send_verification_email

router   = APIRouter()
APP_URL  = os.getenv("APP_URL", "http://localhost:5173")


def _token_hash(tok: str) -> str:
    return hashlib.sha256(tok.encode()).hexdigest()


class RegisterRequest(BaseModel):
    email:     EmailStr
    password:  str
    full_name: str = ""


class LoginRequest(BaseModel):
    email:    EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type:   str = "bearer"
    user_id:      str
    email:        str
    full_name:    str
    role:         str


# ── Register ───────────────────────────────────────────────────────────────────
@router.post("/register", status_code=201)
async def register(
    body:    RegisterRequest,
    request: Request,
    db=Depends(get_db),
    _rl=Depends(ip_3_per_min),
):
    existing = await db.fetchrow("SELECT id FROM users WHERE email = $1", body.email)
    if existing:
        raise HTTPException(400, "An account with this email already exists")
    if len(body.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    # First user → admin, auto-verified (no chicken-and-egg)
    user_count = await db.fetchval("SELECT COUNT(*) FROM users")
    role        = "admin" if user_count == 0 else "user"
    auto_verify = role == "admin"   # admins skip email verification

    # Generate verification token
    token      = secrets.token_urlsafe(32)
    token_h    = _token_hash(token)
    token_exp  = datetime.now(timezone.utc) + timedelta(hours=24)

    row = await db.fetchrow(
        """INSERT INTO users
               (email, hashed_password, full_name, role,
                is_verified, verification_token_hash, verification_token_exp)
           VALUES ($1,$2,$3,$4,$5,$6,$7)
           RETURNING id, email, full_name, role""",
        body.email, hash_password(body.password), body.full_name, role,
        auto_verify, None if auto_verify else token_h,
        None        if auto_verify else token_exp,
    )
    user_id = str(row["id"])

    await audit(db, user_id=user_id, action="register",
                resource_type="user", resource_id=user_id,
                metadata={"email": body.email, "role": role},
                ip_address=ip_from(request), user_agent=ua_from(request))

    if not auto_verify:
        try:
            await send_verification_email(body.email, token, APP_URL)
        except Exception:
            pass   # don't block registration if email fails

    return {
        "message":      "Account created" if auto_verify else "Account created — please check your email to verify your address",
        "user_id":      user_id,
        "email":        row["email"],
        "role":         row["role"],
        "is_verified":  auto_verify,
        "needs_verify": not auto_verify,
    }


# ── Verify email ───────────────────────────────────────────────────────────────
@router.get("/verify-email")
async def verify_email(token: str, db=Depends(get_db)):
    token_h = _token_hash(token)
    row = await db.fetchrow(
        """SELECT id, email, is_verified, verification_token_exp
           FROM users
           WHERE verification_token_hash = $1""",
        token_h,
    )
    if not row:
        raise HTTPException(400, "Invalid or already-used verification link")
    if row["is_verified"]:
        return {"message": "Email already verified — you can log in"}
    if row["verification_token_exp"] and row["verification_token_exp"] < datetime.now(timezone.utc):
        raise HTTPException(400, "Verification link has expired — please request a new one")

    await db.execute(
        """UPDATE users
           SET is_verified=TRUE, verification_token_hash=NULL, verification_token_exp=NULL
           WHERE id=$1""",
        row["id"],
    )
    return {"message": "Email verified — you can now log in", "email": row["email"]}


# ── Resend verification ────────────────────────────────────────────────────────
class ResendRequest(BaseModel):
    email: EmailStr

@router.post("/resend-verification")
async def resend_verification(body: ResendRequest, db=Depends(get_db), _rl=Depends(ip_3_per_min)):
    row = await db.fetchrow(
        "SELECT id, email, is_verified FROM users WHERE email = $1", body.email
    )
    if not row:
        # Don't reveal if email exists
        return {"message": "If that email is registered and unverified, a new link has been sent"}
    if row["is_verified"]:
        return {"message": "This email is already verified"}

    token     = secrets.token_urlsafe(32)
    token_h   = _token_hash(token)
    token_exp = datetime.now(timezone.utc) + timedelta(hours=24)
    await db.execute(
        "UPDATE users SET verification_token_hash=$1, verification_token_exp=$2 WHERE id=$3",
        token_h, token_exp, row["id"],
    )
    try:
        await send_verification_email(row["email"], token, APP_URL)
    except Exception:
        pass
    return {"message": "If that email is registered and unverified, a new link has been sent"}


# ── Login ──────────────────────────────────────────────────────────────────────
@router.post("/login", response_model=TokenResponse)
async def login(
    body:    LoginRequest,
    request: Request,
    db=Depends(get_db),
    _rl=Depends(ip_10_per_min),
):
    row = await db.fetchrow(
        "SELECT id, email, hashed_password, full_name, role, is_verified FROM users WHERE email=$1",
        body.email,
    )
    if not row or not verify_password(body.password, row["hashed_password"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")

    if not row["is_verified"]:
        raise HTTPException(
            403,
            "Please verify your email address before logging in. "
            "Check your inbox or use the resend link."
        )

    await audit(db, user_id=str(row["id"]), action="login",
                resource_type="user", resource_id=str(row["id"]),
                ip_address=ip_from(request), user_agent=ua_from(request))

    token = create_access_token(str(row["id"]), row["email"])
    return TokenResponse(
        access_token=token,
        user_id=str(row["id"]),
        email=row["email"],
        full_name=row["full_name"] or "",
        role=row["role"] or "user",
    )


# ── Me ─────────────────────────────────────────────────────────────────────────
@router.get("/me")
async def me(current_user: CurrentUser):
    return {
        "id":        str(current_user["id"]),
        "email":     current_user["email"],
        "full_name": current_user.get("full_name", ""),
        "role":      current_user.get("role", "user"),
    }