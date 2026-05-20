# auth/router.py
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr
from typing import Annotated

from auth.service import hash_password, verify_password, create_access_token
from auth.dependencies import CurrentUser
from database.connection import get_db

router = APIRouter()


class RegisterRequest(BaseModel):
    email:     EmailStr
    password:  str
    full_name: str = ""


class TokenResponse(BaseModel):
    access_token: str
    token_type:   str = "bearer"
    user_id:      str
    email:        str
    full_name:    str
    role:         str          # 'user' | 'admin'


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, db=Depends(get_db)):
    existing = await db.fetchrow("SELECT id FROM users WHERE email = $1", body.email)
    if existing:
        raise HTTPException(400, "Email already registered")
    if len(body.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    row = await db.fetchrow(
        "INSERT INTO users (email, hashed_password, full_name) VALUES ($1,$2,$3) RETURNING id, email, full_name",
        body.email, hash_password(body.password), body.full_name,
    )
    return {"message": "Account created", "user_id": str(row["id"]), "email": row["email"]}


@router.post("/login", response_model=TokenResponse)
async def login(form: Annotated[OAuth2PasswordRequestForm, Depends()], db=Depends(get_db)):
    row = await db.fetchrow(
        "SELECT id, email, hashed_password, full_name, role FROM users WHERE email = $1",
        form.username,
    )
    if not row or not verify_password(form.password, row["hashed_password"]):
        raise HTTPException(401, "Incorrect email or password")

    token = create_access_token(str(row["id"]), row["email"])
    return TokenResponse(
        access_token=token,
        user_id=str(row["id"]),
        email=row["email"],
        full_name=row["full_name"],
        role=row["role"],
    )


@router.get("/me")
async def me(current_user: CurrentUser):
    return current_user