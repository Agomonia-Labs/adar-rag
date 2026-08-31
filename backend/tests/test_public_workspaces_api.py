from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from auth.api_oauth import ApiPrincipal
from routes import public_workspaces_api


def principal(user_id: str, email: str = "user@example.com") -> ApiPrincipal:
    return ApiPrincipal(
        user={"id": user_id, "email": email, "full_name": "User", "role": "user"},
        client_id="public-client",
        scopes=frozenset({"workspaces:read", "workspaces:write"}),
    )


class PersonalDb:
    def __init__(self):
        self.args = []

    async def fetchval(self, sql, *args):
        self.args.append((sql, args))
        return 4


@pytest.mark.anyio
async def test_personal_context_counts_only_authenticated_users_documents():
    db = PersonalDb()
    result = await public_workspaces_api._workspace_context(db, principal("user-a"), None)

    assert result["key"] == "personal"
    assert result["workspace_type"] == "personal"
    assert result["doc_count"] == 4
    assert db.args[0][1] == ("user-a",)
    assert "workspace_id IS NULL" in db.args[0][0]


class TeamDb:
    def __init__(self, memberships):
        self.memberships = memberships

    async def fetchrow(self, sql, *args):
        if "SELECT role FROM workspace_members" in sql:
            workspace_id, user_id = args
            role = self.memberships.get((workspace_id, user_id))
            return {"role": role} if role else None
        if "FROM workspaces w" in sql:
            workspace_id = args[0]
            return {
                "id": workspace_id,
                "name": "Engineering",
                "owner_id": "user-a",
                "member_count": 1,
                "doc_count": 2,
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
        return None


@pytest.mark.anyio
async def test_workspace_context_accepts_member_and_reports_role():
    db = TeamDb({("workspace-1", "user-a"): "editor"})
    result = await public_workspaces_api._workspace_context(db, principal("user-a"), "workspace-1")
    assert result["id"] == "workspace-1"
    assert result["my_role"] == "editor"


@pytest.mark.anyio
async def test_different_user_cannot_select_anothers_workspace():
    db = TeamDb({("workspace-1", "user-a"): "owner"})
    with pytest.raises(HTTPException) as exc:
        await public_workspaces_api._workspace_context(
            db, principal("user-b", "other@example.com"), "workspace-1"
        )
    assert exc.value.status_code == 403
    assert "not a member" in exc.value.detail
