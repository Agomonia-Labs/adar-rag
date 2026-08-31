from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

from auth.service import ALGORITHM, SECRET_KEY
from database.connection import get_db
from routes.oauth import ALLOWED_SCOPES, API_RESOURCE, ISSUER, _active_scopes


API_RESOURCE_METADATA = f"{ISSUER}/.well-known/oauth-protected-resource/api"
api_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/token", auto_error=False)


@dataclass(frozen=True)
class ApiPrincipal:
    user: dict
    client_id: str
    scopes: frozenset[str]

    @property
    def user_id(self) -> str:
        return str(self.user["id"])


def _bearer_error(status_code: int, detail: str, *, scope: str | None = None) -> HTTPException:
    challenge = f'Bearer resource_metadata="{API_RESOURCE_METADATA}"'
    if scope:
        challenge += f' error="insufficient_scope", scope="{scope}"'
    return HTTPException(status_code=status_code, detail=detail, headers={"WWW-Authenticate": challenge})


async def get_api_principal(
    token: Annotated[str | None, Depends(api_oauth2_scheme)],
    db=Depends(get_db),
) -> ApiPrincipal:
    if not token:
        raise _bearer_error(status.HTTP_401_UNAUTHORIZED, "API authentication required")
    try:
        claims = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM],
            audience=API_RESOURCE,
            issuer=ISSUER,
        )
    except JWTError as exc:
        raise _bearer_error(status.HTTP_401_UNAUTHORIZED, "Invalid or expired API access token") from exc

    user_id = str(claims.get("sub") or "")
    client_id = str(claims.get("client_id") or "")
    scopes = frozenset(str(claims.get("scope") or "").split())
    if not user_id or not client_id or not scopes or not scopes <= ALLOWED_SCOPES:
        raise _bearer_error(status.HTTP_401_UNAUTHORIZED, "Invalid API token claims")

    user = await db.fetchrow(
        "SELECT id,email,full_name,role,created_at FROM users WHERE id=$1::uuid",
        user_id,
    )
    client = await db.fetchrow(
        """SELECT client_id FROM oauth_clients WHERE client_id=$1 AND revoked_at IS NULL
           UNION ALL
           SELECT client_id FROM oauth_service_clients
            WHERE client_id=$1 AND revoked_at IS NULL
              AND (expires_at IS NULL OR expires_at>NOW())
           LIMIT 1""",
        client_id,
    )
    if not user or not client:
        raise _bearer_error(status.HTTP_401_UNAUTHORIZED, "API user or client is inactive")

    if user["role"] != "admin":
        granted = await _active_scopes(db, user_id, client_id)
        if not scopes <= granted:
            raise _bearer_error(status.HTTP_401_UNAUTHORIZED, "One or more API scope grants were revoked")

    return ApiPrincipal(user=dict(user), client_id=client_id, scopes=scopes)


def require_api_scope(scope: str) -> Callable:
    async def dependency(principal: ApiPrincipal = Depends(get_api_principal)) -> ApiPrincipal:
        if scope not in principal.scopes:
            raise _bearer_error(
                status.HTTP_403_FORBIDDEN,
                f"The API access token does not grant '{scope}'",
                scope=scope,
            )
        return principal

    return dependency
