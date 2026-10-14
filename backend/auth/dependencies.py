# auth/dependencies.py
from __future__ import annotations
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
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
    request: Request,
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
    # services/limiter.py's by_user=True limiters read this to key on the
    # signed-in user rather than the apparent client IP -- without it they
    # silently fell back to IP-keying (never actually set anywhere), which
    # on Cloud Run can collapse multiple distinct users behind one apparent
    # IP and share a rate-limit budget that was meant to be per-user.
    request.state.user_id = str(row["id"])
    return dict(row)


async def get_admin_user(current_user: Annotated[dict, Depends(get_current_user)]) -> dict:
    if current_user.get("role") != "admin":
        raise _FORBIDDEN
    return current_user


# A second OAuth2PasswordBearer with auto_error=False -- the default scheme
# above raises 401 on its own the moment a request has no Authorization
# header at all, before get_current_user ever runs, so there was no way to
# accept "signed in or not" short of a second scheme. Used by ADAR Eats'
# guest-open endpoints (marketplace browsing/search, the public menu
# assistant) so a visitor without an account gets treated as an anonymous
# guest instead of a 401 -- while a present-but-invalid/expired token still
# resolves to None rather than silently pretending to be a valid session.
_oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


async def get_optional_user(
    request: Request,
    token: Annotated[str | None, Depends(_oauth2_scheme_optional)],
    db=Depends(get_db),
) -> dict | None:
    if not token:
        return None
    try:
        payload = decode_token(token)
        user_id = payload.get("sub")
        if not user_id:
            return None
    except JWTError:
        return None
    row = await db.fetchrow(
        "SELECT id, email, full_name, role, created_at FROM users WHERE id = $1",
        user_id,
    )
    if not row:
        return None
    request.state.user_id = str(row["id"])
    return dict(row)


CurrentUser  = Annotated[dict, Depends(get_current_user)]
AdminUser    = Annotated[dict, Depends(get_admin_user)]
OptionalUser = Annotated[dict | None, Depends(get_optional_user)]