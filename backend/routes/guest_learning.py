# routes/guest_learning.py
#
# Public, no-login access to ONE fixed ADAR Knowledge Academy course, at an
# access level equivalent to the real "student" persona / workspace "viewer"
# role. Modeled directly on routes/guest.py (the existing sanctioned
# no-login DocIntel "Guest Preview" pattern) and reuses the real learning
# access-control and business logic from routes/learning.py rather than
# re-implementing it, so a guest visitor is bound by exactly the same rules
# a real enrolled student is.
#
# Isolation model:
#   - Every guest session gets its own ephemeral `users` row (role
#     'guest_learner', unguessable random password hash -> cannot log in
#     through /api/auth/login; there is no email-verification/login path
#     exposed for it anywhere).
#   - That ephemeral user is enrolled as workspace role 'viewer' and course
#     persona 'student' in ONLY the single fixed GUEST_LEARNING_COURSE_ID
#     course/workspace. It has no access to any other course or workspace.
#   - All reuse of routes.learning's _course_access / _resolve_learning_scope
#     therefore naturally enforces real student-level RBAC -- this router
#     never elevates privileges and never calls those functions with
#     manage=True.
#   - Session token -> guest_learning_sessions row, same hashed-token
#     pattern as guest_sessions. Expiring a session cascades (via FK
#     ON DELETE CASCADE on users.id) through workspace_members,
#     learning_course_members, learning_artifacts, learning_quiz_attempts,
#     and learning_lesson_progress, so cleanup is a single user delete.
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from auth.dependencies import AdminUser
from auth.service import hash_password
from database.connection import get_db, get_pool
from routes.learning import (
    ARTIFACT_TYPES,
    PracticeQuiz,
    LessonProgressUpdate,
    SubmissionUpsert,
    _assignment_response,
    _build_mastery_projection,
    _course_access,
    _course_calendar_workspace,
    _course_response,
    _decode_json,
    _grade_practice_quiz,
    _jsonable,
    _normalize_artifact_content,
    _quiz_attempt_response,
    _resolve_learning_scope,
    _submission_response,
    _validate_course_documents,
)
from services.audit import audit, ip_from, ua_from
from services.language import response_language_instruction
from services.llm import chat_stream, embed_query, rag_system
from services.pii import redact_text
from services.reranker import RERANK_ENABLED, rerank
from services.vectordb import RERANK_FETCH_K, TOP_K, find_similar
import services.storage as gcs

router = APIRouter()

# ── Configuration (env-overridable; separate from routes/guest.py's own knobs) ──
GUEST_LEARNING_COURSE_ID = os.getenv("GUEST_LEARNING_COURSE_ID", "")
GUEST_LEARNING_TTL_HOURS = int(os.getenv("GUEST_LEARNING_TTL_HOURS", "24"))
GUEST_LEARNING_MAX_TUTOR_QUERIES = int(os.getenv("GUEST_LEARNING_MAX_TUTOR_QUERIES", "12"))
GUEST_LEARNING_MAX_ARTIFACTS = int(os.getenv("GUEST_LEARNING_MAX_ARTIFACTS", "6"))
_FETCH_K = RERANK_FETCH_K if RERANK_ENABLED else TOP_K

# Server-side generation prompts, mirroring frontend/src/components/LearningPanel.jsx's
# STUDY_PROMPTS exactly. Kept server-side (not client-supplied) so a guest visitor can
# never inject an arbitrary generation prompt through this public endpoint.
STUDY_PROMPTS: dict[str, str] = {
    "summary": "Create a concise lesson summary covering the central ideas and important evidence in the selected course content.",
    "study_guide": "Create a structured study guide with learning objectives, major topics, explanations, examples, and review checkpoints.",
    "key_concepts": (
        "Identify the key concepts using only the selected course content. Format every concept in readable Markdown with this structure:\n"
        "## Concept name\n**Definition:** A clear explanation.\n**Why it matters:** Its significance in the course.\n"
        "**Review question:** A question that checks understanding.\n**Answer:** A concise grounded answer.\n"
        "Use separate sections, short paragraphs, and source citations where available."
    ),
    "flashcards": (
        "Create exactly 12 concise flashcards based only on the selected course content. Use this exact plain-text format for every card, with no table:\n"
        "FLASHCARD 1\nQUESTION: A clear question\nANSWER: A concise grounded answer\n"
        "Continue through FLASHCARD 12. Keep each answer focused enough to review as a short tutor response."
    ),
    "practice_questions": (
        'Create exactly 4 grounded practice questions that progress from recall to application. Return one compact JSON object only, '
        "without Markdown, citations outside JSON, commentary, or code fences, using this exact structure:\n"
        '{"schema_version":1,"instructions":"Select every correct answer, then submit each question for immediate feedback.",'
        '"questions":[{"id":"q1","question":"Question text","options":[{"id":"A","text":"Option text","correct":true},'
        '{"id":"B","text":"Option text","correct":false},{"id":"C","text":"Option text","correct":false},'
        '{"id":"D","text":"Option text","correct":false}],"explanation":"Explain why the correct answer or answers are correct using the course material."}]}\n'
        "Every question must have exactly four distinct options labeled A, B, C, and D. One or more options may be correct. "
        "Keep every option under 18 words and every explanation to one concise sentence. Escape quotes inside JSON strings. "
        "Do not include facts that are unsupported by the selected course content."
    ),
}


class GuestLearningSessionResponse(BaseModel):
    guest_token: str
    guest_session_id: str
    course_id: str
    expires_at: str
    max_tutor_queries: int
    max_artifacts: int


class GuestTutorRequest(BaseModel):
    question: str
    module_id: str | None = None
    lesson_id: str | None = None
    history: list[dict] = []
    response_language: str | None = None
    redact_pii: bool = False


class GuestArtifactRequest(BaseModel):
    artifact_type: str
    module_id: str | None = None
    lesson_id: str | None = None
    title: str = ""
    response_language: str | None = None


class GuestQuizAttemptRequest(BaseModel):
    answers: dict[str, list[str]]
    replace: bool = False


def _require_course_configured() -> str:
    if not GUEST_LEARNING_COURSE_ID:
        raise HTTPException(503, "The public Knowledge Academy demo is not configured yet (GUEST_LEARNING_COURSE_ID unset).")
    return GUEST_LEARNING_COURSE_ID


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def _require_guest_learner(db, guest_token: str | None) -> dict:
    if not guest_token:
        raise HTTPException(401, "Guest learning session is required")
    row = await db.fetchrow(
        """
        SELECT *
          FROM guest_learning_sessions
         WHERE token_hash=$1
           AND expires_at > NOW()
        """,
        _token_hash(guest_token),
    )
    if not row:
        raise HTTPException(401, "Guest learning session expired or invalid. Start a new session.")
    return dict(row)


@router.post("/session", response_model=GuestLearningSessionResponse)
async def create_guest_learning_session(request: Request, db=Depends(get_db)):
    course_id = _require_course_configured()
    course = await db.fetchrow(
        "SELECT id, workspace_id, publication_status FROM learning_courses WHERE id=$1::uuid",
        course_id,
    )
    if not course:
        raise HTTPException(503, "The public Knowledge Academy demo course is not available right now.")

    token = secrets.token_urlsafe(36)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=GUEST_LEARNING_TTL_HOURS)
    guest_email = f"guest-learner-{secrets.token_hex(12)}@guest.docintel.local"

    async with db.transaction():
        user_row = await db.fetchrow(
            """
            INSERT INTO users (email, hashed_password, full_name, role, is_verified)
            VALUES ($1,$2,'Knowledge Academy Guest','guest_learner',TRUE)
            RETURNING id
            """,
            guest_email,
            hash_password(secrets.token_urlsafe(32)),
        )
        guest_user_id = str(user_row["id"])

        # Viewer-level workspace membership: the minimum role that satisfies
        # _course_access's required workspace_members join, matching the
        # user's own "equivalent access of viewer role" instruction.
        await db.execute(
            """
            INSERT INTO workspace_members (workspace_id, user_id, role)
            VALUES ($1::uuid,$2::uuid,'viewer')
            ON CONFLICT (workspace_id, user_id) DO NOTHING
            """,
            str(course["workspace_id"]),
            guest_user_id,
        )
        # Student persona in exactly this one course -- no other course
        # membership is ever created for this user.
        await db.execute(
            """
            INSERT INTO learning_course_members (course_id, user_id, persona)
            VALUES ($1::uuid,$2::uuid,'student')
            ON CONFLICT (course_id, user_id) DO NOTHING
            """,
            course_id,
            guest_user_id,
        )
        session_row = await db.fetchrow(
            """
            INSERT INTO guest_learning_sessions (token_hash, guest_user_id, course_id, expires_at)
            VALUES ($1,$2::uuid,$3::uuid,$4)
            RETURNING id, expires_at
            """,
            _token_hash(token),
            guest_user_id,
            course_id,
            expires_at,
        )

    await audit(
        db,
        user_id=guest_user_id,
        action="guest_learning_session_create",
        resource_type="guest_learning_session",
        resource_id=str(session_row["id"]),
        ip_address=ip_from(request),
        user_agent=ua_from(request),
    )
    return {
        "guest_token": token,
        "guest_session_id": str(session_row["id"]),
        "course_id": course_id,
        "expires_at": session_row["expires_at"].isoformat(),
        "max_tutor_queries": GUEST_LEARNING_MAX_TUTOR_QUERIES,
        "max_artifacts": GUEST_LEARNING_MAX_ARTIFACTS,
    }


@router.get("/course")
async def get_guest_course(db=Depends(get_db)):
    """Public, unauthenticated curriculum read -- no guest token required.
    Lets the adar-web page render the module/lesson browser before a
    visitor starts a session. Returns only published, student-safe fields,
    plus enough asset metadata (a lesson's video document_id, a module's
    attached PDF/DOC materials) for the page to render clickable citations
    and a course-materials list without needing an authenticated call."""
    course_id = _require_course_configured()
    course = await db.fetchrow(
        """SELECT id, title, course_code, description, instructor_name, objectives, domain
             FROM learning_courses WHERE id=$1::uuid AND publication_status='published'""",
        course_id,
    )
    if not course:
        raise HTTPException(503, "The public Knowledge Academy demo course is not published yet.")
    modules = await db.fetch(
        "SELECT id, title, description, position FROM learning_modules WHERE course_id=$1::uuid ORDER BY position",
        course_id,
    )
    module_ids = [str(m["id"]) for m in modules]
    lessons = await db.fetch(
        "SELECT id, module_id, title, description, position FROM learning_lessons WHERE module_id = ANY($1::uuid[]) ORDER BY position",
        module_ids,
    ) if module_ids else []
    lessons_by_module: dict[str, list[dict]] = {}
    for lesson in lessons:
        lessons_by_module.setdefault(str(lesson["module_id"]), []).append(
            {"id": str(lesson["id"]), "title": lesson["title"], "description": lesson["description"]}
        )

    assets = await db.fetch(
        """SELECT la.module_id, la.lesson_id, la.document_id, la.title, d.file_type, d.original_name
             FROM learning_assets la JOIN documents d ON d.id = la.document_id
            WHERE la.course_id=$1::uuid""",
        course_id,
    ) if module_ids else []
    video_doc_by_lesson: dict[str, str] = {}
    materials_by_module: dict[str, list[dict]] = {}
    for a in assets:
        ftype = a["file_type"]
        if a["lesson_id"] and ftype == "video":
            video_doc_by_lesson[str(a["lesson_id"])] = str(a["document_id"])
        elif a["module_id"] and ftype in ("pdf", "docx"):
            materials_by_module.setdefault(str(a["module_id"]), []).append({
                "document_id": str(a["document_id"]),
                "title": a["title"] or a["original_name"],
                "file_type": ftype,
            })

    return {
        "id": str(course["id"]),
        "title": course["title"],
        "course_code": course["course_code"],
        "description": course["description"],
        "instructor_name": course["instructor_name"],
        "objectives": json.loads(course["objectives"]) if isinstance(course["objectives"], str) else (course["objectives"] or []),
        "modules": [
            {
                "id": str(m["id"]),
                "title": m["title"],
                "description": m["description"],
                "materials": materials_by_module.get(str(m["id"]), []),
                "lessons": [
                    {**lesson, "video_document_id": video_doc_by_lesson.get(lesson["id"])}
                    for lesson in lessons_by_module.get(str(m["id"]), [])
                ],
            }
            for m in modules
        ],
    }


@router.get("/materials/{document_id}/view-url")
async def get_guest_material_url(
    document_id: str,
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
    db=Depends(get_db),
):
    """Same pattern as the authenticated app's document/evidence viewer
    (routes.documents' /{doc_id}/view-url and routes.learning's
    /courses/{course_id}/evidence/{document_id}/view-url): a short-lived
    signed GCS URL for the original file, opened directly by the browser's
    native PDF viewer -- scoped here to only documents actually attached to
    this guest's single public course."""
    session = await _require_guest_learner(db, x_guest_token)
    course_id = str(session["course_id"])
    row = await db.fetchrow(
        """SELECT d.id, d.original_name, d.file_type, d.gcs_source_path
             FROM documents d JOIN learning_assets a ON a.document_id = d.id
            WHERE a.course_id=$1::uuid AND d.id=$2::uuid LIMIT 1""",
        course_id, document_id,
    )
    if not row:
        raise HTTPException(404, "This material is not attached to the public course")
    if not row["gcs_source_path"]:
        raise HTTPException(409, "The original file is not available in storage")
    return {
        "document_id": document_id, "filename": row["original_name"], "file_type": row["file_type"],
        "url": await gcs.get_signed_url(row["gcs_source_path"]),
        "expires_in_seconds": int(os.getenv("GCS_SIGNED_URL_EXPIRY_SECONDS", "3600")),
    }


@router.get("/scope")
async def get_guest_scope(
    module_id: str | None = None,
    lesson_id: str | None = None,
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
    db=Depends(get_db),
):
    session = await _require_guest_learner(db, x_guest_token)
    return await _resolve_learning_scope(db, str(session["course_id"]), str(session["guest_user_id"]), module_id, lesson_id)


@router.get("/calendar")
async def get_guest_calendar(
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
    db=Depends(get_db),
):
    """Read-only course calendar -- announcements, deadlines, and published
    assignment due dates -- reusing the exact same query the authenticated
    app's Calendar tab uses (routes.learning's _course_calendar_workspace).
    A guest is always enrolled as a 'student' persona / 'viewer' workspace
    role, so that function naturally reports can_manage_calendar=False and
    this endpoint needs no extra gating to stay read-only."""
    session = await _require_guest_learner(db, x_guest_token)
    course_id = str(session["course_id"])
    user_id = str(session["guest_user_id"])
    course = await _course_access(db, course_id, user_id)
    return await _course_calendar_workspace(db, course_id, course, user_id)


@router.get("/assignments")
async def list_guest_assignments(
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
    db=Depends(get_db),
):
    """Published assignments/projects plus this guest's own submissions --
    mirrors the authenticated student view (never draft assignments, never
    another learner's work)."""
    session = await _require_guest_learner(db, x_guest_token)
    course_id = str(session["course_id"])
    user_id = str(session["guest_user_id"])
    await _course_access(db, course_id, user_id)
    assignment_rows = await db.fetch(
        """SELECT a.*,m.title AS module_title,l.title AS lesson_title
             FROM learning_assignments a
             LEFT JOIN learning_modules m ON m.id=a.module_id
             LEFT JOIN learning_lessons l ON l.id=a.lesson_id
            WHERE a.course_id=$1::uuid AND a.publication_status<>'draft'
            ORDER BY a.due_at NULLS LAST,a.created_at DESC""",
        course_id,
    )
    submission_rows = await db.fetch(
        "SELECT s.* FROM learning_submissions s WHERE s.course_id=$1::uuid AND s.user_id=$2::uuid",
        course_id, user_id,
    )
    return {
        "assignments": [_assignment_response(row) for row in assignment_rows],
        "submissions": [_submission_response(row) for row in submission_rows],
    }


@router.put("/assignments/{assignment_id}/submission")
async def save_guest_assignment_submission(
    assignment_id: str,
    body: SubmissionUpsert,
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
    db=Depends(get_db),
):
    """Same save/submit flow as the authenticated student view (PUT
    .../assignments/{id}/submission in routes/learning.py), scoped to this
    guest's own ephemeral user id -- a fresh guest session starts a fresh
    submission history, exactly like a new student enrollment would. AI
    evaluation and instructor review stay staff-only (manage=True) in the
    real app, so they are intentionally not exposed here -- a guest sees
    the same "submitted, awaiting review" state a real student would."""
    session = await _require_guest_learner(db, x_guest_token)
    course_id = str(session["course_id"])
    user_id = str(session["guest_user_id"])
    course = await _course_access(db, course_id, user_id)
    assignment = await db.fetchrow(
        "SELECT * FROM learning_assignments WHERE id=$1::uuid AND course_id=$2::uuid", assignment_id, course_id,
    )
    if not assignment or assignment["publication_status"] == "draft":
        raise HTTPException(404, "Assignment not found")
    if assignment["publication_status"] == "closed":
        raise HTTPException(409, "This assignment is closed")
    document_ids = list(dict.fromkeys(
        body.document_ids + ([body.presentation_document_id] if body.presentation_document_id else [])
    ))
    await _validate_course_documents(db, str(course["workspace_id"]), document_ids)
    existing = await db.fetchrow(
        "SELECT * FROM learning_submissions WHERE assignment_id=$1::uuid AND user_id=$2::uuid", assignment_id, user_id,
    )
    if existing and existing["status"] in {"submitted", "in_review"}:
        raise HTTPException(409, "The submission is under review; wait for instructor feedback")
    if existing and existing["status"] == "approved":
        raise HTTPException(409, "The approved submission is final")
    revision_number = int(existing["revision_number"] or 1) if existing else 1
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
    return _submission_response(row)


@router.put("/lessons/{lesson_id}/progress")
async def update_guest_lesson_progress(
    lesson_id: str,
    body: LessonProgressUpdate,
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
    db=Depends(get_db),
):
    """Same lesson-progress upsert as the authenticated app (PUT
    .../lessons/{id}/progress in routes/learning.py) -- lets this guest's
    own Progress & Mastery tab show real completion state, scoped to this
    guest session only."""
    session = await _require_guest_learner(db, x_guest_token)
    course_id = str(session["course_id"])
    user_id = str(session["guest_user_id"])
    await _course_access(db, course_id, user_id)
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
    return _jsonable(dict(row))


@router.get("/mastery")
async def get_guest_mastery(
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
    db=Depends(get_db),
):
    """Single-learner mastery projection for this guest, reusing the exact
    same computation as the authenticated student's own Progress & Mastery
    tab (routes.learning's _build_mastery_projection). A guest can only
    ever see their own projection -- there is no learner_id override here,
    unlike the authenticated instructor/advisor review view."""
    session = await _require_guest_learner(db, x_guest_token)
    course_id = str(session["course_id"])
    user_id = str(session["guest_user_id"])
    course = await _course_access(db, course_id, user_id)
    member = await db.fetchrow(
        """SELECT u.id,u.email,u.full_name,m.persona FROM learning_course_members m
             JOIN users u ON u.id=m.user_id
            WHERE m.course_id=$1::uuid AND m.user_id=$2::uuid""",
        course_id, user_id,
    )
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
        course_id, user_id,
    )
    attempts = await db.fetch(
        """SELECT qa.*,a.module_id,a.lesson_id,a.title AS artifact_title
             FROM learning_quiz_attempts qa JOIN learning_artifacts a ON a.id=qa.artifact_id
            WHERE qa.course_id=$1::uuid AND qa.user_id=$2::uuid""",
        course_id, user_id,
    )
    return _build_mastery_projection(
        _course_response(course), [_jsonable(dict(row)) for row in modules],
        [_jsonable(dict(row)) for row in lessons], [_jsonable(dict(row)) for row in progress],
        [_quiz_attempt_response(row) for row in attempts], _jsonable(dict(member)),
    )


def _chunk_meta(chunk: dict) -> dict:
    """document_chunks.chunk_metadata comes back from asyncpg as a JSON
    string (no codec registered on the pool) -- parse it defensively."""
    meta = chunk.get("chunk_metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except json.JSONDecodeError:
            meta = {}
    return meta if isinstance(meta, dict) else {}


def _public_source(chunk: dict, index: int) -> dict:
    """Shape a retrieved chunk into a citation-safe, front-end-ready source:
    enough to render a numbered citation and, for a video chunk, seek the
    right <video> element to the moment the answer actually came from --
    without leaking the full chunk content or embedding."""
    meta = _chunk_meta(chunk)
    is_video = meta.get("file_type") == "video" or str(meta.get("chunk_type") or "").startswith("video_")
    source = {
        "source_number": index + 1,
        "document_id": str(chunk.get("document_id") or ""),
        "doc_name": chunk.get("doc_name"),
        "file_type": meta.get("file_type") or ("video" if is_video else "document"),
        "preview": (chunk.get("content") or "")[:320],
    }
    if is_video:
        source.update({
            "start_seconds": meta.get("start_seconds"),
            "end_seconds": meta.get("end_seconds"),
            "start_time": meta.get("start_time"),
            "end_time": meta.get("end_time"),
        })
    return source


def _grounded_instruction(scope: dict, response_language: str | None) -> str:
    return (
        f"{response_language_instruction(response_language)}\n\n"
        f"LEARNING SCOPE RULE:\n{scope['instruction']}"
    )


@router.post("/tutor/query/stream")
async def guest_tutor_query_stream(
    req: GuestTutorRequest,
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
    db=Depends(get_db),
):
    session = await _require_guest_learner(db, x_guest_token)
    if not req.question.strip():
        raise HTTPException(400, "question must not be empty")
    if int(session["tutor_query_count"] or 0) >= GUEST_LEARNING_MAX_TUTOR_QUERIES:
        raise HTTPException(429, "Guest tutor question limit reached for this preview session.")

    course_id = str(session["course_id"])
    user_id = str(session["guest_user_id"])
    scope = await _resolve_learning_scope(db, course_id, user_id, req.module_id, req.lesson_id)
    if not scope["document_ids"]:
        raise HTTPException(409, "No embedded content is available in this learning scope yet.")

    async def generate():
        queue: asyncio.Queue = asyncio.Queue()

        async def on_token(t: str):
            await queue.put(("token", t))

        async def run():
            try:
                question = redact_text(req.question, req.redact_pii).text
                query_vec = await embed_query(question)
                candidates = await find_similar(
                    query_embedding=query_vec,
                    query_text=question,
                    user_id=user_id,
                    document_ids=scope["document_ids"],
                    limit=_FETCH_K,
                )
                chunks = await rerank(query=question, chunks=candidates, top_k=TOP_K)
                context = "\n\n".join(
                    f"[Source {idx+1}] {redact_text(c.get('content',''), req.redact_pii).text}"
                    for idx, c in enumerate(chunks)
                )
                system_prompt = rag_system(context, _grounded_instruction(scope, req.response_language))
                messages = [
                    {"role": m["role"], "content": redact_text(m["content"], req.redact_pii).text}
                    for m in (req.history or [])[-6:]
                    if m.get("role") and m.get("content")
                ] + [{"role": "user", "content": question}]
                await chat_stream(messages, system_prompt, on_token)
                async with get_pool().acquire() as conn:
                    await conn.execute(
                        "UPDATE guest_learning_sessions SET tutor_query_count=tutor_query_count+1, updated_at=NOW() WHERE id=$1",
                        session["id"],
                    )
                sources = [_public_source(c, idx) for idx, c in enumerate(chunks)]
                await queue.put(("done", {"sources": sources, "learning_boundary": scope["label"]}))
            except Exception as exc:
                await queue.put(("error", str(exc)))

        task = asyncio.create_task(run())
        try:
            while True:
                kind, payload = await queue.get()
                if kind == "token":
                    yield f"data: {json.dumps({'type': 'token', 'text': payload})}\n\n"
                elif kind == "done":
                    yield f"data: {json.dumps({'type': 'done', **payload})}\n\n"
                    break
                elif kind == "error":
                    yield f"data: {json.dumps({'type': 'error', 'error': payload})}\n\n"
                    break
            await task
        except asyncio.CancelledError:
            task.cancel()
            raise

    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


async def _generate_grounded_text(db, session: dict, scope: dict, prompt: str, response_language: str | None = None) -> str:
    """Non-streamed variant of the tutor pipeline, used to generate study-tool content."""
    query_vec = await embed_query(prompt)
    candidates = await find_similar(
        query_embedding=query_vec,
        query_text=prompt,
        user_id=str(session["guest_user_id"]),
        document_ids=scope["document_ids"],
        limit=_FETCH_K,
    )
    chunks = await rerank(query=prompt, chunks=candidates, top_k=TOP_K)
    context = "\n\n".join(f"[Source {idx+1}] {c.get('content','')}" for idx, c in enumerate(chunks))
    system_prompt = rag_system(context, _grounded_instruction(scope, response_language))
    output: list[str] = []

    async def on_token(t: str):
        output.append(t)

    await chat_stream([{"role": "user", "content": prompt}], system_prompt, on_token)
    return "".join(output)


@router.post("/artifacts")
async def create_guest_artifact(
    req: GuestArtifactRequest,
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
    db=Depends(get_db),
):
    session = await _require_guest_learner(db, x_guest_token)
    if req.artifact_type not in ARTIFACT_TYPES:
        raise HTTPException(400, f"Unknown artifact_type. Expected one of: {sorted(ARTIFACT_TYPES)}")
    if int(session["artifact_count"] or 0) >= GUEST_LEARNING_MAX_ARTIFACTS:
        raise HTTPException(429, "Guest study-tool generation limit reached for this preview session.")

    course_id = str(session["course_id"])
    user_id = str(session["guest_user_id"])
    scope = await _resolve_learning_scope(db, course_id, user_id, req.module_id, req.lesson_id)
    if not scope["document_ids"]:
        raise HTTPException(409, "No embedded content is available in this learning scope yet.")

    raw_content = await _generate_grounded_text(db, session, scope, STUDY_PROMPTS[req.artifact_type], req.response_language)
    content = _normalize_artifact_content(req.artifact_type, raw_content)

    async with db.transaction():
        row = await db.fetchrow(
            """INSERT INTO learning_artifacts
               (course_id,user_id,artifact_type,title,content,source_document_ids,module_id,lesson_id)
               VALUES ($1::uuid,$2::uuid,$3,$4,$5,$6::jsonb,$7,$8) RETURNING *""",
            course_id, user_id, req.artifact_type,
            (req.title or "").strip() or req.artifact_type.replace("_", " ").title(),
            content, json.dumps(scope["document_ids"]), scope["module_id"], scope["lesson_id"],
        )
        await db.execute(
            "UPDATE guest_learning_sessions SET artifact_count=artifact_count+1, updated_at=NOW() WHERE id=$1",
            session["id"],
        )
    result = dict(row)
    result["id"] = str(result["id"])
    result["created_at"] = result["created_at"].isoformat()
    result["updated_at"] = result["updated_at"].isoformat()
    return result


@router.get("/artifacts")
async def list_guest_artifacts(
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
    db=Depends(get_db),
):
    session = await _require_guest_learner(db, x_guest_token)
    rows = await db.fetch(
        "SELECT * FROM learning_artifacts WHERE course_id=$1::uuid AND user_id=$2::uuid ORDER BY created_at DESC",
        str(session["course_id"]), str(session["guest_user_id"]),
    )
    out = []
    for row in rows:
        item = dict(row)
        item["id"] = str(item["id"])
        item["created_at"] = item["created_at"].isoformat()
        item["updated_at"] = item["updated_at"].isoformat()
        out.append(item)
    return out


@router.post("/artifacts/{artifact_id}/attempts")
async def submit_guest_quiz_attempt(
    artifact_id: str,
    req: GuestQuizAttemptRequest,
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
    db=Depends(get_db),
):
    session = await _require_guest_learner(db, x_guest_token)
    course_id = str(session["course_id"])
    user_id = str(session["guest_user_id"])
    artifact = await db.fetchrow(
        """SELECT id,content,module_id,lesson_id FROM learning_artifacts
           WHERE id=$1::uuid AND course_id=$2::uuid AND user_id=$3::uuid AND artifact_type='practice_questions'""",
        artifact_id, course_id, user_id,
    )
    if not artifact:
        raise HTTPException(404, "Practice-question artifact not found for this guest session")
    quiz = PracticeQuiz.model_validate_json(str(artifact["content"]))
    graded = _grade_practice_quiz(quiz, req.answers)
    row = await db.fetchrow(
        """INSERT INTO learning_quiz_attempts
           (course_id,artifact_id,user_id,answers,result,correct_count,question_count,completed)
           VALUES ($1::uuid,$2::uuid,$3::uuid,$4::jsonb,$5::jsonb,$6,$7,$8)
           ON CONFLICT (artifact_id,user_id) DO UPDATE SET
             answers=EXCLUDED.answers,result=EXCLUDED.result,correct_count=EXCLUDED.correct_count,
             question_count=EXCLUDED.question_count,completed=EXCLUDED.completed,
             submitted_at=NOW(),updated_at=NOW()
           RETURNING *""",
        course_id, artifact_id, user_id, json.dumps(req.answers), json.dumps(graded["result"]),
        graded["correct_count"], graded["question_count"], graded["completed"],
    )
    result = dict(row)
    result["id"] = str(result["id"])
    result["submitted_at"] = result["submitted_at"].isoformat()
    result["updated_at"] = result["updated_at"].isoformat()
    result["answers"] = json.loads(result["answers"]) if isinstance(result["answers"], str) else result["answers"]
    result["result"] = json.loads(result["result"]) if isinstance(result["result"], str) else result["result"]
    return result


@router.get("/progress")
async def get_guest_progress(
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
    db=Depends(get_db),
):
    session = await _require_guest_learner(db, x_guest_token)
    rows = await db.fetch(
        "SELECT lesson_id, module_id, status, progress_pct, time_spent_seconds, completed_at "
        "FROM learning_lesson_progress WHERE course_id=$1::uuid AND user_id=$2::uuid",
        str(session["course_id"]), str(session["guest_user_id"]),
    )
    return [dict(r) | {"lesson_id": str(r["lesson_id"]), "module_id": str(r["module_id"])} for r in rows]


@router.post("/cleanup-expired")
async def cleanup_expired_guest_learning_sessions(admin: AdminUser, db=Depends(get_db)):
    """Admin-gated, mirrors routes/guest.py's cleanup-expired. Deleting the
    ephemeral user cascades through workspace_members, learning_course_members,
    learning_artifacts, learning_quiz_attempts, and learning_lesson_progress."""
    sessions = await db.fetch(
        "SELECT id, guest_user_id FROM guest_learning_sessions WHERE expires_at <= NOW() LIMIT 200",
    )
    deleted = 0
    for session in sessions:
        await db.execute("DELETE FROM users WHERE id=$1", session["guest_user_id"])
        await db.execute("DELETE FROM guest_learning_sessions WHERE id=$1", session["id"])
        deleted += 1
    return {"deleted_sessions": deleted}
