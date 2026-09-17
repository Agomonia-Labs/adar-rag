from __future__ import annotations

import os

os.environ.setdefault("OPENAI_API_KEY", "test-key-not-used")

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from auth.api_oauth import ApiPrincipal, _scope_for_request
from routes import learning, public_learning_api


def principal(*scopes: str) -> ApiPrincipal:
    return ApiPrincipal(
        user={"id": "user-1", "role": "user"},
        client_id="client-1",
        scopes=frozenset(scopes),
    )


def request(workspace_id: str | None = "workspace-1") -> Request:
    value = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    value.state.api_workspace_id = workspace_id
    return value


def route_scope(path: str, method: str) -> str:
    route = next(
        item for item in public_learning_api.router.routes
        if item.path == path and method in item.methods
    )
    dependencies = [
        dependency.call for dependency in route.dependant.dependencies
        if hasattr(dependency.call, "required_scope")
    ]
    assert len(dependencies) == 1
    return dependencies[0].required_scope


@pytest.mark.parametrize(
    ("path", "method", "scope"),
    [
        ("/learning/courses", "GET", "learning:read"),
        ("/learning/courses", "POST", "learning:manage"),
        ("/learning/courses/{course_id}", "GET", "learning:read"),
        ("/learning/courses/{course_id}", "PATCH", "learning:manage"),
        ("/learning/courses/{course_id}", "DELETE", "learning:manage"),
        ("/learning/courses/{course_id}/scope", "GET", "learning:read"),
        ("/learning/courses/{course_id}/curriculum", "PUT", "learning:manage"),
        ("/learning/courses/{course_id}/members", "POST", "learning:manage"),
        ("/learning/courses/{course_id}/directory", "GET", "learning:read"),
        ("/learning/courses/{course_id}/profile", "PATCH", "learning:participate"),
        ("/learning/courses/{course_id}/calendar", "GET", "learning:read"),
        ("/learning/courses/{course_id}/calendar", "POST", "learning:participate"),
        ("/learning/courses/{course_id}/calendar/{item_id}", "PATCH", "learning:participate"),
        ("/learning/courses/{course_id}/calendar/{item_id}", "DELETE", "learning:participate"),
        ("/learning/courses/{course_id}/members/{member_user_id}", "DELETE", "learning:manage"),
        ("/learning/courses/{course_id}/assets", "POST", "learning:manage"),
        ("/learning/courses/{course_id}/assets/{asset_id}", "PATCH", "learning:manage"),
        ("/learning/courses/{course_id}/assets/{asset_id}", "DELETE", "learning:manage"),
        ("/learning/courses/{course_id}/tutor/query/stream", "POST", "learning:participate"),
        ("/learning/courses/{course_id}/artifacts", "GET", "learning:read"),
        ("/learning/courses/{course_id}/artifacts", "POST", "learning:participate"),
        ("/learning/courses/{course_id}/artifacts/{artifact_id}", "DELETE", "learning:participate"),
        ("/learning/courses/{course_id}/artifacts/{artifact_id}/attempts", "POST", "learning:participate"),
        ("/learning/courses/{course_id}/progress", "GET", "learning:read"),
        ("/learning/courses/{course_id}/lessons/{lesson_id}/progress", "PUT", "learning:participate"),
        ("/learning/courses/{course_id}/mastery", "GET", "learning:read"),
        ("/learning/courses/{course_id}/questions", "POST", "learning:participate"),
        ("/learning/courses/{course_id}/questions/{question_id}", "PATCH", "learning:participate"),
    ],
)
def test_public_learning_routes_have_narrow_oauth_scopes(path, method, scope):
    assert route_scope(path, method) == scope


@pytest.mark.parametrize(
    ("method", "path", "granted", "expected"),
    [
        ("GET", "/api/v1/learning/courses", ("learning:read",), "learning:read"),
        ("POST", "/api/v1/learning/courses", ("learning:manage",), "learning:manage"),
        ("POST", "/api/v1/learning/courses/c1/tutor/query/stream", ("learning:participate",), "learning:participate"),
        ("POST", "/api/v1/learning/courses/c1/artifacts", ("learning:participate",), "learning:participate"),
    ],
)
def test_learning_usage_is_attributed_to_the_effective_scope(method, path, granted, expected):
    assert _scope_for_request(principal(*granted), method, path) == expected


def test_public_learning_openapi_exposes_every_operation():
    from main import app

    operations = [
        operation
        for path, path_item in app.openapi()["paths"].items()
        if path.startswith("/api/v1/learning/")
        for operation in path_item
        if operation in {"get", "post", "put", "patch", "delete"}
    ]
    assert len(operations) == 39


@pytest.mark.anyio
async def test_course_operations_reject_a_different_selected_workspace():
    class Db:
        async def fetchval(self, _sql, _course_id):
            return "workspace-2"

    with pytest.raises(HTTPException) as exc:
        await public_learning_api.api_get_learning_course(
            request("workspace-1"), "course-1", principal("learning:read"), Db(),
        )
    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_learning_requires_an_explicit_workspace_context():
    with pytest.raises(HTTPException) as exc:
        await public_learning_api.api_list_learning_courses(
            request(None), principal("learning:read"), "workspace-1", db="db",
        )
    assert exc.value.status_code == 400


@pytest.mark.anyio
async def test_tutor_resolves_lesson_scope_before_grounded_chat(monkeypatch):
    captured = {}

    class Db:
        async def fetchval(self, _sql, _course_id):
            return "workspace-1"

    async def fake_scope(course_id, current_user, module_id, lesson_id, db):
        captured.update(
            course_id=course_id, user=current_user, module_id=module_id,
            lesson_id=lesson_id, scope_db=db,
        )
        return {
            "course_id": course_id,
            "instruction": "Learning scope: Module 1 / Lesson 2. Use only lesson evidence.",
            "document_ids": ["document-1"],
        }

    async def fake_chat(req, body, current_user, db):
        captured.update(request=req, body=body, chat_user=current_user, chat_db=db)
        return {"stream": "ok"}

    monkeypatch.setattr(learning, "resolve_learning_scope", fake_scope)
    monkeypatch.setattr(public_learning_api, "chat_stream_endpoint", fake_chat)

    body = public_learning_api.LearningTutorRequest(
        question="What is hybrid retrieval?", module_id="module-1", lesson_id="lesson-2",
    )
    result = await public_learning_api.api_ask_learning_tutor(
        request(), "course-1", body, principal("learning:participate"), Db(),
    )

    assert result == {"stream": "ok"}
    assert captured["body"].document_ids == ["document-1"]
    assert captured["body"].workspace_id == "workspace-1"
    assert "Use only lesson evidence" in captured["body"].question
    assert captured["body"].question.endswith("STUDENT QUESTION:\nWhat is hybrid retrieval?")


@pytest.mark.anyio
async def test_tutor_rejects_scope_without_embedded_content(monkeypatch):
    class Db:
        async def fetchval(self, _sql, _course_id):
            return "workspace-1"

    async def empty_scope(*_args, **_kwargs):
        return {"course_id": "course-1", "instruction": "Lesson scope", "document_ids": []}

    monkeypatch.setattr(learning, "resolve_learning_scope", empty_scope)
    with pytest.raises(HTTPException) as exc:
        await public_learning_api.api_ask_learning_tutor(
            request(), "course-1",
            public_learning_api.LearningTutorRequest(question="Explain this lesson"),
            principal("learning:participate"), Db(),
        )
    assert exc.value.status_code == 409


@pytest.mark.anyio
async def test_tutor_rejects_scope_resolved_for_another_course(monkeypatch):
    class Db:
        async def fetchval(self, _sql, _course_id):
            return "workspace-1"

    async def stale_scope(*_args, **_kwargs):
        return {
            "course_id": "course-ai-101", "instruction": "Stale scope",
            "document_ids": ["document-ai-101"],
        }

    monkeypatch.setattr(learning, "resolve_learning_scope", stale_scope)
    with pytest.raises(HTTPException) as exc:
        await public_learning_api.api_ask_learning_tutor(
            request(), "course-ai-201",
            public_learning_api.LearningTutorRequest(question="Explain this lesson"),
            principal("learning:participate"), Db(),
        )
    assert exc.value.status_code == 409
    assert "selected course" in str(exc.value.detail)
