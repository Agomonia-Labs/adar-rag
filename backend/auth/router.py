# auth/router.py
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from typing import Annotated

from auth.service import hash_password, verify_password, create_access_token
from auth.dependencies import CurrentUser
from database.connection import get_db
from services.limiter import ip_3_per_min, ip_10_per_min

router = APIRouter()


class RegisterRequest(BaseModel):
    email:     EmailStr
    password:  str
    full_name: str = ""


class LoginRequest(BaseModel):
    email:    EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type:   str   = "bearer"
    user_id:      str
    email:        str
    full_name:    str
    role:         str   # 'user' | 'admin'


# ── Register ───────────────────────────────────────────────────────────────────
@router.post("/register", status_code=201)
async def register(
    body: RegisterRequest,
    db=Depends(get_db),
    _rl=Depends(ip_3_per_min),
):
    existing = await db.fetchrow("SELECT id FROM users WHERE email = $1", body.email)
    if existing:
        raise HTTPException(400, "An account with this email already exists")
    if len(body.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    row = await db.fetchrow(
        "INSERT INTO users (email, hashed_password, full_name) VALUES ($1,$2,$3) RETURNING id, email, full_name",
        body.email, hash_password(body.password), body.full_name,
    )
    return {"message": "Account created", "user_id": str(row["id"]), "email": row["email"]}


# ── Login ──────────────────────────────────────────────────────────────────────
@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    db=Depends(get_db),
    _rl=Depends(ip_10_per_min),
):
    row = await db.fetchrow(
        "SELECT id, email, hashed_password, full_name, role FROM users WHERE email = $1",
        body.email,
    )
    if not row or not verify_password(body.password, row["hashed_password"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")

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