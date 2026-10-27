from __future__ import annotations

from datetime import datetime, timezone

import pytest

from routes import workspaces


class WorkspaceMemberDb:
    def __init__(self, role: str = "owner"):
        self.role = role
        self.inserted = None

    async def fetchrow(self, sql, *args):
        if "SELECT role FROM workspace_members" in sql:
            if args[1] == "owner-user":
                return {"role": self.role}
            return None
        if "FROM users WHERE LOWER(email)" in sql:
            return {
                "id": "member-user",
                "email": "member@example.com",
                "full_name": "Member User",
            }
        if "INSERT INTO workspace_members" in sql:
            self.inserted = args
            return {"joined_at": datetime(2026, 10, 27, tzinfo=timezone.utc)}
        return None

    async def fetchval(self, sql, *args):
        if "SELECT name FROM workspaces" in sql:
            return "Enterprise AI Foundations"
        return None

    async def execute(self, sql, *args):
        return "UPDATE 1"


class WorkspaceSearchDb(WorkspaceMemberDb):
    async def fetch(self, sql, *args):
        assert "NOT EXISTS" in sql
        assert args == ("workspace-1", "%member%", "member")
        return [
            {"id": "member-user", "email": "member@example.com", "full_name": "Member User"},
        ]


@pytest.mark.anyio
@pytest.mark.parametrize("role", ["viewer", "editor"])
async def test_owner_can_add_workspace_viewer_or_editor(monkeypatch, role):
    db = WorkspaceMemberDb()
    notifications = []

    async def fake_audit(*args, **kwargs):
        return None

    async def fake_notify(*args):
        notifications.append(args)

    monkeypatch.setattr(workspaces, "audit", fake_audit)
    monkeypatch.setattr(workspaces, "send_workspace_invite", fake_notify)

    result = await workspaces.invite_member(
        "workspace-1",
        workspaces.InviteMember(email="  MEMBER@EXAMPLE.COM ", role=role),
        current_user={"id": "owner-user", "email": "owner@example.com"},
        db=db,
    )

    assert db.inserted == ("workspace-1", "member-user", role, "owner-user")
    assert result["member"] == {
        "user_id": "member-user",
        "email": "member@example.com",
        "full_name": "Member User",
        "role": role,
        "joined_at": "2026-10-27T00:00:00+00:00",
    }
    assert notifications == [(
        "member@example.com",
        "Enterprise AI Foundations",
        "owner@example.com",
        role,
        workspaces._APP_URL,
    )]


@pytest.mark.anyio
async def test_owner_can_search_registered_non_members():
    result = await workspaces.search_workspace_members(
        "workspace-1",
        " member ",
        current_user={"id": "owner-user", "email": "owner@example.com"},
        db=WorkspaceSearchDb(),
    )

    assert result == [{
        "user_id": "member-user",
        "email": "member@example.com",
        "full_name": "Member User",
    }]
