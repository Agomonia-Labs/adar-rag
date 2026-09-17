from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from routes.learning import (
    AssetCreate,
    AssetMappingUpdate,
    AssignmentCreate,
    ArtifactCreate,
    CourseCreate,
    LessonProgressUpdate,
    PracticeQuiz,
    _build_mastery_projection,
    _build_instructor_dashboard,
    _can_run_submission_evaluation,
    _grade_practice_quiz,
    _generate_submission_evaluation,
    _normalize_artifact_content,
    _normalize_submission_evaluation,
    _clean_list,
    _course_access,
    _course_response,
    _resolve_learning_scope,
    _serialize_course_members,
    _scope_evidence_ranges,
    evaluate_assignment_submission,
    update_asset_mapping,
    update_lesson_progress,
)


def test_course_directory_hides_private_fields_and_opted_out_classmates_from_students():
    members = [
        {
            "id": "member-1", "user_id": "student-1", "persona": "student",
            "email": "student@example.com", "full_name": "Current Student",
            "headline": "RAG learner", "bio": "Learning retrieval", "skills": '["Python"]',
            "interests": '["AI"]', "city": "Seattle", "region": "Washington",
            "country": "United States", "timezone": "America/Los_Angeles",
            "directory_visible": True, "profile_updated_at": None, "created_at": None,
        },
        {
            "id": "member-2", "user_id": "student-2", "persona": "student",
            "email": "classmate@example.com", "full_name": "Visible Classmate",
            "headline": "ML learner", "bio": "Learning grounding", "skills": '[]',
            "interests": '[]', "city": "Portland", "region": "Oregon",
            "country": "United States", "timezone": "America/Los_Angeles",
            "directory_visible": True, "profile_updated_at": None, "created_at": None,
        },
        {
            "id": "member-3", "user_id": "student-3", "persona": "student",
            "email": "private@example.com", "full_name": "Private Student",
            "headline": "", "bio": "", "skills": '[]', "interests": '[]',
            "city": "", "region": "", "country": "", "timezone": "",
            "directory_visible": False, "profile_updated_at": None, "created_at": None,
        },
    ]

    directory, own = _serialize_course_members(members, "student-1", can_manage=False)

    assert [item["user_id"] for item in directory] == ["student-1", "student-2"]
    assert own["email"] == "student@example.com"
    assert "email" not in directory[1]
    assert directory[1]["city"] == "Portland"
    assert directory[0]["skills"] == ["Python"]


def test_course_directory_gives_teachers_the_complete_roster():
    members = [{
        "id": "member-1", "user_id": "student-1", "persona": "student",
        "email": "private@example.com", "full_name": "Private Student",
        "headline": "", "bio": "", "skills": '[]', "interests": '[]',
        "city": "", "region": "", "country": "", "timezone": "",
        "directory_visible": False, "profile_updated_at": None, "created_at": None,
    }]

    directory, own = _serialize_course_members(members, "teacher-1", can_manage=True)

    assert own is None
    assert directory[0]["email"] == "private@example.com"
    assert directory[0]["directory_visible"] is False


def test_submission_evaluation_derives_score_from_criteria_and_normalizes_evidence():
    assignment = {
        "max_score": 100,
        "rubric": [
            {"id": "accuracy", "title": "Accuracy", "weight": 60},
            {"id": "evidence", "title": "Evidence", "weight": 40},
        ],
    }
    result = _normalize_submission_evaluation({
        "summary": "Grounded work",
        "criteria": [
            {"criterion_id": "accuracy", "score": 54, "feedback": "Accurate", "evidence": "Submission paragraph 1"},
            {"criterion_id": "evidence", "score": 32, "feedback": "Supported", "evidence": ["Document chunk 2"]},
        ],
    }, assignment)

    assert result["overall_score"] == 86
    assert result["criteria"][0]["max_score"] == 60
    assert result["criteria"][0]["evidence"] == ["Submission paragraph 1"]
    assert result["status"] == "completed"


def test_failed_submission_evaluation_can_be_retried_from_in_review():
    assert _can_run_submission_evaluation({
        "status": "in_review",
        "ai_evaluation": {"status": "needs_human_review"},
    })
    assert not _can_run_submission_evaluation({
        "status": "in_review",
        "ai_evaluation": {"status": "completed"},
    })


@pytest.mark.asyncio
async def test_submission_evaluation_retries_structured_generation(monkeypatch):
    structured = AsyncMock(side_effect=[
        ValueError("temporary malformed response"),
        {
            "overall_score": 91,
            "summary": "Strong evidence-backed submission",
            "strengths": ["Clear retrieval explanation"],
            "improvements": [],
            "criteria": [{
                "criterion_id": "quality", "score": 91, "max_score": 100,
                "feedback": "Meets the rubric", "evidence": ["Submission response"],
            }],
        },
    ])
    monkeypatch.setattr("services.llm.chat_json", structured)

    result = await _generate_submission_evaluation(
        {"id": "assignment-1", "max_score": 100, "rubric": [{"id": "quality", "weight": 100}]},
        {"id": "submission-1", "submission_text": "Grounded response"},
        "Retrieved evidence",
    )

    assert structured.await_count == 2
    assert result["status"] == "completed"
    assert result["overall_score"] == 91


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
    course = _course_response({"id": "course-1", "objectives": '["Explain RAG", "Cite evidence"]', "domain": "sports", "domain_config": '{"passing_score": 85}'})
    assert course["objectives"] == ["Explain RAG", "Cite evidence"]
    assert course["domain_pack"]["label"] == "Sports and coaching"
    assert course["domain_pack"]["passing_score"] == 85
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


def test_assignment_contract_requires_substantive_rubric_fields():
    assignment = AssignmentCreate(
        title="Grounded project", assignment_type="project",
        rubric=[{"id": "evidence", "title": "Evidence", "weight": 100}],
        publication_status="published",
    )
    assert assignment.rubric[0].id == "evidence"
    assert assignment.rubric[0].weight == 100


def test_learning_asset_contract_supports_curriculum_mapping():
    asset = AssetCreate(document_id="document-1", module_id="module-1", lesson_id="lesson-1", start_seconds=60, end_seconds=180)
    assert asset.module_id == "module-1"
    assert asset.lesson_id == "lesson-1"
    assert asset.end_seconds == 180


def test_learning_asset_update_contract_supports_replacement_mapping():
    mapping = AssetMappingUpdate(
        module_id="module-2", lesson_id="lesson-3", start_seconds=180, end_seconds=300,
    )
    assert mapping.module_id == "module-2"
    assert mapping.lesson_id == "lesson-3"
    assert mapping.start_seconds == 180
    assert mapping.end_seconds == 300


@pytest.mark.asyncio
async def test_update_asset_mapping_replaces_existing_row(monkeypatch):
    db = AsyncMock()
    db.fetchrow.return_value = {
        "id": "asset-1", "document_id": "document-1", "original_name": "lesson.mp4",
    }
    db.fetchval.return_value = None
    monkeypatch.setattr(
        "routes.learning._course_access",
        AsyncMock(return_value={"workspace_id": "workspace-1"}),
    )
    monkeypatch.setattr("routes.learning.emit_event", AsyncMock())
    monkeypatch.setattr(
        "routes.learning._course_workspace",
        AsyncMock(return_value={"id": "course-1", "assets": []}),
    )

    result = await update_asset_mapping(
        "course-1", "asset-1",
        AssetMappingUpdate(start_seconds=180, end_seconds=300),
        {"id": "user-1"}, db,
    )

    assert result["id"] == "course-1"
    update_call = next(call for call in db.execute.await_args_list if "UPDATE learning_assets" in call.args[0])
    assert update_call.args[1:3] == ("asset-1", "course-1")
    assert update_call.args[-2:] == (180.0, 300.0)


@pytest.mark.asyncio
async def test_update_lesson_progress_persists_course_scoped_state(monkeypatch):
    db = AsyncMock()
    db.fetchrow.side_effect = [
        {"id": "lesson-1", "module_id": "module-1"},
        {
            "id": "progress-1", "course_id": "course-1", "module_id": "module-1",
            "lesson_id": "lesson-1", "user_id": "user-1", "status": "in_progress",
            "progress_pct": 45, "time_spent_seconds": 300, "last_position_seconds": 120,
        },
    ]
    monkeypatch.setattr(
        "routes.learning._course_access",
        AsyncMock(return_value={"workspace_id": "workspace-1"}),
    )
    event = AsyncMock()
    monkeypatch.setattr("routes.learning.emit_event", event)

    result = await update_lesson_progress(
        "course-1", "lesson-1",
        LessonProgressUpdate(
            status="in_progress", progress_pct=45,
            time_spent_seconds=300, last_position_seconds=120,
        ),
        {"id": "user-1"}, db,
    )

    assert result["progress_pct"] == 45
    assert result["last_position_seconds"] == 120
    assert event.await_args.kwargs["event_type"] == "learning.progress.updated"


def test_learning_asset_requires_complete_valid_time_range():
    with pytest.raises(ValueError):
        AssetCreate(document_id="document-1", start_seconds=60)
    with pytest.raises(ValueError):
        AssetCreate(document_id="document-1", start_seconds=60, end_seconds=30)
    with pytest.raises(ValueError):
        AssetMappingUpdate(start_seconds=60)


def test_scope_ranges_preserve_full_assets_and_group_reused_media():
    rows = [
        {"document_id": "video-1", "start_seconds": 60, "end_seconds": 120},
        {"document_id": "video-1", "start_seconds": 180, "end_seconds": 240},
        {"document_id": "document-1", "start_seconds": None, "end_seconds": None},
    ]
    assert _scope_evidence_ranges(rows) == [{
        "document_id": "video-1",
        "ranges": [
            {"start_seconds": 60.0, "end_seconds": 120.0},
            {"start_seconds": 180.0, "end_seconds": 240.0},
        ],
    }]


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


def test_lesson_progress_contract_normalizes_terminal_states():
    assert LessonProgressUpdate(status="completed", progress_pct=40).progress_pct == 100
    reset = LessonProgressUpdate(status="not_started", progress_pct=80, last_position_seconds=30)
    assert reset.progress_pct == 0
    assert reset.last_position_seconds is None
    assert LessonProgressUpdate(status="in_progress", progress_pct=100).status == "completed"


def test_mastery_projection_separates_completion_from_assessment_evidence():
    course = {"id": "course-1", "domain_config": {"passing_score": 80}}
    modules = [{"id": "module-1", "title": "Foundations", "position": 0}]
    lessons = [
        {"id": "lesson-1", "module_id": "module-1", "title": "Retrieval", "position": 0,
         "objectives": ["Explain retrieval"], "competencies": ["Hybrid retrieval"]},
        {"id": "lesson-2", "module_id": "module-1", "title": "Evaluation", "position": 1,
         "objectives": ["Evaluate evidence"], "competencies": ["Groundedness"]},
    ]
    progress = [
        {"lesson_id": "lesson-1", "status": "completed", "progress_pct": 100},
        {"lesson_id": "lesson-2", "status": "in_progress", "progress_pct": 25},
    ]
    attempts = [
        {"artifact_id": "quiz-1", "lesson_id": "lesson-1", "correct_count": 4,
         "question_count": 5, "completed": True, "artifact_title": "Retrieval quiz"},
        {"artifact_id": "quiz-2", "lesson_id": "lesson-2", "correct_count": 1,
         "question_count": 4, "completed": True, "artifact_title": "Evaluation quiz"},
    ]

    result = _build_mastery_projection(course, modules, lessons, progress, attempts, {"id": "user-1"})

    assert result["summary"] == {
        "lesson_count": 2, "completed_lessons": 1, "completion_pct": 50,
        "progress_pct": 62, "assessed_lessons": 2, "mastered_lessons": 1,
        "mastery_pct": 56,
    }
    projected = result["modules"][0]["lessons"]
    assert projected[0]["mastery_status"] == "mastered"
    assert projected[0]["competencies"][0]["score"] == 80
    assert projected[1]["mastery_status"] == "developing"
    assert result["recommendations"][0]["type"] == "review_and_retake"


def test_completed_lesson_without_quiz_is_not_reported_as_mastered():
    result = _build_mastery_projection(
        {"id": "course-1", "domain_config": {}},
        [{"id": "module-1", "title": "Module"}],
        [{"id": "lesson-1", "module_id": "module-1", "title": "Lesson", "competencies": []}],
        [{"lesson_id": "lesson-1", "status": "completed", "progress_pct": 100}],
        [], {"id": "user-1"},
    )

    assert result["summary"]["completion_pct"] == 100
    assert result["summary"]["mastery_pct"] is None
    assert result["modules"][0]["lessons"][0]["mastery_status"] == "not_assessed"
    assert result["recommendations"][0]["type"] == "assess_mastery"


def test_incomplete_or_reset_attempt_does_not_reduce_mastery():
    result = _build_mastery_projection(
        {"id": "course-1", "domain_config": {"passing_score": 80}},
        [{"id": "module-1", "title": "Module"}],
        [{"id": "lesson-1", "module_id": "module-1", "title": "Lesson", "competencies": []}],
        [{"lesson_id": "lesson-1", "status": "in_progress", "progress_pct": 40}],
        [{
            "artifact_id": "quiz-1", "lesson_id": "lesson-1", "correct_count": 0,
            "question_count": 4, "completed": False, "result": {},
        }],
        {"id": "user-1"},
    )

    assert result["summary"]["assessed_lessons"] == 0
    assert result["summary"]["mastery_pct"] is None
    assert result["modules"][0]["lessons"][0]["assessment_evidence"] == []


def test_instructor_dashboard_surfaces_risk_difficulty_questions_and_content_gaps():
    course = {"id": "course-1", "domain_config": {"passing_score": 80}, "assets": []}
    members = [{"user_id": "student-1", "email": "student@example.com", "persona": "student"}]
    lessons = [{"id": "lesson-1", "title": "Retrieval", "competencies": ["Grounding"]}]
    progress = [{"user_id": "student-1", "lesson_id": "lesson-1", "progress_pct": 25}]
    attempts = [{"user_id": "student-1", "lesson_id": "lesson-1", "correct_count": 1, "question_count": 4}]
    questions = [{"id": "question-1", "status": "open", "question": "Why rerank?"}]

    result = _build_instructor_dashboard(course, members, lessons, progress, attempts, [], [], questions)

    assert result["summary"]["at_risk_count"] == 1
    assert result["summary"]["assessment_performance_pct"] == 25
    assert result["difficult_concepts"][0]["title"] == "Retrieval"
    assert result["content_quality_gaps"][0]["gap"] == "No embedded lesson evidence"
    assert result["unanswered_questions"][0]["id"] == "question-1"


@pytest.mark.asyncio
async def test_ai_rubric_persists_result_and_moves_submission_to_in_review(monkeypatch):
    db = AsyncMock()
    db.fetchrow.side_effect = [
        {
            "id": "assignment-1", "title": "Grounded project", "description": "Use evidence",
            "max_score": 100, "rubric": '[{"id":"evidence","title":"Evidence","weight":100}]',
            "source_document_ids": "[]",
        },
        {
            "id": "submission-1", "assignment_id": "assignment-1", "status": "submitted",
            "submission_text": "Hybrid retrieval combines lexical and semantic evidence.",
            "document_ids": '["document-1"]', "presentation_document_id": None,
        },
        {
            "id": "submission-1", "assignment_id": "assignment-1", "status": "in_review",
            "submission_text": "Hybrid retrieval combines lexical and semantic evidence.",
            "document_ids": '["document-1"]', "presentation_document_id": None,
            "ai_evaluation": '{"status":"completed","overall_score":88,"summary":"Grounded response","criteria":[]}',
        },
    ]
    db.fetch.return_value = [{
        "original_name": "RAG_Architecture_Notes.docx", "chunk_index": 0,
        "content": "Hybrid retrieval combines keyword and vector retrieval.",
    }]
    monkeypatch.setattr("routes.learning._course_access", AsyncMock(return_value={"workspace_id": "workspace-1"}))
    monkeypatch.setattr(
        "routes.learning._generate_submission_evaluation",
        AsyncMock(return_value={
            "status": "completed", "overall_score": 88, "summary": "Grounded response",
            "criteria": [], "strengths": [], "improvements": [], "requires_human_review": True,
        }),
    )

    result = await evaluate_assignment_submission(
        "course-1", "assignment-1", "submission-1", {"id": "teacher-1"}, db,
    )

    assert result["status"] == "in_review"
    assert result["ai_evaluation"]["overall_score"] == 88
    update = db.fetchrow.await_args_list[-1]
    assert "status='in_review'" in update.args[0]
