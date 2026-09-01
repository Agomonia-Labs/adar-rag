from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Callable

from fastapi import Depends, HTTPException, Request, status
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
    token_kind: str = "user"
    organization_id: str | None = None

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
        """SELECT client_id,'user'::text AS token_kind,NULL::uuid AS organization_id,
                  NULL::text AS client_scope
             FROM oauth_clients WHERE client_id=$1 AND revoked_at IS NULL
           UNION ALL
           SELECT s.client_id,'service'::text AS token_kind,s.organization_id,s.scope AS client_scope
             FROM oauth_service_clients s
             LEFT JOIN developer_organizations o ON o.id=s.organization_id
            WHERE s.client_id=$1 AND s.revoked_at IS NULL
              AND (expires_at IS NULL OR expires_at>NOW())
              AND (s.organization_id IS NULL OR o.status='active')
           LIMIT 1""",
        client_id,
    )
    if not user or not client:
        raise _bearer_error(status.HTTP_401_UNAUTHORIZED, "API user or client is inactive")

    if user["role"] != "admin":
        granted = await _active_scopes(db, user_id, client_id)
        if not scopes <= granted:
            raise _bearer_error(status.HTTP_401_UNAUTHORIZED, "One or more API scope grants were revoked")

    client_kind = str(client.get("token_kind") or "user")
    token_kind = str(claims.get("token_kind") or client_kind)
    organization_id = str(client.get("organization_id")) if client.get("organization_id") else None
    if token_kind != client_kind:
        raise _bearer_error(status.HTTP_401_UNAUTHORIZED, "API token client type is invalid")
    if client_kind == "service":
        client_scopes = set(str(client.get("client_scope") or "").split())
        if not scopes <= client_scopes:
            raise _bearer_error(status.HTTP_401_UNAUTHORIZED, "One or more application scopes were revoked")
    if organization_id != (str(claims.get("organization_id")) if claims.get("organization_id") else None):
        raise _bearer_error(status.HTTP_401_UNAUTHORIZED, "API token organization is invalid")
    return ApiPrincipal(
        user=dict(user), client_id=client_id, scopes=scopes,
        token_kind=token_kind, organization_id=organization_id,
    )


def require_api_scope(scope: str) -> Callable:
    async def dependency(principal: ApiPrincipal = Depends(get_api_principal)) -> ApiPrincipal:
        if scope not in principal.scopes:
            raise _bearer_error(
                status.HTTP_403_FORBIDDEN,
                f"The API access token does not grant '{scope}'",
                scope=scope,
            )
        return principal

    dependency.required_scope = scope
    return dependency


async def validate_api_workspace_context(
    request: Request,
    principal: ApiPrincipal = Depends(get_api_principal),
    db=Depends(get_db),
) -> str | None:
    """Validate the optional stateless workspace selector for public API calls."""
    workspace_id = (request.headers.get("X-DocIntel-Workspace-ID") or "personal").strip()
    if not workspace_id or workspace_id.lower() == "personal":
        if principal.token_kind == "service" and principal.organization_id:
            raise HTTPException(status_code=403, detail="Organization applications require an explicitly granted workspace")
        request.state.api_workspace_id = None
        return None
    role = await db.fetchval(
        "SELECT role FROM workspace_members WHERE workspace_id=$1::uuid AND user_id=$2::uuid",
        workspace_id,
        principal.user_id,
    )
    if not role:
        raise HTTPException(status_code=403, detail="OAuth user cannot access the selected workspace")
    if principal.token_kind == "service" and principal.organization_id:
        granted = await db.fetchval(
            """SELECT 1 FROM oauth_service_workspace_grants
               WHERE client_id=$1 AND workspace_id=$2::uuid""",
            principal.client_id, workspace_id,
        )
        if not granted:
            raise HTTPException(status_code=403, detail="OAuth application is not granted access to this workspace")
    request.state.api_workspace_id = workspace_id
    request.state.api_workspace_role = role
    return workspace_id
