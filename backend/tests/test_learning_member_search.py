from __future__ import annotations

import pytest

from routes import learning


class CandidateDb:
    async def fetch(self, sql, *args):
        assert "workspace_members" in sql
        assert "NOT EXISTS" in sql
        assert args == ("workspace-1", "course-1", "%student%", "student")
        return [{
            "id": "student-user",
            "email": "student@example.com",
            "full_name": "Student User",
            "workspace_role": "viewer",
        }]


@pytest.mark.anyio
async def test_course_manager_can_search_unenrolled_workspace_members(monkeypatch):
    async def fake_course_access(db, course_id, user_id, manage=False):
        assert manage is True
        return {"workspace_id": "workspace-1"}

    monkeypatch.setattr(learning, "_course_access", fake_course_access)
    result = await learning.search_course_member_candidates(
        "course-1",
        " student ",
        current_user={"id": "owner-user"},
        db=CandidateDb(),
    )

    assert result == [{
        "user_id": "student-user",
        "email": "student@example.com",
        "full_name": "Student User",
        "workspace_role": "viewer",
    }]
