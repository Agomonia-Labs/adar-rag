from __future__ import annotations

import json
from datetime import date, datetime
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

router = APIRouter()

PERSONAS = {"admin", "teacher", "student", "advisor"}
MANAGER_PERSONAS = {"admin", "teacher"}
ARTIFACT_TYPES = {"summary", "study_guide", "key_concepts", "flashcards", "practice_questions"}


class CourseCreate(BaseModel):
    workspace_id: str
    title: str = Field(min_length=1, max_length=180)
    course_code: str = Field(default="", max_length=60)
    semester: str = Field(default="", max_length=80)
    description: str = Field(default="", max_length=4000)
    instructor_name: str = Field(default="", max_length=180)
    objectives: list[str] = []


class CourseUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=180)
    course_code: str | None = Field(default=None, max_length=60)
    semester: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=4000)
    instructor_name: str | None = Field(default=None, max_length=180)
    objectives: list[str] | None = None
    status: Literal["active", "archived"] | None = None


class MemberCreate(BaseModel):
    email: str
    persona: Literal["admin", "teacher", "student", "advisor"] = "student"


class CurriculumSave(BaseModel):
    modules: list[dict] = []


class AssetCreate(BaseModel):
    document_id: str
    module_id: str | None = None
    lesson_id: str | None = None
    title: str = ""


class ArtifactCreate(BaseModel):
    artifact_type: Literal["summary", "study_guide", "key_concepts", "flashcards", "practice_questions"]
    title: str = ""
    content: str = Field(min_length=1)
    source_document_ids: list[str] = []
    module_id: str | None = None
    lesson_id: str | None = None


class QuizAttemptCreate(BaseModel):
    answers: dict[str, list[str]] = {}
    replace: bool = False


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
    context: dict = {}


class QuestionUpdate(BaseModel):
    answer: str | None = None
    status: Literal["open", "answered", "closed"] | None = None
    assigned_to: str | None = None


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
            """SELECT l.id,l.title,l.description,l.module_id,m.title AS module_title
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
        focus = (
            f" Selected lesson title: {lesson['title']}."
            f" Selected lesson description: {lesson['description'] or 'Not provided'}."
            " Retrieve and answer only evidence semantically relevant to this lesson, even when an attached file also contains broader module content."
            " Do not summarize the entire module."
        )
    elif module:
        focus = (
            f" Selected module title: {module['title']}."
            f" Selected module description: {module['description'] or 'Not provided'}."
        )
    return {
        "course_id": course_id,
        "workspace_id": str(course["workspace_id"]),
        "module_id": module_id,
        "lesson_id": lesson_id,
        "scope_type": scope_type,
        "label": label,
        "instruction": f"Learning scope: {label}. {boundary}{focus} Ground every claim in the selected course evidence.",
        "document_ids": [str(row["document_id"]) for row in rows],
        "assets": [_jsonable(dict(row)) for row in rows],
    }


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
               (workspace_id,created_by,title,course_code,semester,description,instructor_name,objectives)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb) RETURNING *""",
            body.workspace_id, user_id, body.title.strip(), body.course_code.strip(), body.semester.strip(),
            body.description.strip(), body.instructor_name.strip(), json.dumps(_clean_list(body.objectives)),
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
    allowed = {"title", "course_code", "semester", "description", "instructor_name", "objectives", "status"}
    sets, params = [], [course_id]
    for key, value in values.items():
        if key not in allowed:
            continue
        params.append(json.dumps(_clean_list(value)) if key == "objectives" else value)
        cast = "::jsonb" if key == "objectives" else ""
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
                            """UPDATE learning_lessons SET title=$3,description=$4,position=$5,updated_at=NOW()
                               WHERE id=$1::uuid AND module_id=$2::uuid RETURNING id""",
                            lesson_id, saved_module_id, lesson_title,
                            str(lesson.get("description") or "").strip(), lesson_position,
                        )
                        if not lesson_row:
                            raise HTTPException(400, "Lesson does not belong to its selected module")
                    else:
                        lesson_row = await db.fetchrow(
                            """INSERT INTO learning_lessons (module_id,title,description,position)
                               VALUES ($1,$2,$3,$4) RETURNING id""",
                            saved_module_id, lesson_title,
                            str(lesson.get("description") or "").strip(), lesson_position,
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
    await db.execute(
        """INSERT INTO learning_assets (course_id,module_id,lesson_id,document_id,title,added_by)
           VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (course_id,document_id)
           DO UPDATE SET module_id=EXCLUDED.module_id,lesson_id=EXCLUDED.lesson_id,title=EXCLUDED.title""",
        course_id, module_id, lesson_id, body.document_id, body.title.strip() or doc["original_name"], user_id,
    )
    await emit_event(
        db, user_id=user_id, workspace_id=str(course["workspace_id"]),
        event_type="learning.content.attached", resource_type="learning_course",
        resource_id=course_id, payload={"document_id": body.document_id, "module_id": module_id, "lesson_id": lesson_id},
    )
    return await _course_workspace(db, course_id, user_id)


@router.delete("/courses/{course_id}/assets/{asset_id}")
async def remove_asset(course_id: str, asset_id: str, current_user: CurrentUser, db=Depends(get_db)):
    await _course_access(db, course_id, str(current_user["id"]), manage=True)
    await db.execute("DELETE FROM learning_assets WHERE id=$1::uuid AND course_id=$2::uuid", asset_id, course_id)
    return {"ok": True}


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
        """SELECT id,content FROM learning_artifacts
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
        lessons_by_module.setdefault(str(lesson["module_id"]), []).append(_jsonable(dict(lesson)))
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
    effective_persona = course.get("persona") or ("admin" if course.get("workspace_role") in ("owner", "editor") else "student")
    questions = await db.fetch(
        """SELECT q.*,asker.full_name AS asker_name,asker.email AS asker_email,
                  responder.full_name AS responder_name
           FROM learning_questions q JOIN users asker ON asker.id=q.asked_by
           LEFT JOIN users responder ON responder.id=q.answered_by
           WHERE q.course_id=$1 AND (q.asked_by=$2 OR q.assigned_to=$2 OR $3 IN ('admin','teacher','advisor'))
           ORDER BY q.created_at DESC""", course_id, user_id, effective_persona,
    )
    result = _course_response(course)
    result.update({
        "my_persona": effective_persona,
        "can_manage": bool(course.get("workspace_role") in ("owner", "editor") or course.get("persona") in MANAGER_PERSONAS),
        "members": [_jsonable(dict(row)) for row in members],
        "modules": modules,
        "assets": [_jsonable(dict(row)) for row in assets],
        "artifacts": [_jsonable(dict(row)) for row in artifacts],
        "quiz_attempts": [_quiz_attempt_response(row) for row in quiz_attempts],
        "questions": [_question_response(row) for row in questions],
    })
    return result


def _course_response(row) -> dict:
    result = _jsonable(dict(row))
    result["objectives"] = _decode_json(result.get("objectives"), [])
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
