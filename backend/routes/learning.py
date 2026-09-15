from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from auth.dependencies import CurrentUser
from database.connection import get_db
from routes.workspaces import ROLE_ORDER, _require_role
from services.audit import audit, ip_from, ua_from
from services.notifications import send_learning_question_notification
from services.mcp_enterprise import emit_event
import services.storage as gcs

router = APIRouter()
logger = logging.getLogger(__name__)

PERSONAS = {"admin", "teacher", "student", "advisor"}
MANAGER_PERSONAS = {"admin", "teacher"}
ARTIFACT_TYPES = {"summary", "study_guide", "key_concepts", "flashcards", "practice_questions"}
DOMAIN_PACKS = {
    "general": {"label": "General learning", "reviewer_persona": "teacher", "tutor_focus": "Explain the approved course evidence clearly."},
    "healthcare": {"label": "Healthcare education", "reviewer_persona": "teacher", "tutor_focus": "Use clinical terminology carefully and distinguish education from medical advice."},
    "financial_services": {"label": "Financial services", "reviewer_persona": "advisor", "tutor_focus": "Explain financial and compliance concepts without presenting personalized financial advice."},
    "manufacturing": {"label": "Manufacturing and safety", "reviewer_persona": "teacher", "tutor_focus": "Emphasize procedures, controls, hazards, and evidence-backed safety steps."},
    "construction": {"label": "Construction and field service", "reviewer_persona": "teacher", "tutor_focus": "Emphasize site procedures, inspections, safety, and corrective actions."},
    "sports": {"label": "Sports and coaching", "reviewer_persona": "teacher", "tutor_focus": "Connect instruction to timestamped plays, technique, and coaching evidence."},
    "legal_compliance": {"label": "Legal and compliance", "reviewer_persona": "advisor", "tutor_focus": "Preserve policy language, effective context, and evidence citations; do not provide legal advice."},
    "enterprise_training": {"label": "Enterprise training", "reviewer_persona": "teacher", "tutor_focus": "Focus on role readiness, procedures, decisions, and operational examples."},
    "customer_education": {"label": "Customer education", "reviewer_persona": "advisor", "tutor_focus": "Use accessible product language and approved customer-facing evidence."},
    "government": {"label": "Government and public sector", "reviewer_persona": "advisor", "tutor_focus": "Preserve policy, accessibility, accountability, and public-sector context."},
    "cultural_arts": {"label": "Cultural arts", "reviewer_persona": "teacher", "tutor_focus": "Explain performance, history, narration, movement, and timestamped visual evidence respectfully."},
}
LearningDomain = Literal[
    "general", "healthcare", "financial_services", "manufacturing", "construction", "sports",
    "legal_compliance", "enterprise_training", "customer_education", "government", "cultural_arts",
]


class CourseCreate(BaseModel):
    workspace_id: str
    title: str = Field(min_length=1, max_length=180)
    course_code: str = Field(default="", max_length=60)
    semester: str = Field(default="", max_length=80)
    description: str = Field(default="", max_length=4000)
    instructor_name: str = Field(default="", max_length=180)
    objectives: list[str] = Field(default_factory=list)
    domain: LearningDomain = "general"
    domain_config: dict[str, Any] = Field(default_factory=dict)
    publication_status: Literal["draft", "published"] = "draft"


class CourseUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=180)
    course_code: str | None = Field(default=None, max_length=60)
    semester: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=4000)
    instructor_name: str | None = Field(default=None, max_length=180)
    objectives: list[str] | None = None
    status: Literal["active", "archived"] | None = None
    domain: LearningDomain | None = None
    domain_config: dict[str, Any] | None = None
    publication_status: Literal["draft", "published"] | None = None


class MemberCreate(BaseModel):
    email: str
    persona: Literal["admin", "teacher", "student", "advisor"] = "student"


class CurriculumSave(BaseModel):
    modules: list[dict] = Field(default_factory=list)


class AssetCreate(BaseModel):
    asset_id: str | None = None
    document_id: str
    module_id: str | None = None
    lesson_id: str | None = None
    title: str = ""
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_time_range(self):
        if (self.start_seconds is None) != (self.end_seconds is None):
            raise ValueError("Both start_seconds and end_seconds are required for a time-bounded mapping")
        if self.start_seconds is not None and self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must be greater than start_seconds")
        return self


class AssetMappingUpdate(BaseModel):
    module_id: str | None = None
    lesson_id: str | None = None
    title: str = ""
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_time_range(self):
        if (self.start_seconds is None) != (self.end_seconds is None):
            raise ValueError("Both start_seconds and end_seconds are required for a time-bounded mapping")
        if self.start_seconds is not None and self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must be greater than start_seconds")
        return self


class ArtifactCreate(BaseModel):
    artifact_type: Literal["summary", "study_guide", "key_concepts", "flashcards", "practice_questions"]
    title: str = ""
    content: str = Field(min_length=1)
    source_document_ids: list[str] = Field(default_factory=list)
    module_id: str | None = None
    lesson_id: str | None = None


class QuizAttemptCreate(BaseModel):
    answers: dict[str, list[str]] = Field(default_factory=dict)
    replace: bool = False


class LessonProgressUpdate(BaseModel):
    status: Literal["not_started", "in_progress", "completed"] = "in_progress"
    progress_pct: int = Field(default=0, ge=0, le=100)
    time_spent_seconds: int = Field(default=0, ge=0)
    last_position_seconds: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def normalize_status(self):
        if self.status == "completed":
            self.progress_pct = 100
        elif self.status == "not_started":
            self.progress_pct = 0
            self.last_position_seconds = None
        elif self.progress_pct == 100:
            self.status = "completed"
        return self


class PracticeOption(BaseModel):
    id: str = Field(min_length=1, max_length=12)
    text: str = Field(min_length=1, max_length=1000)
    correct: bool = False


class PracticeQuestion(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    question: str = Field(min_length=1, max_length=4000)
    options: list[PracticeOption] = Field(min_length=4, max_length=4)
    explanation: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def validate_options(self):
        option_ids = [option.id for option in self.options]
        if set(option_ids) != {"A", "B", "C", "D"}:
            raise ValueError("Practice-question options must be labeled A, B, C, and D")
        if not any(option.correct for option in self.options):
            raise ValueError("A practice question must have at least one correct answer")
        return self


class PracticeQuiz(BaseModel):
    schema_version: Literal[1] = 1
    instructions: str = Field(default="Select every correct answer.", max_length=1000)
    questions: list[PracticeQuestion] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_questions(self):
        question_ids = [question.id for question in self.questions]
        if len(set(question_ids)) != len(question_ids):
            raise ValueError("Practice-question IDs must be unique")
        return self


def _normalize_artifact_content(artifact_type: str, content: str) -> str:
    cleaned = content.strip()
    if artifact_type != "practice_questions":
        return cleaned
    try:
        quiz = PracticeQuiz.model_validate_json(cleaned)
    except ValueError as exc:
        raise HTTPException(422, f"Practice questions must use the interactive quiz format: {exc}") from exc
    return json.dumps(quiz.model_dump(), ensure_ascii=False)


class QuestionCreate(BaseModel):
    target_role: Literal["teacher", "advisor"] = "teacher"
    question: str = Field(min_length=1, max_length=8000)
    context: dict = Field(default_factory=dict)


class QuestionUpdate(BaseModel):
    answer: str | None = None
    status: Literal["open", "answered", "closed"] | None = None
    assigned_to: str | None = None


class RubricCriterion(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=180)
    description: str = Field(default="", max_length=2000)
    weight: int = Field(default=1, ge=1, le=100)


class AssignmentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=12_000)
    assignment_type: Literal["written", "document", "presentation", "project"] = "written"
    module_id: str | None = None
    lesson_id: str | None = None
    rubric: list[RubricCriterion] = Field(default_factory=list, max_length=20)
    source_document_ids: list[str] = Field(default_factory=list, max_length=50)
    max_score: int = Field(default=100, gt=0, le=10_000)
    due_at: datetime | None = None
    publication_status: Literal["draft", "published", "closed"] = "draft"

    @model_validator(mode="after")
    def validate_rubric(self):
        if self.rubric:
            if len({item.id for item in self.rubric}) != len(self.rubric):
                raise ValueError("Rubric criterion IDs must be unique")
            if sum(item.weight for item in self.rubric) != 100:
                raise ValueError("Rubric criterion weights must total 100")
        return self


class AssignmentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    description: str | None = Field(default=None, max_length=12_000)
    assignment_type: Literal["written", "document", "presentation", "project"] | None = None
    module_id: str | None = None
    lesson_id: str | None = None
    rubric: list[RubricCriterion] | None = Field(default=None, max_length=20)
    source_document_ids: list[str] | None = Field(default=None, max_length=50)
    max_score: int | None = Field(default=None, gt=0, le=10_000)
    due_at: datetime | None = None
    publication_status: Literal["draft", "published", "closed"] | None = None

    @model_validator(mode="after")
    def validate_rubric(self):
        if self.rubric is not None and self.rubric:
            if len({item.id for item in self.rubric}) != len(self.rubric):
                raise ValueError("Rubric criterion IDs must be unique")
            if sum(item.weight for item in self.rubric) != 100:
                raise ValueError("Rubric criterion weights must total 100")
        return self


class SubmissionUpsert(BaseModel):
    submission_text: str = Field(default="", max_length=100_000)
    document_ids: list[str] = Field(default_factory=list, max_length=30)
    presentation_document_id: str | None = None
    submit: bool = False

    @model_validator(mode="after")
    def require_evidence(self):
        if self.submit and not (
            self.submission_text.strip() or self.document_ids or self.presentation_document_id
        ):
            raise ValueError("A submitted assignment requires written or uploaded evidence")
        return self


class SubmissionReview(BaseModel):
    status: Literal["in_review", "revision_requested", "approved"]
    instructor_feedback: str = Field(default="", max_length=30_000)
    score: float | None = Field(default=None, ge=0)


def _assignment_response(row) -> dict:
    result = _jsonable(dict(row))
    result["rubric"] = _decode_json(result.get("rubric"), [])
    result["source_document_ids"] = _decode_json(result.get("source_document_ids"), [])
    return result


def _submission_response(row) -> dict:
    result = _jsonable(dict(row))
    result["document_ids"] = _decode_json(result.get("document_ids"), [])
    result["ai_evaluation"] = _decode_json(result.get("ai_evaluation"), {})
    return result


async def _validate_assignment_scope(db, course_id: str, module_id: str | None, lesson_id: str | None):
    if lesson_id:
        row = await db.fetchrow(
            """SELECT l.module_id FROM learning_lessons l
               JOIN learning_modules m ON m.id=l.module_id
               WHERE l.id=$1::uuid AND m.course_id=$2::uuid""",
            lesson_id, course_id,
        )
        if not row:
            raise HTTPException(400, "Lesson does not belong to this course")
        if module_id and str(row["module_id"]) != str(module_id):
            raise HTTPException(400, "Lesson does not belong to the selected module")
        module_id = str(row["module_id"])
    elif module_id and not await db.fetchval(
        "SELECT 1 FROM learning_modules WHERE id=$1::uuid AND course_id=$2::uuid", module_id, course_id,
    ):
        raise HTTPException(400, "Module does not belong to this course")
    return module_id, lesson_id


async def _validate_course_documents(db, workspace_id: str, document_ids: list[str]) -> None:
    unique_ids = list(dict.fromkeys(str(value) for value in document_ids if value))
    if not unique_ids:
        return
    rows = await db.fetch(
        """SELECT id FROM documents WHERE id=ANY($1::uuid[]) AND workspace_id=$2::uuid
           AND status<>'deleted'""", unique_ids, workspace_id,
    )
    if {str(row["id"]) for row in rows} != set(unique_ids):
        raise HTTPException(400, "One or more documents are outside the course workspace")


def _extract_json_object(value: str) -> dict:
    text = str(value or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("AI evaluation did not contain a JSON object")
    parsed = json.loads(text[start:end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("AI evaluation must be a JSON object")
    return parsed


def _normalize_submission_evaluation(result: dict, assignment: dict) -> dict:
    maximum = float(assignment.get("max_score") or 100)
    rubric = assignment.get("rubric") or []
    rubric_by_id = {str(item.get("id")): item for item in rubric if isinstance(item, dict) and item.get("id")}
    raw_criteria = result.get("criteria") or []
    if isinstance(raw_criteria, dict):
        raw_criteria = [dict(value, criterion_id=key) if isinstance(value, dict) else {"criterion_id": key, "feedback": str(value)}
                        for key, value in raw_criteria.items()]
    if not isinstance(raw_criteria, list):
        raise ValueError("AI evaluation criteria must be a list")

    criteria = []
    for index, raw in enumerate(raw_criteria):
        if not isinstance(raw, dict):
            continue
        fallback = rubric[index] if index < len(rubric) and isinstance(rubric[index], dict) else {}
        criterion_id = str(raw.get("criterion_id") or raw.get("id") or fallback.get("id") or f"criterion_{index + 1}")
        rubric_item = rubric_by_id.get(criterion_id, fallback)
        criterion_max = raw.get("max_score")
        if criterion_max is None and rubric_item:
            criterion_max = maximum * float(rubric_item.get("weight") or 0) / 100
        criterion_score = raw.get("score")
        if criterion_score is None or criterion_max is None:
            continue
        criterion_score, criterion_max = float(criterion_score), float(criterion_max)
        if criterion_score < 0 or criterion_max <= 0 or criterion_score > criterion_max:
            raise ValueError(f"AI score for criterion '{criterion_id}' is outside its allowed range")
        evidence = raw.get("evidence") or []
        criteria.append({
            "criterion_id": criterion_id,
            "score": criterion_score,
            "max_score": criterion_max,
            "feedback": str(raw.get("feedback") or "").strip(),
            "evidence": [str(item).strip() for item in evidence if str(item).strip()]
            if isinstance(evidence, list) else [str(evidence).strip()],
        })

    score = result.get("overall_score")
    if score is None and criteria:
        score = sum(item["score"] for item in criteria)
    if score is None:
        raise ValueError("AI evaluation did not provide an overall or criterion-derived score")
    score = float(score)
    if score < 0 or score > maximum:
        raise ValueError("AI score is outside the assignment score range")

    strengths = result.get("strengths") or []
    improvements = result.get("improvements") or []
    return {
        "status": "completed",
        "requires_human_review": True,
        "overall_score": round(score, 2),
        "summary": str(result.get("summary") or "AI rubric evaluation completed for instructor review.").strip(),
        "criteria": criteria,
        "strengths": _clean_list(strengths if isinstance(strengths, list) else [strengths]),
        "improvements": _clean_list(improvements if isinstance(improvements, list) else [improvements]),
    }


def _can_run_submission_evaluation(submission: dict) -> bool:
    if submission.get("status") == "submitted":
        return True
    evaluation = _decode_json(submission.get("ai_evaluation"), {})
    return submission.get("status") == "in_review" and evaluation.get("status") == "needs_human_review"


async def _generate_submission_evaluation(assignment: dict, submission: dict, evidence: str) -> dict:
    from services.llm import chat_json

    rubric = assignment.get("rubric") or []
    maximum = float(assignment.get("max_score") or 100)
    rubric_contract = []
    for item in rubric:
        if not isinstance(item, dict):
            continue
        rubric_contract.append({
            **item,
            "criterion_max_score": round(maximum * float(item.get("weight") or 0) / 100, 2),
        })
    system_prompt = (
        "You are an evidence-grounded learning evaluator. Evaluate only the supplied submission evidence. "
        "Do not infer missing work. Return JSON only, without Markdown or commentary. Use exactly this shape: "
        '{"overall_score":0,"summary":"...","strengths":["..."],"improvements":["..."],'
        '"criteria":[{"criterion_id":"...","score":0,"max_score":0,"feedback":"...","evidence":["..."]}]}. '
        "Use each supplied rubric ID exactly once and keep every score within its criterion maximum."
    )
    prompt = {
        "assignment": {"title": assignment.get("title"), "description": assignment.get("description"),
                       "max_score": assignment.get("max_score"), "rubric": rubric_contract},
        "submission_text": submission.get("submission_text") or "",
        "uploaded_evidence": evidence[:30_000],
    }
    last_error: Exception | None = None
    try:
        for _attempt in range(2):
            try:
                result = await chat_json(
                    [{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
                    system_prompt,
                )
                return _normalize_submission_evaluation(result, assignment)
            except Exception as exc:
                last_error = exc
        raise last_error or ValueError("AI rubric evaluation returned no result")
    except Exception as exc:
        logger.warning(
            "Learning rubric evaluation unavailable for assignment=%s submission=%s: %s",
            assignment.get("id"), submission.get("id"), exc,
        )
        return {
            "status": "needs_human_review", "requires_human_review": True,
            "overall_score": None,
            "summary": "Automated rubric evaluation was unavailable; instructor review is required.",
            "criteria": [], "strengths": [], "improvements": [],
            "error_code": "evaluation_unavailable",
            "error": f"{type(exc).__name__}: {str(exc)}"[:1000],
        }


async def _course_access(db, course_id: str, user_id: str, manage: bool = False):
    row = await db.fetchrow(
        """
        SELECT c.*, wm.role AS workspace_role, lcm.persona
        FROM learning_courses c
        JOIN workspace_members wm ON wm.workspace_id=c.workspace_id AND wm.user_id=$2::uuid
        LEFT JOIN learning_course_members lcm ON lcm.course_id=c.id AND lcm.user_id=$2::uuid
        WHERE c.id=$1::uuid
        """,
        course_id, user_id,
    )
    if not row:
        raise HTTPException(404, "Course not found or not accessible")
    data = dict(row)
    is_workspace_manager = ROLE_ORDER.get(data.get("workspace_role"), -1) >= ROLE_ORDER["editor"]
    if not data.get("persona") and not is_workspace_manager:
        raise HTTPException(403, "You are not enrolled in this course")
    if manage and not (is_workspace_manager or data.get("persona") in MANAGER_PERSONAS):
        raise HTTPException(403, "Course administration requires a teacher or admin role")
    return data


async def _resolve_learning_scope(
    db, course_id: str, user_id: str, module_id: str | None = None, lesson_id: str | None = None,
) -> dict:
    course = await _course_access(db, course_id, user_id)
    module = None
    lesson = None
    if lesson_id:
        lesson = await db.fetchrow(
            """SELECT l.id,l.title,l.description,l.objectives,l.competencies,l.module_id,m.title AS module_title
               FROM learning_lessons l JOIN learning_modules m ON m.id=l.module_id
               WHERE l.id=$1::uuid AND m.course_id=$2::uuid""",
            lesson_id, course_id,
        )
        if not lesson:
            raise HTTPException(400, "Lesson does not belong to this course")
        if module_id and str(lesson["module_id"]) != module_id:
            raise HTTPException(400, "Lesson does not belong to the selected module")
        module_id = str(lesson["module_id"])
    if module_id:
        module = await db.fetchrow(
            "SELECT id,title,description FROM learning_modules WHERE id=$1::uuid AND course_id=$2::uuid",
            module_id, course_id,
        )
        if not module:
            raise HTTPException(400, "Module does not belong to this course")

    rows = await db.fetch(
        """SELECT a.*,d.original_name,d.file_type,d.doc_type,d.status,d.chunk_count
           FROM learning_assets a JOIN documents d ON d.id=a.document_id
           WHERE a.course_id=$1::uuid AND d.status='embedded'
             AND CASE
                   WHEN $3::uuid IS NOT NULL THEN
                     a.module_id IS NULL OR (
                       a.module_id=$2::uuid AND (a.lesson_id IS NULL OR a.lesson_id=$3::uuid)
                     )
                   WHEN $2::uuid IS NOT NULL THEN
                     a.module_id IS NULL OR a.module_id=$2::uuid
                   ELSE TRUE
                 END
           ORDER BY a.position,a.created_at""",
        course_id, module_id, lesson_id,
    )
    scope_type = "lesson" if lesson else "module" if module else "course"
    label = str(course["title"])
    if module:
        label += f" / {module['title']}"
    if lesson:
        label += f" / {lesson['title']}"
    boundary = {
        "course": "Use all embedded content attached to this course.",
        "module": "Use course-wide content and content attached to this module; exclude other modules.",
        "lesson": "Use course-wide context, selected-module content, and content explicitly attached to this lesson; exclude content attached to other lessons and modules.",
    }[scope_type]
    focus = ""
    if lesson:
        objectives = ", ".join(_decode_json(lesson.get("objectives"), [])) or "Not specified"
        competencies = ", ".join(_decode_json(lesson.get("competencies"), [])) or "Not specified"
        focus = (
            f" Selected lesson title: {lesson['title']}."
            f" Selected lesson description: {lesson['description'] or 'Not provided'}."
            f" Learning objectives: {objectives}. Competencies: {competencies}."
            " Retrieve and answer only evidence semantically relevant to this lesson, even when an attached file also contains broader module content."
            " Do not summarize the entire module."
        )
    elif module:
        focus = (
            f" Selected module title: {module['title']}."
            f" Selected module description: {module['description'] or 'Not provided'}."
        )
    evidence_ranges = _scope_evidence_ranges(rows)
    range_instruction = _range_instruction(evidence_ranges)
    domain = str(course.get("domain") or "general")
    domain_config = _decode_json(course.get("domain_config"), {})
    domain_pack = DOMAIN_PACKS.get(domain, DOMAIN_PACKS["general"])
    tutor_focus = str(domain_config.get("tutor_focus") or domain_pack["tutor_focus"])
    return {
        "course_id": course_id,
        "workspace_id": str(course["workspace_id"]),
        "module_id": module_id,
        "lesson_id": lesson_id,
        "scope_type": scope_type,
        "label": label,
        "domain": domain,
        "domain_pack": {**domain_pack, **domain_config},
        "instruction": (
            f"Learning scope: {label}. Domain: {domain_pack['label']}. {boundary}{focus}"
            f" {range_instruction} Domain guidance: {tutor_focus}"
            " Ground every claim in the selected course evidence."
        ),
        "document_ids": list(dict.fromkeys(str(row["document_id"]) for row in rows)),
        "evidence_ranges": evidence_ranges,
        "assets": [_jsonable(dict(row)) for row in rows],
    }


def _scope_evidence_ranges(rows) -> list[dict]:
    by_document: dict[str, list[dict] | None] = {}
    for row in rows:
        document_id = str(row["document_id"])
        start = row.get("start_seconds") if hasattr(row, "get") else row["start_seconds"]
        end = row.get("end_seconds") if hasattr(row, "get") else row["end_seconds"]
        if start is None or end is None:
            by_document[document_id] = None
        elif document_id not in by_document or by_document[document_id] is not None:
            by_document.setdefault(document_id, []).append({
                "start_seconds": float(start), "end_seconds": float(end),
            })
    return [
        {"document_id": document_id, "ranges": ranges}
        for document_id, ranges in by_document.items() if ranges is not None
    ]


def _range_instruction(evidence_ranges: list[dict]) -> str:
    if not evidence_ranges:
        return "Use the complete attached assets."
    parts = []
    for item in evidence_ranges:
        windows = ", ".join(
            f"{window['start_seconds']:.2f}-{window['end_seconds']:.2f} seconds"
            for window in item["ranges"]
        )
        parts.append(f"document {item['document_id']}: {windows}")
    return "Use only these approved media ranges: " + "; ".join(parts) + "."


@router.get("/domain-packs")
async def list_domain_packs(current_user: CurrentUser):
    return [{"id": key, **value} for key, value in DOMAIN_PACKS.items()]


def _grade_practice_quiz(quiz: PracticeQuiz, answers: dict[str, list[str]]) -> dict:
    results: dict[str, dict] = {}
    correct_count = 0
    valid_questions = {question.id: question for question in quiz.questions}
    for question_id, selected in answers.items():
        question = valid_questions.get(question_id)
        if not question:
            raise HTTPException(400, f"Unknown practice question: {question_id}")
        selected_ids = sorted(set(selected))
        valid_options = {option.id for option in question.options}
        if not set(selected_ids) <= valid_options:
            raise HTTPException(400, f"Invalid option for practice question: {question_id}")
        correct_ids = sorted(option.id for option in question.options if option.correct)
        is_correct = selected_ids == correct_ids
        correct_count += int(is_correct)
        results[question_id] = {
            "selected": selected_ids,
            "correct": correct_ids,
            "is_correct": is_correct,
            "explanation": question.explanation,
        }
    return {
        "result": results,
        "correct_count": correct_count,
        "question_count": len(quiz.questions),
        "completed": len(results) == len(quiz.questions),
    }


@router.get("/courses")
async def list_courses(workspace_id: str, current_user: CurrentUser, db=Depends(get_db)):
    await _require_role(db, workspace_id, str(current_user["id"]), "viewer")
    rows = await db.fetch(
        """
        SELECT c.*, lcm.persona,
          (SELECT COUNT(*) FROM learning_course_members m WHERE m.course_id=c.id) AS member_count,
          (SELECT COUNT(*) FROM learning_assets a WHERE a.course_id=c.id) AS asset_count
        FROM learning_courses c
        LEFT JOIN learning_course_members lcm ON lcm.course_id=c.id AND lcm.user_id=$2::uuid
        WHERE c.workspace_id=$1::uuid
          AND (lcm.user_id IS NOT NULL OR EXISTS (
            SELECT 1 FROM workspace_members wm
            WHERE wm.workspace_id=c.workspace_id AND wm.user_id=$2::uuid AND wm.role IN ('owner','editor')
          ))
        ORDER BY c.updated_at DESC
        """,
        workspace_id, str(current_user["id"]),
    )
    return [_course_response(row) for row in rows]


@router.post("/courses")
async def create_course(body: CourseCreate, request: Request, current_user: CurrentUser, db=Depends(get_db)):
    user_id = str(current_user["id"])
    await _require_role(db, body.workspace_id, user_id, "editor")
    async with db.transaction():
        row = await db.fetchrow(
            """INSERT INTO learning_courses
               (workspace_id,created_by,title,course_code,semester,description,instructor_name,objectives,
                domain,domain_config,publication_status)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9,$10::jsonb,$11) RETURNING *""",
            body.workspace_id, user_id, body.title.strip(), body.course_code.strip(), body.semester.strip(),
            body.description.strip(), body.instructor_name.strip(), json.dumps(_clean_list(body.objectives)),
            body.domain, json.dumps(body.domain_config), body.publication_status,
        )
        await db.execute(
            """INSERT INTO learning_course_members (course_id,user_id,persona,added_by)
               VALUES ($1,$2,'teacher',$2) ON CONFLICT (course_id,user_id) DO NOTHING""",
            str(row["id"]), user_id,
        )
    await audit(db, user_id=user_id, action="learning_course_create", resource_type="learning_course",
                resource_id=str(row["id"]), metadata={"workspace_id": body.workspace_id},
                ip_address=ip_from(request), user_agent=ua_from(request))
    result = _course_response(row)
    result["persona"] = "teacher"
    await emit_event(
        db, user_id=user_id, workspace_id=body.workspace_id,
        event_type="learning.course.created", resource_type="learning_course",
        resource_id=str(row["id"]), payload={"title": result["title"], "course_code": result.get("course_code", "")},
    )
    return result


@router.get("/courses/{course_id}")
async def get_course(course_id: str, current_user: CurrentUser, db=Depends(get_db)):
    await _course_access(db, course_id, str(current_user["id"]))
    return await _course_workspace(db, course_id, str(current_user["id"]))


@router.get("/courses/{course_id}/scope")
async def resolve_learning_scope(
    course_id: str, current_user: CurrentUser, module_id: str | None = None,
    lesson_id: str | None = None, db=Depends(get_db),
):
    return await _resolve_learning_scope(
        db, course_id, str(current_user["id"]), module_id=module_id, lesson_id=lesson_id,
    )


@router.patch("/courses/{course_id}")
async def update_course(course_id: str, body: CourseUpdate, request: Request, current_user: CurrentUser, db=Depends(get_db)):
    user_id = str(current_user["id"])
    await _course_access(db, course_id, user_id, manage=True)
    values = body.model_dump(exclude_unset=True)
    if not values:
        return await _course_workspace(db, course_id, user_id)
    allowed = {"title", "course_code", "semester", "description", "instructor_name", "objectives", "status", "domain", "domain_config", "publication_status"}
    sets, params = [], [course_id]
    for key, value in values.items():
        if key not in allowed:
            continue
        params.append(json.dumps(_clean_list(value)) if key == "objectives" else json.dumps(value) if key == "domain_config" else value)
        cast = "::jsonb" if key in {"objectives", "domain_config"} else ""
        sets.append(f"{key}=${len(params)}{cast}")
    await db.execute(f"UPDATE learning_courses SET {','.join(sets)},updated_at=NOW() WHERE id=$1::uuid", *params)
    await audit(db, user_id=user_id, action="learning_course_update", resource_type="learning_course",
                resource_id=course_id, metadata={"fields": list(values)}, ip_address=ip_from(request), user_agent=ua_from(request))
    return await _course_workspace(db, course_id, user_id)


@router.delete("/courses/{course_id}")
async def delete_course(course_id: str, request: Request, current_user: CurrentUser, db=Depends(get_db)):
    user_id = str(current_user["id"])
    course = await _course_access(db, course_id, user_id, manage=True)
    if str(course["created_by"]) != user_id and course.get("workspace_role") != "owner" and course.get("persona") != "admin":
        raise HTTPException(403, "Only the course creator, course admin, or workspace owner can delete this course")
    await db.execute("DELETE FROM learning_courses WHERE id=$1::uuid", course_id)
    await audit(db, user_id=user_id, action="learning_course_delete", resource_type="learning_course",
                resource_id=course_id, ip_address=ip_from(request), user_agent=ua_from(request))
    return {"ok": True}


@router.post("/courses/{course_id}/members")
async def add_member(course_id: str, body: MemberCreate, current_user: CurrentUser, db=Depends(get_db)):
    user_id = str(current_user["id"])
    course = await _course_access(db, course_id, user_id, manage=True)
    target = await db.fetchrow("SELECT id,email,full_name FROM users WHERE LOWER(email)=LOWER($1)", body.email.strip())
    if not target:
        raise HTTPException(404, "User must create a DocIntel account before course enrollment")
    membership = await db.fetchrow("SELECT 1 FROM workspace_members WHERE workspace_id=$1 AND user_id=$2", str(course["workspace_id"]), str(target["id"]))
    if not membership:
        raise HTTPException(400, "Add this user to the workspace before course enrollment")
    await db.execute(
        """INSERT INTO learning_course_members (course_id,user_id,persona,added_by)
           VALUES ($1,$2,$3,$4) ON CONFLICT (course_id,user_id)
           DO UPDATE SET persona=EXCLUDED.persona,added_by=EXCLUDED.added_by""",
        course_id, str(target["id"]), body.persona, user_id,
    )
    return await _course_workspace(db, course_id, user_id)


@router.delete("/courses/{course_id}/members/{member_user_id}")
async def remove_member(course_id: str, member_user_id: str, current_user: CurrentUser, db=Depends(get_db)):
    user_id = str(current_user["id"])
    course = await _course_access(db, course_id, user_id, manage=True)
    if str(course["created_by"]) == member_user_id:
        raise HTTPException(400, "The course creator cannot be removed")
    await db.execute("DELETE FROM learning_course_members WHERE course_id=$1 AND user_id=$2", course_id, member_user_id)
    return {"ok": True}


@router.put("/courses/{course_id}/curriculum")
async def save_curriculum(course_id: str, body: CurriculumSave, current_user: CurrentUser, db=Depends(get_db)):
    user_id = str(current_user["id"])
    await _course_access(db, course_id, user_id, manage=True)
    async with db.transaction():
        saved_module_ids: list[str] = []
        saved_lesson_ids: list[str] = []
        for module_position, module in enumerate(body.modules[:100]):
            title = str(module.get("title") or "").strip()
            if not title:
                continue
            module_id = str(module.get("id") or "").strip()
            if module_id:
                module_row = await db.fetchrow(
                    """UPDATE learning_modules SET title=$3,description=$4,position=$5,updated_at=NOW()
                       WHERE id=$1::uuid AND course_id=$2::uuid RETURNING id""",
                    module_id, course_id, title, str(module.get("description") or "").strip(), module_position,
                )
                if not module_row:
                    raise HTTPException(400, "Module does not belong to this course")
            else:
                module_row = await db.fetchrow(
                    """INSERT INTO learning_modules (course_id,title,description,position)
                       VALUES ($1,$2,$3,$4) RETURNING id""",
                    course_id, title, str(module.get("description") or "").strip(), module_position,
                )
            saved_module_id = str(module_row["id"])
            saved_module_ids.append(saved_module_id)
            for lesson_position, lesson in enumerate((module.get("lessons") or [])[:200]):
                lesson_title = str(lesson.get("title") or "").strip()
                if lesson_title:
                    lesson_id = str(lesson.get("id") or "").strip()
                    if lesson_id:
                        lesson_row = await db.fetchrow(
                            """UPDATE learning_lessons SET title=$3,description=$4,position=$5,
                               objectives=$6::jsonb,competencies=$7::jsonb,updated_at=NOW()
                               WHERE id=$1::uuid AND module_id=$2::uuid RETURNING id""",
                            lesson_id, saved_module_id, lesson_title,
                            str(lesson.get("description") or "").strip(), lesson_position,
                            json.dumps(_clean_list(lesson.get("objectives"))),
                            json.dumps(_clean_list(lesson.get("competencies"))),
                        )
                        if not lesson_row:
                            raise HTTPException(400, "Lesson does not belong to its selected module")
                    else:
                        lesson_row = await db.fetchrow(
                            """INSERT INTO learning_lessons
                               (module_id,title,description,position,objectives,competencies)
                               VALUES ($1,$2,$3,$4,$5::jsonb,$6::jsonb) RETURNING id""",
                            saved_module_id, lesson_title,
                            str(lesson.get("description") or "").strip(), lesson_position,
                            json.dumps(_clean_list(lesson.get("objectives"))),
                            json.dumps(_clean_list(lesson.get("competencies"))),
                        )
                    saved_lesson_ids.append(str(lesson_row["id"]))
        await db.execute(
            """DELETE FROM learning_lessons
               WHERE module_id IN (SELECT id FROM learning_modules WHERE course_id=$1::uuid)
                 AND id <> ALL($2::uuid[])""",
            course_id, saved_lesson_ids,
        )
        await db.execute(
            "DELETE FROM learning_modules WHERE course_id=$1::uuid AND id <> ALL($2::uuid[])",
            course_id, saved_module_ids,
        )
    return await _course_workspace(db, course_id, user_id)


@router.get("/documents")
async def list_learning_documents(workspace_id: str, current_user: CurrentUser, db=Depends(get_db)):
    await _require_role(db, workspace_id, str(current_user["id"]), "viewer")
    rows = await db.fetch(
        """SELECT id,original_name,file_type,doc_type,doc_domain,status,chunk_count,created_at
           FROM documents WHERE workspace_id=$1::uuid AND status!='deleted'
           ORDER BY created_at DESC""",
        workspace_id,
    )
    return [_jsonable(dict(row)) for row in rows]


@router.post("/courses/{course_id}/assets")
async def add_asset(course_id: str, body: AssetCreate, current_user: CurrentUser, db=Depends(get_db)):
    user_id = str(current_user["id"])
    course = await _course_access(db, course_id, user_id, manage=True)
    doc = await db.fetchrow(
        """SELECT id,original_name,status FROM documents
           WHERE id=$1::uuid AND workspace_id=$2::uuid AND status!='deleted'""",
        body.document_id, str(course["workspace_id"]),
    )
    if not doc:
        raise HTTPException(404, "Document is not available in this course workspace")
    module_id = body.module_id
    lesson_id = body.lesson_id
    if module_id:
        valid_module = await db.fetchval("SELECT 1 FROM learning_modules WHERE id=$1 AND course_id=$2", body.module_id, course_id)
        if not valid_module:
            raise HTTPException(400, "Module does not belong to this course")
    if lesson_id:
        lesson_module_id = await db.fetchval(
            """SELECT l.module_id FROM learning_lessons l JOIN learning_modules m ON m.id=l.module_id
               WHERE l.id=$1 AND m.course_id=$2""", lesson_id, course_id,
        )
        if not lesson_module_id:
            raise HTTPException(400, "Lesson does not belong to this course")
        if module_id and str(lesson_module_id) != str(module_id):
            raise HTTPException(400, "Lesson does not belong to the selected module")
        module_id = str(lesson_module_id)
    if body.asset_id:
        updated = await db.fetchval(
            """UPDATE learning_assets SET module_id=$3,lesson_id=$4,title=$5,start_seconds=$6,end_seconds=$7
               WHERE id=$1::uuid AND course_id=$2::uuid AND document_id=$8::uuid RETURNING id""",
            body.asset_id, course_id, module_id, lesson_id, body.title.strip() or doc["original_name"],
            body.start_seconds, body.end_seconds, body.document_id,
        )
        if not updated:
            raise HTTPException(404, "Course content mapping not found")
    else:
        duplicate = await db.fetchval(
            """SELECT id FROM learning_assets WHERE course_id=$1::uuid AND document_id=$2::uuid
               AND module_id IS NOT DISTINCT FROM $3::uuid AND lesson_id IS NOT DISTINCT FROM $4::uuid
               AND start_seconds IS NOT DISTINCT FROM $5 AND end_seconds IS NOT DISTINCT FROM $6""",
            course_id, body.document_id, module_id, lesson_id, body.start_seconds, body.end_seconds,
        )
        if duplicate:
            raise HTTPException(409, "This content mapping already exists")
        await db.execute(
            """INSERT INTO learning_assets
               (course_id,module_id,lesson_id,document_id,title,added_by,start_seconds,end_seconds)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
            course_id, module_id, lesson_id, body.document_id, body.title.strip() or doc["original_name"],
            user_id, body.start_seconds, body.end_seconds,
        )
    await emit_event(
        db, user_id=user_id, workspace_id=str(course["workspace_id"]),
        event_type="learning.content.attached", resource_type="learning_course",
        resource_id=course_id, payload={
            "document_id": body.document_id, "module_id": module_id, "lesson_id": lesson_id,
            "start_seconds": body.start_seconds, "end_seconds": body.end_seconds,
        },
    )
    return await _course_workspace(db, course_id, user_id)


@router.patch("/courses/{course_id}/assets/{asset_id}")
async def update_asset_mapping(
    course_id: str, asset_id: str, body: AssetMappingUpdate,
    current_user: CurrentUser, db=Depends(get_db),
):
    user_id = str(current_user["id"])
    course = await _course_access(db, course_id, user_id, manage=True)
    asset = await db.fetchrow(
        """SELECT a.id,a.document_id,d.original_name FROM learning_assets a
           JOIN documents d ON d.id=a.document_id
           WHERE a.id=$1::uuid AND a.course_id=$2::uuid""",
        asset_id, course_id,
    )
    if not asset:
        raise HTTPException(404, "Course content mapping not found")
    module_id = body.module_id
    lesson_id = body.lesson_id
    if module_id:
        valid_module = await db.fetchval(
            "SELECT 1 FROM learning_modules WHERE id=$1::uuid AND course_id=$2::uuid",
            module_id, course_id,
        )
        if not valid_module:
            raise HTTPException(400, "Module does not belong to this course")
    if lesson_id:
        lesson_module_id = await db.fetchval(
            """SELECT l.module_id FROM learning_lessons l JOIN learning_modules m ON m.id=l.module_id
               WHERE l.id=$1::uuid AND m.course_id=$2::uuid""",
            lesson_id, course_id,
        )
        if not lesson_module_id:
            raise HTTPException(400, "Lesson does not belong to this course")
        if module_id and str(lesson_module_id) != str(module_id):
            raise HTTPException(400, "Lesson does not belong to the selected module")
        module_id = str(lesson_module_id)
    duplicate = await db.fetchval(
        """SELECT id FROM learning_assets WHERE course_id=$1::uuid AND document_id=$2::uuid
           AND id<>$3::uuid AND module_id IS NOT DISTINCT FROM $4::uuid
           AND lesson_id IS NOT DISTINCT FROM $5::uuid
           AND start_seconds IS NOT DISTINCT FROM $6 AND end_seconds IS NOT DISTINCT FROM $7""",
        course_id, str(asset["document_id"]), asset_id, module_id, lesson_id,
        body.start_seconds, body.end_seconds,
    )
    if duplicate:
        raise HTTPException(409, "This content mapping already exists")
    await db.execute(
        """UPDATE learning_assets SET module_id=$3::uuid,lesson_id=$4::uuid,title=$5,
           start_seconds=$6,end_seconds=$7 WHERE id=$1::uuid AND course_id=$2::uuid""",
        asset_id, course_id, module_id, lesson_id,
        body.title.strip() or asset["original_name"], body.start_seconds, body.end_seconds,
    )
    await emit_event(
        db, user_id=user_id, workspace_id=str(course["workspace_id"]),
        event_type="learning.content.mapping_updated", resource_type="learning_course",
        resource_id=course_id, payload={
            "asset_id": asset_id, "document_id": str(asset["document_id"]),
            "module_id": module_id, "lesson_id": lesson_id,
            "start_seconds": body.start_seconds, "end_seconds": body.end_seconds,
        },
    )
    return await _course_workspace(db, course_id, user_id)


@router.delete("/courses/{course_id}/assets/{asset_id}")
async def remove_asset(course_id: str, asset_id: str, current_user: CurrentUser, db=Depends(get_db)):
    await _course_access(db, course_id, str(current_user["id"]), manage=True)
    await db.execute("DELETE FROM learning_assets WHERE id=$1::uuid AND course_id=$2::uuid", asset_id, course_id)
    return {"ok": True}


@router.post("/courses/{course_id}/assignments")
async def create_assignment(
    course_id: str, body: AssignmentCreate, request: Request,
    current_user: CurrentUser, db=Depends(get_db),
):
    user_id = str(current_user["id"])
    course = await _course_access(db, course_id, user_id, manage=True)
    module_id, lesson_id = await _validate_assignment_scope(db, course_id, body.module_id, body.lesson_id)
    await _validate_course_documents(db, str(course["workspace_id"]), body.source_document_ids)
    row = await db.fetchrow(
        """INSERT INTO learning_assignments
           (course_id,module_id,lesson_id,created_by,title,description,assignment_type,rubric,
            source_document_ids,max_score,due_at,publication_status)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9::jsonb,$10,$11,$12) RETURNING *""",
        course_id, module_id, lesson_id, user_id, body.title.strip(), body.description.strip(),
        body.assignment_type, json.dumps([item.model_dump() for item in body.rubric]),
        json.dumps(body.source_document_ids), body.max_score, body.due_at, body.publication_status,
    )
    await audit(db, user_id=user_id, action="learning_assignment_create", resource_type="learning_assignment",
                resource_id=str(row["id"]), metadata={"course_id": course_id},
                ip_address=ip_from(request), user_agent=ua_from(request))
    await emit_event(
        db, user_id=user_id, workspace_id=str(course["workspace_id"]),
        event_type="learning.assignment.created", resource_type="learning_assignment",
        resource_id=str(row["id"]), payload={"course_id": course_id, "title": body.title},
    )
    return _assignment_response(row)


@router.get("/courses/{course_id}/assignments")
async def list_assignments(course_id: str, current_user: CurrentUser, db=Depends(get_db)):
    workspace = await _course_workspace(db, course_id, str(current_user["id"]))
    return {"assignments": workspace["assignments"], "submissions": workspace["submissions"]}


@router.patch("/courses/{course_id}/assignments/{assignment_id}")
async def update_assignment(
    course_id: str, assignment_id: str, body: AssignmentUpdate,
    current_user: CurrentUser, db=Depends(get_db),
):
    user_id = str(current_user["id"])
    course = await _course_access(db, course_id, user_id, manage=True)
    existing = await db.fetchrow(
        "SELECT * FROM learning_assignments WHERE id=$1::uuid AND course_id=$2::uuid", assignment_id, course_id,
    )
    if not existing:
        raise HTTPException(404, "Assignment not found")
    values = body.model_dump(exclude_unset=True)
    module_id = values.get("module_id", str(existing["module_id"]) if existing["module_id"] else None)
    lesson_id = values.get("lesson_id", str(existing["lesson_id"]) if existing["lesson_id"] else None)
    module_id, lesson_id = await _validate_assignment_scope(db, course_id, module_id, lesson_id)
    values["module_id"], values["lesson_id"] = module_id, lesson_id
    if "source_document_ids" in values:
        await _validate_course_documents(db, str(course["workspace_id"]), values["source_document_ids"])
    allowed = {"title", "description", "assignment_type", "module_id", "lesson_id", "rubric",
               "source_document_ids", "max_score", "due_at", "publication_status"}
    sets, params = [], [assignment_id, course_id]
    for key, value in values.items():
        if key not in allowed:
            continue
        if key == "rubric":
            value = json.dumps([item.model_dump() if hasattr(item, "model_dump") else item for item in value])
        elif key == "source_document_ids":
            value = json.dumps(value)
        params.append(value)
        cast = "::jsonb" if key in {"rubric", "source_document_ids"} else "::uuid" if key in {"module_id", "lesson_id"} else ""
        sets.append(f"{key}=${len(params)}{cast}")
    if sets:
        await db.execute(
            f"UPDATE learning_assignments SET {','.join(sets)},updated_at=NOW() WHERE id=$1::uuid AND course_id=$2::uuid",
            *params,
        )
    row = await db.fetchrow("SELECT * FROM learning_assignments WHERE id=$1::uuid", assignment_id)
    return _assignment_response(row)


@router.delete("/courses/{course_id}/assignments/{assignment_id}")
async def delete_assignment(course_id: str, assignment_id: str, current_user: CurrentUser, db=Depends(get_db)):
    await _course_access(db, course_id, str(current_user["id"]), manage=True)
    result = await db.execute(
        "DELETE FROM learning_assignments WHERE id=$1::uuid AND course_id=$2::uuid", assignment_id, course_id,
    )
    if result == "DELETE 0":
        raise HTTPException(404, "Assignment not found")
    return {"ok": True}


@router.put("/courses/{course_id}/assignments/{assignment_id}/submission")
async def save_assignment_submission(
    course_id: str, assignment_id: str, body: SubmissionUpsert,
    current_user: CurrentUser, db=Depends(get_db),
):
    user_id = str(current_user["id"])
    course = await _course_access(db, course_id, user_id)
    assignment = await db.fetchrow(
        "SELECT * FROM learning_assignments WHERE id=$1::uuid AND course_id=$2::uuid", assignment_id, course_id,
    )
    if not assignment or (assignment["publication_status"] == "draft" and not course.get("persona") in MANAGER_PERSONAS):
        raise HTTPException(404, "Assignment not found")
    if assignment["publication_status"] == "closed":
        raise HTTPException(409, "This assignment is closed")
    document_ids = list(dict.fromkeys(body.document_ids + ([body.presentation_document_id] if body.presentation_document_id else [])))
    await _validate_course_documents(db, str(course["workspace_id"]), document_ids)
    existing = await db.fetchrow(
        "SELECT * FROM learning_submissions WHERE assignment_id=$1::uuid AND user_id=$2::uuid", assignment_id, user_id,
    )
    revision_number = int(existing["revision_number"] or 1) if existing else 1
    if existing and existing["status"] in {"submitted", "in_review"}:
        raise HTTPException(409, "The submission is under review; wait for instructor feedback")
    if existing and existing["status"] == "approved":
        raise HTTPException(409, "The approved submission is final")
    if existing and existing["status"] == "revision_requested":
        await db.execute(
            """INSERT INTO learning_submission_revisions
               (submission_id,revision_number,submission_text,document_ids,presentation_document_id,status,created_by)
               VALUES ($1,$2,$3,$4,$5,$6,$7) ON CONFLICT DO NOTHING""",
            str(existing["id"]), revision_number, existing["submission_text"],
            json.dumps(_decode_json(existing["document_ids"], [])),
            existing["presentation_document_id"], existing["status"], user_id,
        )
        revision_number += 1
    status = "submitted" if body.submit else "draft"
    row = await db.fetchrow(
        """INSERT INTO learning_submissions
           (course_id,assignment_id,user_id,submission_text,document_ids,presentation_document_id,status,
            revision_number,submitted_at)
           VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7,$8,CASE WHEN $7='submitted' THEN NOW() END)
           ON CONFLICT (assignment_id,user_id) DO UPDATE SET
             submission_text=EXCLUDED.submission_text,document_ids=EXCLUDED.document_ids,
             presentation_document_id=EXCLUDED.presentation_document_id,status=EXCLUDED.status,
             revision_number=EXCLUDED.revision_number,ai_evaluation='{}'::jsonb,instructor_feedback='',
             score=NULL,reviewed_by=NULL,reviewed_at=NULL,approved_at=NULL,
             submitted_at=CASE WHEN EXCLUDED.status='submitted' THEN NOW() ELSE learning_submissions.submitted_at END,
             updated_at=NOW() RETURNING *""",
        course_id, assignment_id, user_id, body.submission_text.strip(), json.dumps(body.document_ids),
        body.presentation_document_id, status, revision_number,
    )
    await emit_event(
        db, user_id=user_id, workspace_id=str(course["workspace_id"]),
        event_type="learning.assignment.submitted" if body.submit else "learning.assignment.saved",
        resource_type="learning_submission", resource_id=str(row["id"]),
        payload={"course_id": course_id, "assignment_id": assignment_id, "status": status},
    )
    return _submission_response(row)


@router.post("/courses/{course_id}/assignments/{assignment_id}/submissions/{submission_id}/evaluate")
async def evaluate_assignment_submission(
    course_id: str, assignment_id: str, submission_id: str,
    current_user: CurrentUser, db=Depends(get_db),
):
    user_id = str(current_user["id"])
    await _course_access(db, course_id, user_id, manage=True)
    assignment_row = await db.fetchrow(
        "SELECT * FROM learning_assignments WHERE id=$1::uuid AND course_id=$2::uuid", assignment_id, course_id,
    )
    submission_row = await db.fetchrow(
        "SELECT * FROM learning_submissions WHERE id=$1::uuid AND assignment_id=$2::uuid", submission_id, assignment_id,
    )
    if not assignment_row or not submission_row:
        raise HTTPException(404, "Assignment submission not found")
    assignment, submission = _assignment_response(assignment_row), _submission_response(submission_row)
    if not _can_run_submission_evaluation(submission):
        raise HTTPException(409, "Only submitted work or a failed automated evaluation can be evaluated")
    evidence_ids = list(dict.fromkeys(submission["document_ids"] + (
        [submission["presentation_document_id"]] if submission.get("presentation_document_id") else []
    )))
    chunk_rows = await db.fetch(
        """SELECT d.original_name,c.chunk_index,c.content FROM document_chunks c
           JOIN documents d ON d.id=c.document_id
           WHERE c.document_id=ANY($1::uuid[]) ORDER BY d.original_name,c.chunk_index LIMIT 30""",
        evidence_ids,
    ) if evidence_ids else []
    evidence = "\n\n".join(
        f"[{row['original_name']} chunk {int(row['chunk_index']) + 1}]\n{str(row['content'])[:2000]}" for row in chunk_rows
    )
    evaluation = await _generate_submission_evaluation(assignment, submission, evidence)
    row = await db.fetchrow(
        """UPDATE learning_submissions SET ai_evaluation=$3::jsonb,status='in_review',updated_at=NOW()
           WHERE id=$1::uuid AND assignment_id=$2::uuid RETURNING *""",
        submission_id, assignment_id, json.dumps(evaluation),
    )
    return _submission_response(row)


@router.patch("/courses/{course_id}/assignments/{assignment_id}/submissions/{submission_id}/review")
async def review_assignment_submission(
    course_id: str, assignment_id: str, submission_id: str, body: SubmissionReview,
    current_user: CurrentUser, db=Depends(get_db),
):
    user_id = str(current_user["id"])
    course = await _course_access(db, course_id, user_id, manage=True)
    maximum = await db.fetchval(
        "SELECT max_score FROM learning_assignments WHERE id=$1::uuid AND course_id=$2::uuid", assignment_id, course_id,
    )
    if maximum is None:
        raise HTTPException(404, "Assignment not found")
    current_status = await db.fetchval(
        "SELECT status FROM learning_submissions WHERE id=$1::uuid AND assignment_id=$2::uuid AND course_id=$3::uuid",
        submission_id, assignment_id, course_id,
    )
    if current_status not in {"submitted", "in_review"}:
        raise HTTPException(409, "Only submitted or evaluated work can be reviewed")
    if body.score is not None and body.score > float(maximum):
        raise HTTPException(400, "Score exceeds the assignment maximum")
    row = await db.fetchrow(
        """UPDATE learning_submissions SET status=$4,instructor_feedback=$5,score=$6,reviewed_by=$7,
           reviewed_at=NOW(),approved_at=CASE WHEN $4='approved' THEN NOW() ELSE NULL END,updated_at=NOW()
           WHERE id=$1::uuid AND assignment_id=$2::uuid AND course_id=$3::uuid RETURNING *""",
        submission_id, assignment_id, course_id, body.status, body.instructor_feedback.strip(), body.score, user_id,
    )
    if not row:
        raise HTTPException(404, "Submission not found")
    await emit_event(
        db, user_id=user_id, workspace_id=str(course["workspace_id"]),
        event_type="learning.assignment.reviewed", resource_type="learning_submission",
        resource_id=submission_id, payload={"course_id": course_id, "status": body.status, "score": body.score},
    )
    return _submission_response(row)


@router.get("/courses/{course_id}/evidence/{document_id}/view-url")
async def get_learning_evidence_url(
    course_id: str, document_id: str, current_user: CurrentUser, db=Depends(get_db),
):
    await _course_access(db, course_id, str(current_user["id"]))
    row = await db.fetchrow(
        """SELECT d.id,d.original_name,d.file_type,d.gcs_source_path
           FROM documents d JOIN learning_assets a ON a.document_id=d.id
           WHERE a.course_id=$1::uuid AND d.id=$2::uuid LIMIT 1""", course_id, document_id,
    )
    if not row:
        raise HTTPException(404, "Evidence is not attached to this course")
    if not row["gcs_source_path"]:
        raise HTTPException(409, "The original evidence file is not available in storage")
    return {"document_id": document_id, "filename": row["original_name"], "file_type": row["file_type"],
            "url": await gcs.get_signed_url(row["gcs_source_path"])}


@router.post("/courses/{course_id}/artifacts")
async def save_artifact(course_id: str, body: ArtifactCreate, current_user: CurrentUser, db=Depends(get_db)):
    user_id = str(current_user["id"])
    scope = await _resolve_learning_scope(db, course_id, user_id, body.module_id, body.lesson_id)
    allowed_ids = set(scope["document_ids"])
    invalid = set(body.source_document_ids) - allowed_ids
    if invalid:
        raise HTTPException(400, "Study artifact references content outside the selected learning scope")
    content = _normalize_artifact_content(body.artifact_type, body.content)
    row = await db.fetchrow(
        """INSERT INTO learning_artifacts
           (course_id,user_id,artifact_type,title,content,source_document_ids,module_id,lesson_id)
           VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7,$8) RETURNING *""",
        course_id, user_id, body.artifact_type, body.title.strip(), content,
        json.dumps(body.source_document_ids), scope["module_id"], scope["lesson_id"],
    )
    return _jsonable(dict(row))


@router.post("/courses/{course_id}/artifacts/{artifact_id}/attempts")
async def submit_quiz_attempt(
    course_id: str, artifact_id: str, body: QuizAttemptCreate,
    current_user: CurrentUser, db=Depends(get_db),
):
    user_id = str(current_user["id"])
    await _course_access(db, course_id, user_id)
    artifact = await db.fetchrow(
        """SELECT id,content,module_id,lesson_id FROM learning_artifacts
           WHERE id=$1::uuid AND course_id=$2::uuid AND artifact_type='practice_questions'""",
        artifact_id, course_id,
    )
    if not artifact:
        raise HTTPException(404, "Practice-question artifact not found")
    quiz = PracticeQuiz.model_validate_json(str(artifact["content"]))
    existing = await db.fetchrow(
        "SELECT answers FROM learning_quiz_attempts WHERE artifact_id=$1::uuid AND user_id=$2::uuid",
        artifact_id, user_id,
    )
    answers = {} if body.replace else (_decode_json(existing["answers"], {}) if existing else {})
    answers.update(body.answers)
    graded = _grade_practice_quiz(quiz, answers)
    row = await db.fetchrow(
        """INSERT INTO learning_quiz_attempts
           (course_id,artifact_id,user_id,answers,result,correct_count,question_count,completed)
           VALUES ($1,$2,$3,$4::jsonb,$5::jsonb,$6,$7,$8)
           ON CONFLICT (artifact_id,user_id) DO UPDATE SET
             answers=EXCLUDED.answers,result=EXCLUDED.result,correct_count=EXCLUDED.correct_count,
             question_count=EXCLUDED.question_count,completed=EXCLUDED.completed,
             submitted_at=NOW(),updated_at=NOW()
           RETURNING *""",
        course_id, artifact_id, user_id, json.dumps(answers), json.dumps(graded["result"]),
        graded["correct_count"], graded["question_count"], graded["completed"],
    )
    result = _jsonable(dict(row))
    result["answers"] = _decode_json(result.get("answers"), {})
    result["result"] = _decode_json(result.get("result"), {})
    if artifact.get("lesson_id"):
        course_config = _decode_json(
            await db.fetchval("SELECT domain_config FROM learning_courses WHERE id=$1::uuid", course_id), {},
        )
        passing_score = max(0, min(100, int(course_config.get("passing_score", 80))))
        score_pct = round((graded["correct_count"] / graded["question_count"]) * 100) if graded["question_count"] else 0
        lesson_status = "completed" if graded["completed"] and score_pct >= passing_score else "in_progress"
        await db.execute(
            """INSERT INTO learning_lesson_progress
               (course_id,module_id,lesson_id,user_id,status,progress_pct,started_at,completed_at)
               VALUES ($1,$2,$3,$4,$5,$6,NOW(),CASE WHEN $5='completed' THEN NOW() END)
               ON CONFLICT (lesson_id,user_id) DO UPDATE SET
                 status=CASE WHEN learning_lesson_progress.status='completed' THEN 'completed' ELSE EXCLUDED.status END,
                 progress_pct=GREATEST(learning_lesson_progress.progress_pct,EXCLUDED.progress_pct),
                 started_at=COALESCE(learning_lesson_progress.started_at,NOW()),
                 completed_at=CASE WHEN EXCLUDED.status='completed' THEN COALESCE(learning_lesson_progress.completed_at,NOW()) ELSE learning_lesson_progress.completed_at END,
                 updated_at=NOW()""",
            course_id, str(artifact["module_id"]), str(artifact["lesson_id"]), user_id,
            lesson_status, 100 if lesson_status == "completed" else 50,
        )
    course_workspace_id = await db.fetchval("SELECT workspace_id FROM learning_courses WHERE id=$1::uuid", course_id)
    await emit_event(
        db, user_id=user_id, workspace_id=str(course_workspace_id),
        event_type="learning.quiz.submitted", resource_type="learning_quiz_attempt",
        resource_id=str(row["id"]), payload={"course_id": course_id, "artifact_id": artifact_id, "correct_count": graded["correct_count"], "question_count": graded["question_count"], "completed": graded["completed"]},
    )
    return result


@router.get("/courses/{course_id}/progress")
async def get_learning_progress(course_id: str, current_user: CurrentUser, db=Depends(get_db)):
    user_id = str(current_user["id"])
    course = await _course_access(db, course_id, user_id)
    can_review = bool(
        course.get("workspace_role") in ("owner", "editor")
        or course.get("persona") in MANAGER_PERSONAS | {"advisor"}
    )
    rows = await db.fetch(
        """SELECT qa.*,u.email,u.full_name,a.title AS artifact_title
           FROM learning_quiz_attempts qa
           JOIN users u ON u.id=qa.user_id JOIN learning_artifacts a ON a.id=qa.artifact_id
           WHERE qa.course_id=$1::uuid AND ($2::boolean OR qa.user_id=$3::uuid)
           ORDER BY qa.updated_at DESC""",
        course_id, can_review, user_id,
    )
    return [_quiz_attempt_response(row) for row in rows]


@router.put("/courses/{course_id}/lessons/{lesson_id}/progress")
async def update_lesson_progress(
    course_id: str, lesson_id: str, body: LessonProgressUpdate,
    current_user: CurrentUser, db=Depends(get_db),
):
    user_id = str(current_user["id"])
    course = await _course_access(db, course_id, user_id)
    lesson = await db.fetchrow(
        """SELECT l.id,l.module_id FROM learning_lessons l
           JOIN learning_modules m ON m.id=l.module_id
           WHERE l.id=$1::uuid AND m.course_id=$2::uuid""",
        lesson_id, course_id,
    )
    if not lesson:
        raise HTTPException(404, "Lesson not found in this course")
    row = await db.fetchrow(
        """INSERT INTO learning_lesson_progress
           (course_id,module_id,lesson_id,user_id,status,progress_pct,time_spent_seconds,
            last_position_seconds,started_at,completed_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,
                   CASE WHEN $5<>'not_started' THEN NOW() END,
                   CASE WHEN $5='completed' THEN NOW() END)
           ON CONFLICT (lesson_id,user_id) DO UPDATE SET
             status=EXCLUDED.status,progress_pct=EXCLUDED.progress_pct,
             time_spent_seconds=GREATEST(learning_lesson_progress.time_spent_seconds,EXCLUDED.time_spent_seconds),
             last_position_seconds=EXCLUDED.last_position_seconds,
             started_at=CASE WHEN EXCLUDED.status='not_started' THEN NULL ELSE COALESCE(learning_lesson_progress.started_at,NOW()) END,
             completed_at=CASE WHEN EXCLUDED.status='completed' THEN COALESCE(learning_lesson_progress.completed_at,NOW()) ELSE NULL END,
             updated_at=NOW()
           RETURNING *""",
        course_id, str(lesson["module_id"]), lesson_id, user_id, body.status,
        body.progress_pct, body.time_spent_seconds, body.last_position_seconds,
    )
    await emit_event(
        db, user_id=user_id, workspace_id=str(course["workspace_id"]),
        event_type="learning.progress.updated", resource_type="learning_lesson_progress",
        resource_id=str(row["id"]), payload={
            "course_id": course_id, "lesson_id": lesson_id,
            "status": body.status, "progress_pct": body.progress_pct,
        },
    )
    return _jsonable(dict(row))


@router.get("/courses/{course_id}/mastery")
async def get_learning_mastery(
    course_id: str, current_user: CurrentUser, learner_id: str | None = None,
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    course = await _course_access(db, course_id, user_id)
    can_review = bool(
        course.get("workspace_role") in ("owner", "editor")
        or course.get("persona") in MANAGER_PERSONAS | {"advisor"}
    )
    target_user_id = learner_id or user_id
    if target_user_id != user_id and not can_review:
        raise HTTPException(403, "Only a teacher, advisor, or admin may review another learner")
    member = await db.fetchrow(
        """SELECT u.id,u.email,u.full_name,m.persona FROM learning_course_members m
           JOIN users u ON u.id=m.user_id
           WHERE m.course_id=$1::uuid AND m.user_id=$2::uuid""",
        course_id, target_user_id,
    )
    if not member:
        raise HTTPException(404, "Learner is not enrolled in this course")
    modules = await db.fetch(
        "SELECT id,title,description,position FROM learning_modules WHERE course_id=$1::uuid ORDER BY position,created_at",
        course_id,
    )
    lessons = await db.fetch(
        """SELECT l.id,l.module_id,l.title,l.description,l.objectives,l.competencies,l.position
           FROM learning_lessons l JOIN learning_modules m ON m.id=l.module_id
           WHERE m.course_id=$1::uuid ORDER BY m.position,l.position,l.created_at""",
        course_id,
    )
    progress = await db.fetch(
        "SELECT * FROM learning_lesson_progress WHERE course_id=$1::uuid AND user_id=$2::uuid",
        course_id, target_user_id,
    )
    attempts = await db.fetch(
        """SELECT qa.*,a.module_id,a.lesson_id,a.title AS artifact_title
           FROM learning_quiz_attempts qa JOIN learning_artifacts a ON a.id=qa.artifact_id
           WHERE qa.course_id=$1::uuid AND qa.user_id=$2::uuid""",
        course_id, target_user_id,
    )
    return _build_mastery_projection(
        _course_response(course), [_jsonable(dict(row)) for row in modules],
        [_jsonable(dict(row)) for row in lessons], [_jsonable(dict(row)) for row in progress],
        [_quiz_attempt_response(row) for row in attempts], _jsonable(dict(member)),
    )


@router.delete("/courses/{course_id}/artifacts/{artifact_id}")
async def delete_artifact(course_id: str, artifact_id: str, current_user: CurrentUser, db=Depends(get_db)):
    user_id = str(current_user["id"])
    await _course_access(db, course_id, user_id)
    result = await db.execute("DELETE FROM learning_artifacts WHERE id=$1 AND course_id=$2 AND user_id=$3", artifact_id, course_id, user_id)
    if result == "DELETE 0":
        raise HTTPException(404, "Study artifact not found")
    return {"ok": True}


@router.post("/courses/{course_id}/questions")
async def ask_human(course_id: str, body: QuestionCreate, request: Request, current_user: CurrentUser, db=Depends(get_db)):
    user_id = str(current_user["id"])
    await _course_access(db, course_id, user_id)
    assignee = await db.fetchrow(
        """SELECT m.user_id,u.email FROM learning_course_members m JOIN users u ON u.id=m.user_id
           WHERE m.course_id=$1 AND m.persona=$2 ORDER BY m.created_at LIMIT 1""",
        course_id, body.target_role,
    )
    row = await db.fetchrow(
        """INSERT INTO learning_questions (course_id,asked_by,assigned_to,target_role,question,context)
           VALUES ($1,$2,$3,$4,$5,$6::jsonb) RETURNING *""",
        course_id, user_id, str(assignee["user_id"]) if assignee else None, body.target_role,
        body.question.strip(), json.dumps(body.context),
    )
    await audit(db, user_id=user_id, action="learning_question_create", resource_type="learning_question",
                resource_id=str(row["id"]), metadata={"course_id": course_id, "target_role": body.target_role},
                ip_address=ip_from(request), user_agent=ua_from(request))
    if assignee:
        await send_learning_question_notification(
            assignee["email"], course_title=str((await db.fetchval("SELECT title FROM learning_courses WHERE id=$1", course_id)) or "Course"),
            question=body.question.strip(), action="assigned",
        )
    return _question_response(row)


@router.patch("/courses/{course_id}/questions/{question_id}")
async def answer_human(course_id: str, question_id: str, body: QuestionUpdate, request: Request, current_user: CurrentUser, db=Depends(get_db)):
    user_id = str(current_user["id"])
    course = await _course_access(db, course_id, user_id)
    persona = course.get("persona")
    question = await db.fetchrow("SELECT * FROM learning_questions WHERE id=$1 AND course_id=$2", question_id, course_id)
    if not question:
        raise HTTPException(404, "Question not found")
    is_reviewer = persona in MANAGER_PERSONAS | {"advisor"} or course.get("workspace_role") in ("owner", "editor")
    is_asker = str(question["asked_by"]) == user_id
    if not is_reviewer and not is_asker:
        raise HTTPException(403, "You cannot update this question")
    if is_asker and not is_reviewer and (body.answer is not None or body.assigned_to is not None or body.status not in (None, "closed")):
        raise HTTPException(403, "Learners may close their question, but only a teacher or advisor may answer or reassign it")
    if body.assigned_to:
        valid_assignee = await db.fetchval(
            "SELECT 1 FROM learning_course_members WHERE course_id=$1 AND user_id=$2 AND persona IN ('admin','teacher','advisor')",
            course_id, body.assigned_to,
        )
        if not valid_assignee:
            raise HTTPException(400, "Assignee must be a teacher, advisor, or admin in this course")
    answer = body.answer if body.answer is not None else question["answer"]
    status = body.status or ("answered" if body.answer and body.answer.strip() else question["status"])
    row = await db.fetchrow(
        """UPDATE learning_questions SET answer=$3,status=$4,assigned_to=COALESCE($5,assigned_to),
           answered_by=CASE WHEN $3<>'' THEN $6 ELSE answered_by END,
           answered_at=CASE WHEN $3<>'' THEN NOW() ELSE answered_at END,updated_at=NOW()
           WHERE id=$1 AND course_id=$2 RETURNING *""",
        question_id, course_id, answer, status, body.assigned_to, user_id,
    )
    await audit(db, user_id=user_id, action="learning_question_update", resource_type="learning_question",
                resource_id=question_id, metadata={"status": status}, ip_address=ip_from(request), user_agent=ua_from(request))
    if body.answer and body.answer.strip():
        asker_email = await db.fetchval("SELECT email FROM users WHERE id=$1", str(question["asked_by"]))
        await send_learning_question_notification(
            asker_email, course_title=str(course.get("title") or "Course"), question=str(question["question"]), action="answered",
        )
        await emit_event(
            db, user_id=user_id, workspace_id=str(course["workspace_id"]),
            event_type="learning.question.answered", resource_type="learning_question",
            resource_id=question_id, payload={"course_id": course_id, "status": status},
        )
    return _question_response(row)


def _build_instructor_dashboard(
    course: dict, members: list[dict], lessons: list[dict], progress: list[dict],
    attempts: list[dict], assignments: list[dict], submissions: list[dict], questions: list[dict],
) -> dict:
    students = [member for member in members if member.get("persona") == "student"]
    lesson_count = len(lessons)
    progress_by_user: dict[str, list[dict]] = {}
    attempts_by_user: dict[str, list[dict]] = {}
    submissions_by_user: dict[str, list[dict]] = {}

    def is_overdue(value) -> bool:
        if not value:
            return False
        due = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        return due < datetime.now(timezone.utc)
    for row in progress:
        progress_by_user.setdefault(str(row.get("user_id")), []).append(row)
    for row in attempts:
        attempts_by_user.setdefault(str(row.get("user_id")), []).append(row)
    for row in submissions:
        submissions_by_user.setdefault(str(row.get("user_id")), []).append(row)
    published = [item for item in assignments if item.get("publication_status") != "draft"]
    now = datetime.now(timezone.utc)
    learner_rows = []
    for student in students:
        student_id = str(student["user_id"])
        learner_progress = progress_by_user.get(student_id, [])
        learner_attempts = attempts_by_user.get(student_id, [])
        learner_submissions = submissions_by_user.get(student_id, [])
        average_progress = round(sum(int(item.get("progress_pct") or 0) for item in learner_progress) / lesson_count) if lesson_count else 0
        correct = sum(int(item.get("correct_count") or 0) for item in learner_attempts)
        questions_total = sum(int(item.get("question_count") or 0) for item in learner_attempts)
        assessment_pct = round(correct / questions_total * 100) if questions_total else None
        submitted_ids = {str(item.get("assignment_id")) for item in learner_submissions if item.get("status") != "draft"}
        overdue = sum(
            1 for item in published
            if is_overdue(item.get("due_at"))
            and str(item["id"]) not in submitted_ids
        )
        risk_reasons = []
        if average_progress < 50:
            risk_reasons.append("Course progress is below 50%")
        if assessment_pct is not None and assessment_pct < int(course.get("domain_config", {}).get("passing_score", 80)):
            risk_reasons.append("Assessment performance is below the mastery threshold")
        if overdue:
            risk_reasons.append(f"{overdue} overdue assignment(s)")
        learner_rows.append({
            **student, "progress_pct": average_progress, "assessment_pct": assessment_pct,
            "engagement_seconds": sum(int(item.get("time_spent_seconds") or 0) for item in learner_progress),
            "submitted_assignments": len(submitted_ids), "assignment_count": len(published),
            "overdue_assignments": overdue, "at_risk": bool(risk_reasons), "risk_reasons": risk_reasons,
        })
    lesson_attempts: dict[str, list[dict]] = {}
    for attempt in attempts:
        if attempt.get("lesson_id") and int(attempt.get("question_count") or 0):
            lesson_attempts.setdefault(str(attempt["lesson_id"]), []).append(attempt)
    difficult = []
    for lesson in lessons:
        rows = lesson_attempts.get(str(lesson["id"]), [])
        correct = sum(int(item.get("correct_count") or 0) for item in rows)
        total = sum(int(item.get("question_count") or 0) for item in rows)
        score = round(correct / total * 100) if total else None
        if score is not None and score < int(course.get("domain_config", {}).get("passing_score", 80)):
            difficult.append({"lesson_id": str(lesson["id"]), "title": lesson["title"], "assessment_pct": score,
                              "attempt_count": len(rows), "competencies": _decode_json(lesson.get("competencies"), [])})
    embedded_assets = [item for item in course.get("assets", []) if item.get("status") == "embedded"]
    def lesson_has_evidence(lesson: dict) -> bool:
        return any(
            not asset.get("module_id")
            or (
                str(asset.get("module_id")) == str(lesson.get("module_id"))
                and (not asset.get("lesson_id") or str(asset.get("lesson_id")) == str(lesson.get("id")))
            )
            for asset in embedded_assets
        )
    content_gaps = [
        {"lesson_id": str(lesson["id"]), "title": lesson["title"], "gap": "No embedded lesson evidence"}
        for lesson in lessons if not lesson_has_evidence(lesson)
    ]
    open_questions = [item for item in questions if item.get("status") == "open"]
    completion_values = [item["progress_pct"] for item in learner_rows]
    assessment_values = [item["assessment_pct"] for item in learner_rows if item["assessment_pct"] is not None]
    engagement_values = [item["engagement_seconds"] for item in learner_rows]
    return {
        "course_id": str(course["id"]), "generated_at": now.isoformat(),
        "summary": {"student_count": len(students),
                    "cohort_progress_pct": round(sum(completion_values) / len(completion_values)) if completion_values else 0,
                    "assessment_performance_pct": round(sum(assessment_values) / len(assessment_values)) if assessment_values else None,
                    "average_engagement_seconds": round(sum(engagement_values) / len(engagement_values)) if engagement_values else 0,
                    "at_risk_count": sum(item["at_risk"] for item in learner_rows),
                    "open_question_count": len(open_questions), "assignment_count": len(published)},
        "learners": learner_rows, "difficult_concepts": difficult,
        "unanswered_questions": open_questions, "content_quality_gaps": content_gaps,
        "assignment_performance": [
            {"assignment_id": str(item["id"]), "title": item["title"],
             "submitted_count": sum(str(row.get("assignment_id")) == str(item["id"]) and row.get("status") != "draft" for row in submissions),
             "approved_count": sum(str(row.get("assignment_id")) == str(item["id"]) and row.get("status") == "approved" for row in submissions)}
            for item in published
        ],
    }


@router.get("/courses/{course_id}/instructor-dashboard")
async def get_instructor_dashboard(course_id: str, current_user: CurrentUser, db=Depends(get_db)):
    user_id = str(current_user["id"])
    course_access = await _course_access(db, course_id, user_id, manage=True)
    workspace = await _course_workspace(db, course_id, user_id)
    members = workspace["members"]
    lessons = [lesson for module in workspace["modules"] for lesson in module.get("lessons", [])]
    progress = [_jsonable(dict(row)) for row in await db.fetch(
        "SELECT * FROM learning_lesson_progress WHERE course_id=$1::uuid", course_id,
    )]
    attempts = [_quiz_attempt_response(row) for row in await db.fetch(
        """SELECT qa.*,a.lesson_id FROM learning_quiz_attempts qa
           JOIN learning_artifacts a ON a.id=qa.artifact_id WHERE qa.course_id=$1::uuid""", course_id,
    )]
    course_data = _course_response(course_access)
    course_data["assets"] = workspace["assets"]
    return _build_instructor_dashboard(
        course_data, members, lessons, progress, attempts,
        workspace.get("assignments", []), workspace.get("submissions", []), workspace["questions"],
    )


async def _course_workspace(db, course_id: str, user_id: str) -> dict:
    course = await _course_access(db, course_id, user_id)
    members = await db.fetch(
        """SELECT m.id,m.user_id,m.persona,m.created_at,u.email,u.full_name
           FROM learning_course_members m JOIN users u ON u.id=m.user_id
           WHERE m.course_id=$1 ORDER BY m.persona,u.full_name,u.email""", course_id,
    )
    module_rows = await db.fetch("SELECT * FROM learning_modules WHERE course_id=$1 ORDER BY position,created_at", course_id)
    lesson_rows = await db.fetch(
        """SELECT l.* FROM learning_lessons l JOIN learning_modules m ON m.id=l.module_id
           WHERE m.course_id=$1 ORDER BY m.position,l.position,l.created_at""", course_id,
    )
    lessons_by_module: dict[str, list] = {}
    for lesson in lesson_rows:
        item = _jsonable(dict(lesson))
        item["objectives"] = _decode_json(item.get("objectives"), [])
        item["competencies"] = _decode_json(item.get("competencies"), [])
        lessons_by_module.setdefault(str(lesson["module_id"]), []).append(item)
    modules = []
    for module in module_rows:
        item = _jsonable(dict(module))
        item["lessons"] = lessons_by_module.get(str(module["id"]), [])
        modules.append(item)
    assets = await db.fetch(
        """SELECT a.*,d.original_name,d.file_type,d.doc_type,d.status,d.chunk_count,
                  NULLIF(d.doc_metadata->'video_intelligence'->>'duration_seconds','')::double precision AS duration_seconds
           FROM learning_assets a JOIN documents d ON d.id=a.document_id
           WHERE a.course_id=$1 ORDER BY a.position,a.created_at""", course_id,
    )
    artifacts = await db.fetch(
        "SELECT * FROM learning_artifacts WHERE course_id=$1 AND user_id=$2 ORDER BY created_at DESC", course_id, user_id,
    )
    quiz_attempts = await db.fetch(
        "SELECT * FROM learning_quiz_attempts WHERE course_id=$1 AND user_id=$2 ORDER BY updated_at DESC",
        course_id, user_id,
    )
    lesson_progress = await db.fetch(
        "SELECT * FROM learning_lesson_progress WHERE course_id=$1 AND user_id=$2 ORDER BY updated_at DESC",
        course_id, user_id,
    )
    effective_persona = course.get("persona") or ("admin" if course.get("workspace_role") in ("owner", "editor") else "student")
    questions = await db.fetch(
        """SELECT q.*,asker.full_name AS asker_name,asker.email AS asker_email,
                  responder.full_name AS responder_name
           FROM learning_questions q JOIN users asker ON asker.id=q.asked_by
           LEFT JOIN users responder ON responder.id=q.answered_by
           WHERE q.course_id=$1 AND (q.asked_by=$2 OR q.assigned_to=$2 OR $3 IN ('admin','teacher','advisor'))
           ORDER BY q.created_at DESC""", course_id, user_id, effective_persona,
    )
    can_manage = bool(course.get("workspace_role") in ("owner", "editor") or course.get("persona") in MANAGER_PERSONAS)
    assignments = await db.fetch(
        """SELECT a.*,m.title AS module_title,l.title AS lesson_title
           FROM learning_assignments a
           LEFT JOIN learning_modules m ON m.id=a.module_id
           LEFT JOIN learning_lessons l ON l.id=a.lesson_id
           WHERE a.course_id=$1::uuid AND ($2::boolean OR a.publication_status<>'draft')
           ORDER BY a.due_at NULLS LAST,a.created_at DESC""", course_id, can_manage,
    )
    submissions = await db.fetch(
        """SELECT s.*,u.email,u.full_name,a.title AS assignment_title,a.max_score
           FROM learning_submissions s JOIN users u ON u.id=s.user_id
           JOIN learning_assignments a ON a.id=s.assignment_id
           WHERE s.course_id=$1::uuid AND ($2::boolean OR s.user_id=$3::uuid)
           ORDER BY s.updated_at DESC""", course_id, can_manage, user_id,
    )
    revisions = await db.fetch(
        """SELECT r.* FROM learning_submission_revisions r
           JOIN learning_submissions s ON s.id=r.submission_id
           WHERE s.course_id=$1::uuid AND ($2::boolean OR s.user_id=$3::uuid)
           ORDER BY r.submission_id,r.revision_number DESC""", course_id, can_manage, user_id,
    )
    revisions_by_submission: dict[str, list[dict]] = {}
    for revision in revisions:
        item = _jsonable(dict(revision))
        item["document_ids"] = _decode_json(item.get("document_ids"), [])
        revisions_by_submission.setdefault(str(revision["submission_id"]), []).append(item)
    result = _course_response(course)
    result.update({
        "my_persona": effective_persona,
        "can_manage": can_manage,
        "members": [_jsonable(dict(row)) for row in members],
        "modules": modules,
        "assets": [_jsonable(dict(row)) for row in assets],
        "artifacts": [_jsonable(dict(row)) for row in artifacts],
        "quiz_attempts": [_quiz_attempt_response(row) for row in quiz_attempts],
        "lesson_progress": [_jsonable(dict(row)) for row in lesson_progress],
        "questions": [_question_response(row) for row in questions],
        "assignments": [_assignment_response(row) for row in assignments],
        "submissions": [
            {**_submission_response(row), "revisions": revisions_by_submission.get(str(row["id"]), [])}
            for row in submissions
        ],
    })
    return result


def _course_response(row) -> dict:
    result = _jsonable(dict(row))
    result["objectives"] = _decode_json(result.get("objectives"), [])
    result["domain_config"] = _decode_json(result.get("domain_config"), {})
    domain = result.get("domain") or "general"
    result["domain"] = domain
    result["domain_pack"] = {**DOMAIN_PACKS.get(domain, DOMAIN_PACKS["general"]), **result["domain_config"]}
    return result


def _question_response(row) -> dict:
    result = _jsonable(dict(row))
    result["context"] = _decode_json(result.get("context"), {})
    return result


def _quiz_attempt_response(row) -> dict:
    result = _jsonable(dict(row))
    result["answers"] = _decode_json(result.get("answers"), {})
    result["result"] = _decode_json(result.get("result"), {})
    return result


def _build_mastery_projection(
    course: dict, modules: list[dict], lessons: list[dict], progress: list[dict],
    attempts: list[dict], learner: dict,
) -> dict:
    passing_score = max(0, min(100, int((course.get("domain_config") or {}).get("passing_score", 80))))
    progress_by_lesson = {str(row["lesson_id"]): row for row in progress}
    attempts_by_lesson: dict[str, list[dict]] = {}
    for attempt in attempts:
        if (
            attempt.get("lesson_id")
            and attempt.get("completed")
            and int(attempt.get("question_count") or 0) > 0
        ):
            attempts_by_lesson.setdefault(str(attempt["lesson_id"]), []).append(attempt)

    lessons_by_module: dict[str, list[dict]] = {}
    assessed_correct = 0
    assessed_questions = 0
    completed_lessons = 0
    mastered_lessons = 0
    progress_total = 0
    recommendations: list[dict] = []

    for lesson in lessons:
        lesson_id = str(lesson["id"])
        lesson_progress = progress_by_lesson.get(lesson_id, {})
        lesson_attempts = attempts_by_lesson.get(lesson_id, [])
        correct_count = sum(int(item.get("correct_count") or 0) for item in lesson_attempts)
        question_count = sum(int(item.get("question_count") or 0) for item in lesson_attempts)
        score = round((correct_count / question_count) * 100) if question_count else None
        status = str(lesson_progress.get("status") or "not_started")
        progress_pct = int(lesson_progress.get("progress_pct") or 0)
        mastery_status = (
            "mastered" if score is not None and score >= passing_score
            else "developing" if score is not None
            else "not_assessed"
        )
        objectives = _clean_list(_decode_json(lesson.get("objectives"), []))
        competency_names = _clean_list(_decode_json(lesson.get("competencies"), []))
        evidence = [
            {
                "artifact_id": str(item.get("artifact_id") or ""),
                "artifact_title": item.get("artifact_title") or "Practice questions",
                "correct_count": int(item.get("correct_count") or 0),
                "question_count": int(item.get("question_count") or 0),
                "completed": bool(item.get("completed")),
                "updated_at": item.get("updated_at"),
            }
            for item in lesson_attempts
        ]
        lesson_result = {
            **lesson,
            "id": lesson_id,
            "status": status,
            "progress_pct": progress_pct,
            "time_spent_seconds": int(lesson_progress.get("time_spent_seconds") or 0),
            "last_position_seconds": lesson_progress.get("last_position_seconds"),
            "assessment_score": score,
            "mastery_status": mastery_status,
            "passing_score": passing_score,
            "objectives": objectives,
            "competencies": [
                {"name": name, "score": score, "status": mastery_status, "evidence": evidence}
                for name in competency_names
            ],
            "assessment_evidence": evidence,
        }
        lessons_by_module.setdefault(str(lesson["module_id"]), []).append(lesson_result)
        completed_lessons += int(status == "completed")
        mastered_lessons += int(mastery_status == "mastered")
        progress_total += progress_pct
        assessed_correct += correct_count
        assessed_questions += question_count

        recommendation = None
        if score is not None and score < passing_score:
            recommendation = {
                "priority": 1, "type": "review_and_retake", "lesson_id": lesson_id,
                "module_id": str(lesson["module_id"]), "title": f"Review {lesson['title']}",
                "reason": f"Assessment score {score}% is below the {passing_score}% mastery threshold.",
                "action": "Review cited lesson evidence, ask the AI Tutor about missed concepts, and retake the practice questions.",
            }
        elif status == "in_progress":
            recommendation = {
                "priority": 2, "type": "continue_lesson", "lesson_id": lesson_id,
                "module_id": str(lesson["module_id"]), "title": f"Continue {lesson['title']}",
                "reason": f"Lesson progress is {progress_pct}%.",
                "action": "Continue from the saved position and complete the lesson activities.",
            }
        elif status == "not_started":
            recommendation = {
                "priority": 3, "type": "start_lesson", "lesson_id": lesson_id,
                "module_id": str(lesson["module_id"]), "title": f"Start {lesson['title']}",
                "reason": "This is the next incomplete curriculum lesson.",
                "action": "Open the lesson, review its learning objectives, and begin the attached content.",
            }
        elif score is None:
            recommendation = {
                "priority": 4, "type": "assess_mastery", "lesson_id": lesson_id,
                "module_id": str(lesson["module_id"]), "title": f"Assess {lesson['title']}",
                "reason": "The lesson is complete but has no graded mastery evidence.",
                "action": "Generate and complete practice questions for this lesson.",
            }
        if recommendation:
            recommendations.append(recommendation)
    module_results = []
    for module in modules:
        module_lessons = lessons_by_module.get(str(module["id"]), [])
        module_results.append({
            **module,
            "id": str(module["id"]),
            "lesson_count": len(module_lessons),
            "completed_lessons": sum(item["status"] == "completed" for item in module_lessons),
            "progress_pct": round(sum(item["progress_pct"] for item in module_lessons) / len(module_lessons)) if module_lessons else 0,
            "mastery_pct": (
                round(sum(item["assessment_score"] for item in module_lessons if item["assessment_score"] is not None)
                      / sum(item["assessment_score"] is not None for item in module_lessons))
                if any(item["assessment_score"] is not None for item in module_lessons) else None
            ),
            "lessons": module_lessons,
        })

    lesson_count = len(lessons)
    mastery_pct = round((assessed_correct / assessed_questions) * 100) if assessed_questions else None
    recommendations.sort(key=lambda item: (item["priority"], next(
        (int(lesson.get("position") or 0) for lesson in lessons if str(lesson["id"]) == item["lesson_id"]), 0,
    )))
    if not recommendations and lesson_count:
        recommendations.append({
            "priority": 5, "type": "course_review", "title": "Review completed course",
            "reason": "All lessons are complete and current assessments meet the mastery threshold.",
            "action": "Review key concepts or ask the AI Tutor for a cumulative course check.",
        })
    return {
        "course_id": str(course["id"]),
        "learner": learner,
        "passing_score": passing_score,
        "summary": {
            "lesson_count": lesson_count,
            "completed_lessons": completed_lessons,
            "completion_pct": round((completed_lessons / lesson_count) * 100) if lesson_count else 0,
            "progress_pct": round(progress_total / lesson_count) if lesson_count else 0,
            "assessed_lessons": len(attempts_by_lesson),
            "mastered_lessons": mastered_lessons,
            "mastery_pct": mastery_pct,
        },
        "modules": module_results,
        "recommendations": recommendations[:5],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _clean_list(values) -> list[str]:
    return [str(value).strip() for value in (values or []) if str(value).strip()]


def _decode_json(value: Any, default):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value if value is not None else default


def _jsonable(value: Any):
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value
