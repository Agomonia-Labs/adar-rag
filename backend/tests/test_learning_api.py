from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from routes.learning import (
    AssetCreate,
    ArtifactCreate,
    CourseCreate,
    PracticeQuiz,
    _grade_practice_quiz,
    _normalize_artifact_content,
    _clean_list,
    _course_access,
    _course_response,
    _resolve_learning_scope,
)


class FakeDb:
    def __init__(self, row):
        self.row = row
        self.calls = []

    async def fetchrow(self, query, *args):
        self.calls.append((query, args))
        return self.row


class ScopeDb:
    def __init__(self):
        self.fetch_query = ""
        self.fetch_args = ()

    async def fetchrow(self, query, *args):
        if "FROM learning_courses" in query:
            return {
                "id": "course-1",
                "title": "AI Systems",
                "workspace_id": "workspace-1",
                "workspace_role": "viewer",
                "persona": "student",
            }
        if "FROM learning_lessons" in query:
            return {
                "id": "lesson-1",
                "title": "Hybrid Retrieval",
                "description": "Combining keyword and vector evidence",
                "module_id": "module-1",
                "module_title": "RAG",
            }
        if "FROM learning_modules" in query:
            return {"id": "module-1", "title": "RAG", "description": "Retrieval and grounding"}
        return None

    async def fetch(self, query, *args):
        self.fetch_query = query
        self.fetch_args = args
        return [{"document_id": "document-1", "status": "embedded"}]


@pytest.mark.asyncio
async def test_enrolled_student_can_open_course_but_cannot_manage_it():
    db = FakeDb({
        "id": "course-1",
        "workspace_role": "viewer",
        "persona": "student",
    })

    course = await _course_access(db, "course-1", "user-1")
    assert course["persona"] == "student"

    with pytest.raises(HTTPException) as exc:
        await _course_access(db, "course-1", "user-1", manage=True)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_workspace_editor_can_manage_course_without_explicit_enrollment():
    db = FakeDb({
        "id": "course-1",
        "workspace_role": "editor",
        "persona": None,
    })

    course = await _course_access(db, "course-1", "user-1", manage=True)
    assert course["workspace_role"] == "editor"


@pytest.mark.asyncio
async def test_non_member_cannot_discover_course():
    db = FakeDb(None)
    with pytest.raises(HTTPException) as exc:
        await _course_access(db, "course-1", "user-1")
    assert exc.value.status_code == 404


def test_course_contract_normalizes_objectives():
    course = _course_response({"id": "course-1", "objectives": '["Explain RAG", "Cite evidence"]'})
    assert course["objectives"] == ["Explain RAG", "Cite evidence"]
    assert _clean_list(["  First  ", "", "Second"]) == ["First", "Second"]


def test_learning_request_contracts_cover_course_and_saved_study_material():
    course = CourseCreate(workspace_id="workspace-1", title="AI Systems", objectives=["Ground answers"])
    artifact = ArtifactCreate(
        artifact_type="practice_questions",
        content="1. What is retrieval?",
        source_document_ids=["document-1"],
    )
    assert course.title == "AI Systems"
    assert artifact.artifact_type == "practice_questions"


def test_learning_asset_contract_supports_curriculum_mapping():
    asset = AssetCreate(document_id="document-1", module_id="module-1", lesson_id="lesson-1")
    assert asset.module_id == "module-1"
    assert asset.lesson_id == "lesson-1"


@pytest.mark.asyncio
async def test_lesson_scope_inherits_parent_content_but_excludes_sibling_lessons():
    db = ScopeDb()

    scope = await _resolve_learning_scope(
        db, "course-1", "user-1", module_id="module-1", lesson_id="lesson-1",
    )

    assert "WHEN $3::uuid IS NOT NULL" in db.fetch_query
    assert "WHEN $2::uuid IS NOT NULL" in db.fetch_query
    assert "a.module_id IS NULL" in db.fetch_query
    assert "a.module_id=$2::uuid" in db.fetch_query
    assert "a.lesson_id IS NULL OR a.lesson_id=$3::uuid" in db.fetch_query
    assert scope["scope_type"] == "lesson"
    assert scope["document_ids"] == ["document-1"]
    assert "exclude content attached to other lessons and modules" in scope["instruction"]
    assert "Combining keyword and vector evidence" in scope["instruction"]
    assert "Do not summarize the entire module" in scope["instruction"]


@pytest.mark.asyncio
async def test_course_scope_keeps_stable_three_argument_query_signature():
    db = ScopeDb()

    scope = await _resolve_learning_scope(db, "course-1", "user-1")

    assert db.fetch_args == ("course-1", None, None)
    assert "$1::uuid" in db.fetch_query
    assert "$2::uuid" in db.fetch_query
    assert "$3::uuid" in db.fetch_query
    assert scope["scope_type"] == "course"


def _practice_quiz(*, option_count=4, correct_ids=("A", "C")):
    option_ids = ("A", "B", "C", "D")[:option_count]
    return json.dumps({
        "schema_version": 1,
        "instructions": "Select every correct answer.",
        "questions": [{
            "id": "q1",
            "question": "Which statements are supported by the course material?",
            "options": [
                {"id": option_id, "text": f"Option {option_id}", "correct": option_id in correct_ids}
                for option_id in option_ids
            ],
            "explanation": "The course evidence supports options A and C.",
        }],
    })


def test_practice_quiz_accepts_multiple_correct_answers():
    normalized = json.loads(_normalize_artifact_content("practice_questions", _practice_quiz()))

    assert len(normalized["questions"][0]["options"]) == 4
    assert [option["id"] for option in normalized["questions"][0]["options"] if option["correct"]] == ["A", "C"]


@pytest.mark.parametrize("content", [
    _practice_quiz(option_count=3),
    _practice_quiz(correct_ids=()),
])
def test_practice_quiz_rejects_ungradable_questions(content):
    with pytest.raises(HTTPException) as exc:
        _normalize_artifact_content("practice_questions", content)

    assert exc.value.status_code == 422


def test_non_quiz_artifact_content_remains_plain_text():
    assert _normalize_artifact_content("study_guide", "  Grounded guide  ") == "Grounded guide"


def test_practice_quiz_grading_requires_exact_multi_select_answer():
    quiz = PracticeQuiz.model_validate_json(_practice_quiz())

    correct = _grade_practice_quiz(quiz, {"q1": ["C", "A"]})
    partial = _grade_practice_quiz(quiz, {"q1": ["A"]})

    assert correct["correct_count"] == 1
    assert correct["completed"] is True
    assert correct["result"]["q1"]["correct"] == ["A", "C"]
    assert partial["correct_count"] == 0
    assert partial["result"]["q1"]["is_correct"] is False


def test_practice_quiz_grading_rejects_unknown_question():
    quiz = PracticeQuiz.model_validate_json(_practice_quiz())
    with pytest.raises(HTTPException) as exc:
        _grade_practice_quiz(quiz, {"q99": ["A"]})
    assert exc.value.status_code == 400
