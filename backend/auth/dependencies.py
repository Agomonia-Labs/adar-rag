# auth/dependencies.py
from __future__ import annotations
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError

from auth.service import decode_token
from database.connection import get_db

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")
_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid or expired token",
    headers={"WWW-Authenticate": "Bearer"},
)
_FORBIDDEN = HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db=Depends(get_db),
) -> dict:
    try:
        payload = decode_token(token)
        user_id = payload.get("sub")
        if not user_id:
            raise _UNAUTHORIZED
    except JWTError:
        raise _UNAUTHORIZED

    row = await db.fetchrow(
        "SELECT id, email, full_name, role, created_at FROM users WHERE id = $1",
        user_id,
    )
    if not row:
        raise _UNAUTHORIZED
    return dict(row)


async def get_admin_user(current_user: Annotated[dict, Depends(get_current_user)]) -> dict:
    if current_user.get("role") != "admin":
        raise _FORBIDDEN
    return current_user


CurrentUser = Annotated[dict, Depends(get_current_user)]
AdminUser   = Annotated[dict, Depends(get_admin_user)]