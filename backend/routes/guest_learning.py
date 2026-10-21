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
import logging
import re
import os
import secrets
import tempfile
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile
import base64
import httpx
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from auth.dependencies import AdminUser
from auth.service import hash_password
from database.connection import get_db, get_pool
from routes.documents import _metadata_path_from_row
from routes.video import _get_accessible_document, _video_doc_row
from routes.summarize import _load_chunk_texts, _sse_error, _stream_summary
from routes.voice import (
    AUDIO_TYPE_ALIASES, GEMINI_BASE, MAX_AUDIO_BYTES, MAX_SSML_BYTES, SUPPORTED_AUDIO_TYPES,
    TTS_VOICE_BY_LANGUAGE, _build_speech_ssml, _byte_truncate, _resolve_google_speech_language,
    _transcribe_with_google_speech,
)
from services.content_translation import get_course_translation
from services.language import normalize_language
from routes.learning import (
    ARTIFACT_TYPES,
    PracticeQuiz,
    LessonProgressUpdate,
    SubmissionUpsert,
    DiscussionPostCreate,
    _assignment_response,
    _build_mastery_projection,
    _course_access,
    _course_calendar_workspace,
    _course_response,
    _decode_json,
    _discussion_can_delete,
    _discussion_post_response,
    _grade_practice_quiz,
    _jsonable,
    _lesson_in_course,
    _module_in_course,
    _normalize_artifact_content,
    _question_response,
    _quiz_attempt_response,
    _resolve_learning_scope,
    _submission_response,
    _validate_course_documents,
)
from services.audit import audit, ip_from, ua_from
from services.evaluator import eval_groundedness
from services.language import response_language_instruction
from services.llm import chat_stream, embed_query, rag_system
from services.pii import redact_text
from services.reranker import RERANK_ENABLED, rerank
from services.vectordb import RERANK_FETCH_K, TOP_K, find_similar
import services.storage as gcs
from services.extractor import convert_to_pdf

router = APIRouter()
log = logging.getLogger("docintel.guest_learning")

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
        'Create exactly 10 flashcards based only on the selected course content. '
        'Return compact JSON only, with no Markdown, commentary, or code fences, '
        'as {"cards":[{"front":"...","back":"..."}]}.'
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


async def _resolve_public_course(db, slug: str | None) -> str:
    """Resolve a public course id for the guest demo. `slug` (the optional
    `?course=` query param) maps through the public_courses registry table
    so this deployment can serve more than one curated public course --
    adding one is an INSERT into public_courses, not an env var change or a
    redeploy. No slug, or a slug with no matching row, falls back to the
    single-course GUEST_LEARNING_COURSE_ID env var."""
    if slug:
        row = await db.fetchrow("SELECT course_id FROM public_courses WHERE slug=$1", slug)
        if row:
            return str(row["course_id"])
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
async def create_guest_learning_session(
    request: Request,
    course: str | None = Query(default=None, description="Public course slug, e.g. a partner pilot. Omit for the default Knowledge Academy course."),
    db=Depends(get_db),
):
    course_id = await _resolve_public_course(db, course)
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
async def get_guest_course(
    course: str | None = Query(default=None, description="Public course slug, e.g. a partner pilot. Omit for the default Knowledge Academy course."),
    db=Depends(get_db),
):
    """Public, unauthenticated curriculum read -- no guest token required.
    Lets the adar-web page render the module/lesson browser before a
    visitor starts a session. Returns only published, student-safe fields,
    plus enough asset metadata (a lesson's video document_id, a module's
    attached PDF/DOC materials) for the page to render clickable citations
    and a course-materials list without needing an authenticated call."""
    course_id = await _resolve_public_course(db, course)
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


@router.get("/course/translation")
async def get_guest_course_translation(
    course: str | None = Query(default=None, description="Public course slug -- same lookup as GET /course."),
    language: str = "en",
    db=Depends(get_db),
):
    """Public, unauthenticated counterpart to
    routes/learning.py's GET /courses/{course_id}/translation -- same
    cache, same course content, just resolved by public slug instead of a
    course_id the guest never sees. `fields` is null for English or if
    translation isn't available right now; the frontend falls back to the
    original English content in either case."""
    course_id = await _resolve_public_course(db, course)
    try:
        fields = await get_course_translation(db, course_id, language)
    except Exception:
        fields = None
    return {"language": normalize_language(language), "fields": fields}


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


@router.get("/materials/{document_id}/pdf-view-url")
async def get_guest_material_pdf_url(
    document_id: str,
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
    db=Depends(get_db),
):
    """Guest twin of routes.documents' /{doc_id}/pdf-view-url -- a browser
    can't render .doc/.docx inline, so this converts it to a real PDF once
    via headless LibreOffice (services.extractor.convert_to_pdf) and caches
    the result on the SAME documents.gcs_pdf_path column the authenticated
    viewer uses, so a doc already converted for a signed-in student is
    served straight from cache here too."""
    session = await _require_guest_learner(db, x_guest_token)
    course_id = str(session["course_id"])
    row = await db.fetchrow(
        """SELECT d.id, d.user_id, d.original_name, d.file_type, d.gcs_source_path, d.gcs_pdf_path
             FROM documents d JOIN learning_assets a ON a.document_id = d.id
            WHERE a.course_id=$1::uuid AND d.id=$2::uuid LIMIT 1""",
        course_id, document_id,
    )
    if not row:
        raise HTTPException(404, "This material is not attached to the public course")
    if row["gcs_pdf_path"]:
        return {"url": await gcs.get_signed_url(row["gcs_pdf_path"]), "cached": True}
    if not row["gcs_source_path"]:
        raise HTTPException(409, "The original file is not available in storage")

    tmp_src = tempfile.NamedTemporaryFile(delete=False)
    tmp_src.close()
    pdf_local_path = None
    try:
        try:
            await gcs.download_to_file(row["gcs_source_path"], tmp_src.name)
        except Exception as exc:
            raise HTTPException(502, f"Could not read source file: {exc}")
        try:
            pdf_local_path = await convert_to_pdf(tmp_src.name, row["original_name"])
        except Exception as exc:
            raise HTTPException(502, f"Could not convert this document to PDF: {exc}")
        with open(pdf_local_path, "rb") as f:
            pdf_bytes = f.read()
        pdf_path = gcs.pdf_preview_path(str(row["user_id"]), document_id)
        await gcs.upload_bytes(pdf_path, pdf_bytes, "application/pdf")
        await db.execute("UPDATE documents SET gcs_pdf_path=$2, updated_at=NOW() WHERE id=$1", document_id, pdf_path)
    finally:
        try: os.unlink(tmp_src.name)
        except OSError: pass
        if pdf_local_path:
            try: os.unlink(pdf_local_path)
            except OSError: pass

    return {"url": await gcs.get_signed_url(pdf_path), "cached": False}


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


# ── Module- and lesson-scoped discussion (same table/shape as the
# authenticated app's routes.learning module/lesson discussion threads --
# reused here unmodified, just addressed by module_id/lesson_id alone
# since a guest session is already locked to one course) -- lets a guest
# post a comment/question on a Curriculum module or lesson, same as a
# real signed-in student, with replies visible to the real course's
# teachers from their own authenticated Q&A/discussion view. ──────────────

@router.get("/modules/{module_id}/discussion")
async def list_guest_module_discussion(
    module_id: str,
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
    db=Depends(get_db),
):
    session = await _require_guest_learner(db, x_guest_token)
    course_id = str(session["course_id"])
    user_id = str(session["guest_user_id"])
    course = await _course_access(db, course_id, user_id)
    await _module_in_course(db, course_id, module_id)
    rows = await db.fetch(
        """SELECT p.*, u.full_name AS author_name, u.email AS author_email
           FROM learning_assignment_discussion_posts p JOIN users u ON u.id=p.author_id
           WHERE p.module_id=$1::uuid AND p.lesson_id IS NULL ORDER BY p.created_at""",
        module_id,
    )
    posts = []
    for row in rows:
        result = _discussion_post_response(row)
        result["can_delete"] = _discussion_can_delete(course, user_id, row)
        posts.append(result)
    return {"posts": posts}


@router.post("/modules/{module_id}/discussion")
async def create_guest_module_discussion_post(
    module_id: str, body: DiscussionPostCreate, request: Request,
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
    db=Depends(get_db),
):
    session = await _require_guest_learner(db, x_guest_token)
    course_id = str(session["course_id"])
    user_id = str(session["guest_user_id"])
    course = await _course_access(db, course_id, user_id)
    await _module_in_course(db, course_id, module_id)
    if body.parent_post_id:
        parent = await db.fetchrow(
            "SELECT id FROM learning_assignment_discussion_posts WHERE id=$1::uuid AND module_id=$2::uuid AND deleted_at IS NULL",
            body.parent_post_id, module_id,
        )
        if not parent:
            raise HTTPException(404, "The post you are replying to was not found")
    row = await db.fetchrow(
        """INSERT INTO learning_assignment_discussion_posts (course_id,module_id,author_id,author_persona,body,parent_post_id)
           VALUES ($1,$2,$3,'student',$4,$5) RETURNING *""",
        course_id, module_id, user_id, body.body.strip(), body.parent_post_id,
    )
    try:
        await audit(db, user_id=user_id, action="guest_module_discussion_post_create",
                    resource_type="learning_module_discussion_post", resource_id=str(row["id"]),
                    metadata={"course_id": course_id, "module_id": module_id},
                    ip_address=ip_from(request), user_agent=ua_from(request))
    except Exception as exc:
        log.warning(f"guest module discussion audit log failed: {exc}")
    author = await db.fetchrow("SELECT full_name,email FROM users WHERE id=$1", user_id)
    result = _discussion_post_response(row)
    result["author_name"] = author["full_name"] if author else None
    result["author_email"] = author["email"] if author else None
    result["can_delete"] = True
    return result


@router.delete("/modules/{module_id}/discussion/{post_id}")
async def delete_guest_module_discussion_post(
    module_id: str, post_id: str,
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
    db=Depends(get_db),
):
    session = await _require_guest_learner(db, x_guest_token)
    course_id = str(session["course_id"])
    user_id = str(session["guest_user_id"])
    course = await _course_access(db, course_id, user_id)
    await _module_in_course(db, course_id, module_id)
    post = await db.fetchrow(
        "SELECT * FROM learning_assignment_discussion_posts WHERE id=$1::uuid AND module_id=$2::uuid",
        post_id, module_id,
    )
    if not post or post["deleted_at"]:
        raise HTTPException(404, "Discussion post not found")
    if not _discussion_can_delete(course, user_id, post):
        raise HTTPException(403, "You cannot delete this post")
    await db.execute(
        "UPDATE learning_assignment_discussion_posts SET deleted_at=NOW(),deleted_by=$2,updated_at=NOW() WHERE id=$1",
        post_id, user_id,
    )
    return {"ok": True}


@router.get("/lessons/{lesson_id}/discussion")
async def list_guest_lesson_discussion(
    lesson_id: str,
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
    db=Depends(get_db),
):
    session = await _require_guest_learner(db, x_guest_token)
    course_id = str(session["course_id"])
    user_id = str(session["guest_user_id"])
    course = await _course_access(db, course_id, user_id)
    await _lesson_in_course(db, course_id, lesson_id)
    rows = await db.fetch(
        """SELECT p.*, u.full_name AS author_name, u.email AS author_email
           FROM learning_assignment_discussion_posts p JOIN users u ON u.id=p.author_id
           WHERE p.lesson_id=$1::uuid ORDER BY p.created_at""",
        lesson_id,
    )
    posts = []
    for row in rows:
        result = _discussion_post_response(row)
        result["can_delete"] = _discussion_can_delete(course, user_id, row)
        posts.append(result)
    return {"posts": posts}


@router.post("/lessons/{lesson_id}/discussion")
async def create_guest_lesson_discussion_post(
    lesson_id: str, body: DiscussionPostCreate, request: Request,
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
    db=Depends(get_db),
):
    session = await _require_guest_learner(db, x_guest_token)
    course_id = str(session["course_id"])
    user_id = str(session["guest_user_id"])
    course = await _course_access(db, course_id, user_id)
    lesson = await _lesson_in_course(db, course_id, lesson_id)
    if body.parent_post_id:
        parent = await db.fetchrow(
            "SELECT id FROM learning_assignment_discussion_posts WHERE id=$1::uuid AND lesson_id=$2::uuid AND deleted_at IS NULL",
            body.parent_post_id, lesson_id,
        )
        if not parent:
            raise HTTPException(404, "The post you are replying to was not found")
    row = await db.fetchrow(
        """INSERT INTO learning_assignment_discussion_posts (course_id,module_id,lesson_id,author_id,author_persona,body,parent_post_id)
           VALUES ($1,$2,$3,$4,'student',$5,$6) RETURNING *""",
        course_id, lesson["module_id"], lesson_id, user_id, body.body.strip(), body.parent_post_id,
    )
    try:
        await audit(db, user_id=user_id, action="guest_lesson_discussion_post_create",
                    resource_type="learning_lesson_discussion_post", resource_id=str(row["id"]),
                    metadata={"course_id": course_id, "lesson_id": lesson_id},
                    ip_address=ip_from(request), user_agent=ua_from(request))
    except Exception as exc:
        log.warning(f"guest lesson discussion audit log failed: {exc}")
    author = await db.fetchrow("SELECT full_name,email FROM users WHERE id=$1", user_id)
    result = _discussion_post_response(row)
    result["author_name"] = author["full_name"] if author else None
    result["author_email"] = author["email"] if author else None
    result["can_delete"] = True
    return result


@router.delete("/lessons/{lesson_id}/discussion/{post_id}")
async def delete_guest_lesson_discussion_post(
    lesson_id: str, post_id: str,
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
    db=Depends(get_db),
):
    session = await _require_guest_learner(db, x_guest_token)
    course_id = str(session["course_id"])
    user_id = str(session["guest_user_id"])
    course = await _course_access(db, course_id, user_id)
    await _lesson_in_course(db, course_id, lesson_id)
    post = await db.fetchrow(
        "SELECT * FROM learning_assignment_discussion_posts WHERE id=$1::uuid AND lesson_id=$2::uuid",
        post_id, lesson_id,
    )
    if not post or post["deleted_at"]:
        raise HTTPException(404, "Discussion post not found")
    if not _discussion_can_delete(course, user_id, post):
        raise HTTPException(403, "You cannot delete this post")
    await db.execute(
        "UPDATE learning_assignment_discussion_posts SET deleted_at=NOW(),deleted_by=$2,updated_at=NOW() WHERE id=$1",
        post_id, user_id,
    )
    return {"ok": True}


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
    request: Request,
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
        answer_parts: list[str] = []

        async def on_token(t: str):
            answer_parts.append(t)
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
                judge = None
                try:
                    judge = await eval_groundedness(question, "".join(answer_parts), context)
                except Exception as judge_exc:
                    log.warning(f"guest tutor judge eval failed: {judge_exc}")
                try:
                    await audit(
                        db,
                        user_id=user_id,
                        action="guest_tutor_query",
                        resource_type="guest_learning_session",
                        resource_id=str(session["id"]),
                        metadata={
                            "course_id": course_id,
                            "module_id": req.module_id,
                            "lesson_id": req.lesson_id,
                            "question": question,
                            "judge_verdict": (judge or {}).get("verdict"),
                            "judge_score": (judge or {}).get("score"),
                        },
                        ip_address=ip_from(request),
                        user_agent=ua_from(request),
                    )
                except Exception as audit_exc:
                    log.warning(f"guest tutor audit log failed: {audit_exc}")
                await queue.put(("done", {"sources": sources, "learning_boundary": scope["label"], "judge": judge}))
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


def _normalize_guest_flashcards(raw: str) -> str:
    """Extract and validate a flashcard JSON set from raw model output,
    mirroring the authenticated frontend's parseFlashcards() so a
    guest-generated set is always valid JSON the shared FlashcardDeck
    component (JSON.parse(content)) can render."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.IGNORECASE)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        # The model may have returned a bare JSON array instead of {"cards":[...]}.
        start, end = cleaned.find("["), cleaned.rfind("]")
        if start < 0 or end <= start:
            raise HTTPException(502, "The AI did not return a valid flashcard set. Please try generating again.")
    try:
        parsed = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError:
        raise HTTPException(502, "The AI did not return a valid flashcard set. Please try generating again.")
    cards = parsed.get("cards") if isinstance(parsed, dict) else parsed
    if not isinstance(cards, list) or not cards:
        raise HTTPException(502, "The AI did not return any flashcards. Please try generating again.")
    out = []
    for card in cards:
        if not isinstance(card, dict):
            continue
        front = str(card.get("front") or card.get("question") or "").strip()
        back = str(card.get("back") or card.get("answer") or "").strip()
        if front and back:
            out.append({"front": front, "back": back})
    if not out:
        raise HTTPException(502, "The AI did not return any usable flashcards. Please try generating again.")
    return json.dumps(out, ensure_ascii=False)


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
    content = (
        _normalize_guest_flashcards(raw_content) if req.artifact_type == "flashcards"
        else _normalize_artifact_content(req.artifact_type, raw_content)
    )

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
    course_id = str(session["course_id"])
    user_id = str(session["guest_user_id"])
    rows = await db.fetch(
        "SELECT * FROM learning_artifacts WHERE course_id=$1::uuid AND user_id=$2::uuid ORDER BY created_at DESC",
        course_id, user_id,
    )
    # Embed each practice-quiz artifact's own attempt (if any) directly on
    # the artifact object -- the guest API has no separate "list quiz
    # attempts" endpoint the way the authenticated course-detail payload
    # does (course.quiz_attempts), so the frontend quiz component needs it
    # inlined here to know which questions are already answered/graded.
    attempt_rows = await db.fetch(
        "SELECT * FROM learning_quiz_attempts WHERE course_id=$1::uuid AND user_id=$2::uuid",
        course_id, user_id,
    )
    attempts_by_artifact = {str(a["artifact_id"]): a for a in attempt_rows}
    out = []
    for row in rows:
        item = dict(row)
        item["id"] = str(item["id"])
        item["created_at"] = item["created_at"].isoformat()
        item["updated_at"] = item["updated_at"].isoformat()
        attempt = attempts_by_artifact.get(item["id"])
        if attempt:
            a = dict(attempt)
            a["id"] = str(a["id"])
            a["artifact_id"] = str(a["artifact_id"])
            a["submitted_at"] = a["submitted_at"].isoformat()
            a["updated_at"] = a["updated_at"].isoformat()
            a["answers"] = _decode_json(a.get("answers"), {})
            a["result"] = _decode_json(a.get("result"), {})
            item["attempt"] = a
        else:
            item["attempt"] = None
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
    existing = await db.fetchrow(
        "SELECT answers FROM learning_quiz_attempts WHERE artifact_id=$1::uuid AND user_id=$2::uuid",
        artifact_id, user_id,
    )
    answers = {} if req.replace else (_decode_json(existing["answers"], {}) if existing else {})
    answers.update(req.answers)
    graded = await _grade_practice_quiz(quiz, answers)
    row = await db.fetchrow(
        """INSERT INTO learning_quiz_attempts
           (course_id,artifact_id,user_id,answers,result,correct_count,question_count,completed)
           VALUES ($1::uuid,$2::uuid,$3::uuid,$4::jsonb,$5::jsonb,$6,$7,$8)
           ON CONFLICT (artifact_id,user_id) DO UPDATE SET
             answers=EXCLUDED.answers,result=EXCLUDED.result,correct_count=EXCLUDED.correct_count,
             question_count=EXCLUDED.question_count,completed=EXCLUDED.completed,
             submitted_at=NOW(),updated_at=NOW()
           RETURNING *""",
        course_id, artifact_id, user_id, json.dumps(answers), json.dumps(graded["result"]),
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


class GuestQuestionRequest(BaseModel):
    target_role: str = "teacher"
    question: str


@router.get("/qna")
async def list_guest_questions(
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
    db=Depends(get_db),
):
    """A guest's own escalations only -- mirrors routes.learning's course-detail
    questions query (routes/learning.py get_learning_course) but scoped to
    q.asked_by=<this guest>, so a public visitor never sees another real
    student's question, the same privacy boundary the student persona
    already gets there (a non-admin/teacher/advisor asker only ever matches
    their own asked_by/assigned_to rows)."""
    session = await _require_guest_learner(db, x_guest_token)
    rows = await db.fetch(
        """SELECT q.*, asker.full_name AS asker_name, asker.email AS asker_email,
                  responder.full_name AS responder_name
           FROM learning_questions q JOIN users asker ON asker.id=q.asked_by
           LEFT JOIN users responder ON responder.id=q.answered_by
           WHERE q.course_id=$1::uuid AND q.asked_by=$2::uuid
           ORDER BY q.created_at DESC""",
        str(session["course_id"]), str(session["guest_user_id"]),
    )
    return [_question_response(row) for row in rows]


@router.post("/qna")
async def create_guest_question(
    req: GuestQuestionRequest,
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
    db=Depends(get_db),
):
    """Escalate a question to the course's real teacher/advisor, same as
    routes.learning.ask_human -- but intentionally skips that endpoint's
    email notification: a guest preview session is anonymous and
    unauthenticated, and paging a real instructor by email for every
    anonymous-visitor question would be noisy and confusing. The question
    still lands in learning_questions and is visible to the real teacher
    from their own authenticated Q&A tab (assigned_to them)."""
    session = await _require_guest_learner(db, x_guest_token)
    target_role = req.target_role if req.target_role in ("teacher", "advisor") else "teacher"
    question_text = (req.question or "").strip()
    if not question_text:
        raise HTTPException(400, "Question text is required")
    if len(question_text) > 8000:
        raise HTTPException(400, "Question is too long")
    course_id = str(session["course_id"])
    user_id = str(session["guest_user_id"])
    assignee = await db.fetchrow(
        """SELECT m.user_id FROM learning_course_members m
           WHERE m.course_id=$1::uuid AND m.persona=$2 ORDER BY m.created_at LIMIT 1""",
        course_id, target_role,
    )
    row = await db.fetchrow(
        """INSERT INTO learning_questions (course_id,asked_by,assigned_to,target_role,question,context)
           VALUES ($1::uuid,$2::uuid,$3,$4,$5,'{}'::jsonb) RETURNING *""",
        course_id, user_id, str(assignee["user_id"]) if assignee else None, target_role, question_text,
    )
    return _question_response(row)


# ── Content Library & Video Intelligence (read-only, chat/Q&A-enabled) ──
# Mirrors the EXACT capability the authenticated app already ships --
# routes/chat.py's /chat/stream ("select embedded documents, then chat")
# and routes/video.py's /ask (per-video Q&A over its processed timeline) --
# reused here read-only and no-login, scoped to the guest's own course
# workspace. No upload, edit, delete, process, or reclassify route is
# exposed for guests: list + chat/ask only, exactly as approved.

class GuestLibraryChatRequest(BaseModel):
    question: str
    document_ids: list[str]
    history: list[dict] = []
    response_language: str | None = None
    redact_pii: bool = False


class GuestVideoAskRequest(BaseModel):
    question: str
    limit: int = 6


async def _guest_workspace_id(db, session: dict) -> str:
    workspace_id = await db.fetchval(
        "SELECT workspace_id FROM learning_courses WHERE id=$1::uuid", str(session["course_id"]),
    )
    if not workspace_id:
        raise HTTPException(404, "This preview course has no workspace configured.")
    return str(workspace_id)


@router.get("/library/documents")
async def list_guest_library_documents(
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
    db=Depends(get_db),
):
    session = await _require_guest_learner(db, x_guest_token)
    workspace_id = await _guest_workspace_id(db, session)
    rows = await db.fetch(
        """SELECT d.id, d.original_name, d.status, d.file_type, d.file_size,
                  d.doc_type, d.doc_domain, d.doc_language, d.created_at,
                  (SELECT COUNT(*) FROM document_chunks WHERE document_id=d.id) AS chunk_count
           FROM documents d
           WHERE d.workspace_id=$1::uuid AND d.status!='deleted'
           ORDER BY d.created_at DESC""",
        workspace_id,
    )
    out = []
    for r in rows:
        item = dict(r)
        item["id"] = str(item["id"])
        item["created_at"] = item["created_at"].isoformat() if item.get("created_at") else None
        out.append(item)
    return out


@router.get("/library/documents/{doc_id}/view-url")
async def get_guest_library_view_url(
    doc_id: str,
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
    db=Depends(get_db),
):
    session = await _require_guest_learner(db, x_guest_token)
    row = await _get_accessible_document(db, doc_id, str(session["guest_user_id"]))
    url = await gcs.get_signed_url(row["gcs_source_path"])
    return {"url": url}


@router.get("/library/documents/{doc_id}/pdf-view-url")
async def get_guest_library_pdf_view_url(
    doc_id: str,
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
    db=Depends(get_db),
):
    """Guest twin of routes.documents' /{doc_id}/pdf-view-url -- see
    get_guest_material_pdf_url's docstring above for the conversion/cache
    rationale; same documents.gcs_pdf_path cache, just reached through the
    Content Library's own access check (_get_accessible_document)."""
    session = await _require_guest_learner(db, x_guest_token)
    row = await _get_accessible_document(db, doc_id, str(session["guest_user_id"]))
    if row.get("gcs_pdf_path"):
        return {"url": await gcs.get_signed_url(row["gcs_pdf_path"]), "cached": True}
    if not row.get("gcs_source_path"):
        raise HTTPException(409, "The original file is not available in storage")

    tmp_src = tempfile.NamedTemporaryFile(delete=False)
    tmp_src.close()
    pdf_local_path = None
    try:
        try:
            await gcs.download_to_file(row["gcs_source_path"], tmp_src.name)
        except Exception as exc:
            raise HTTPException(502, f"Could not read source file: {exc}")
        try:
            pdf_local_path = await convert_to_pdf(tmp_src.name, row["original_name"])
        except Exception as exc:
            raise HTTPException(502, f"Could not convert this document to PDF: {exc}")
        with open(pdf_local_path, "rb") as f:
            pdf_bytes = f.read()
        pdf_path = gcs.pdf_preview_path(str(row["user_id"]), doc_id)
        await gcs.upload_bytes(pdf_path, pdf_bytes, "application/pdf")
        await db.execute("UPDATE documents SET gcs_pdf_path=$2, updated_at=NOW() WHERE id=$1", doc_id, pdf_path)
    finally:
        try: os.unlink(tmp_src.name)
        except OSError: pass
        if pdf_local_path:
            try: os.unlink(pdf_local_path)
            except OSError: pass

    return {"url": await gcs.get_signed_url(pdf_path), "cached": False}


class GuestSummarizeRequest(BaseModel):
    summary_type: str = "executive"
    custom_prompt: str = ""
    response_language: str | None = None


@router.post("/library/documents/{doc_id}/summarize/stream")
async def guest_library_summarize_stream(
    doc_id: str,
    body: GuestSummarizeRequest,
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
    db=Depends(get_db),
):
    """Guest twin of routes.summarize's /document/{doc_id}/stream, reusing
    its pure _stream_summary/_load_chunk_texts/_sse_error map-reduce
    helpers unmodified. Access is checked with the same
    _get_accessible_document helper the rest of this guest Content
    Library already uses, plus the chunked/embedding/embedded gate
    routes.summarize's own _get_accessible_chunked enforces."""
    session = await _require_guest_learner(db, x_guest_token)
    if int(session["tutor_query_count"] or 0) >= GUEST_LEARNING_MAX_TUTOR_QUERIES:
        raise HTTPException(429, "Guest preview question limit reached for this session.")
    row = await _get_accessible_document(db, doc_id, str(session["guest_user_id"]))
    if row["status"] not in ("chunked", "embedding", "embedded"):
        raise HTTPException(400, f"Document must be chunked first (status: {row['status']})")

    async def generate():
        try:
            meta = await gcs.download_json(_metadata_path_from_row(dict(row), doc_id))
            chunks = meta["chunks"]
            if not chunks:
                yield _sse_error("No chunks found"); return
            texts = await _load_chunk_texts(chunks)
            async for event in _stream_summary(
                texts, body.summary_type, body.custom_prompt, row["original_name"],
                body.response_language or row.get("doc_language") or "en", False,
            ):
                yield event
            # Fresh connection -- `db` is released back to the pool as soon
            # as this endpoint returns the StreamingResponse below, well
            # before this generator actually runs (see the identical fix
            # in guest_library_chat_stream above).
            async with get_pool().acquire() as conn:
                await conn.execute(
                    "UPDATE guest_learning_sessions SET tutor_query_count=tutor_query_count+1, updated_at=NOW() WHERE id=$1",
                    session["id"],
                )
        except Exception as e:
            yield _sse_error(str(e))

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/library/chat/stream")
async def guest_library_chat_stream(
    req: GuestLibraryChatRequest,
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
    db=Depends(get_db),
):
    session = await _require_guest_learner(db, x_guest_token)
    if not req.question.strip():
        raise HTTPException(400, "question must not be empty")
    if not req.document_ids:
        raise HTTPException(400, "Select at least one document to chat with")
    if int(session["tutor_query_count"] or 0) >= GUEST_LEARNING_MAX_TUTOR_QUERIES:
        raise HTTPException(429, "Guest preview question limit reached for this session.")
    workspace_id = await _guest_workspace_id(db, session)
    user_id = str(session["guest_user_id"])

    rows = await db.fetch(
        "SELECT id, status FROM documents WHERE id = ANY($1::uuid[]) AND workspace_id=$2::uuid AND status != 'deleted'",
        req.document_ids, workspace_id,
    )
    found_ids = {str(r["id"]) for r in rows}
    not_found = set(req.document_ids) - found_ids
    not_embedded = {str(r["id"]) for r in rows if r["status"] != "embedded"}
    if not_found:
        raise HTTPException(403, f"Documents not found in this preview workspace: {sorted(not_found)}")
    if not_embedded:
        raise HTTPException(400, f"Documents not yet embedded: {sorted(not_embedded)}")

    async def generate():
        queue: asyncio.Queue = asyncio.Queue()

        async def run():
            try:
                question = redact_text(req.question, req.redact_pii).text
                query_vec = await embed_query(question)
                candidates = await find_similar(
                    query_embedding=query_vec, query_text=question, user_id=user_id,
                    document_ids=sorted(found_ids), limit=_FETCH_K,
                )
                chunks = await rerank(query=question, chunks=candidates, top_k=TOP_K)
                context = "\n\n".join(f"[Source {idx+1}] {c.get('content','')}" for idx, c in enumerate(chunks))
                system_prompt = rag_system(context, response_language_instruction(req.response_language))

                async def on_token(t: str):
                    await queue.put(("token", t))

                messages = [
                    {"role": m["role"], "content": redact_text(m["content"], req.redact_pii).text}
                    for m in (req.history or [])[-6:] if m.get("role") and m.get("content")
                ] + [{"role": "user", "content": question}]
                await chat_stream(messages, system_prompt, on_token)
                # Use a freshly-acquired connection here, not the request-scoped
                # `db` -- FastAPI's yield-dependency cleanup releases `db` back to
                # the pool as soon as this endpoint RETURNS the StreamingResponse
                # object, which happens well before this generator actually runs
                # (the generator only starts once the ASGI server begins consuming
                # the stream). Using the stale `db` here always raised "cannot call
                # Connection.execute(): connection has been released back to the
                # pool" -- exactly the error reported live. guest_tutor_query_stream
                # already does this correctly a few hundred lines up; this mirrors it.
                async with get_pool().acquire() as conn:
                    await conn.execute(
                        "UPDATE guest_learning_sessions SET tutor_query_count=tutor_query_count+1, updated_at=NOW() WHERE id=$1",
                        session["id"],
                    )
                sources = [_public_source(c, idx) for idx, c in enumerate(chunks)]
                await queue.put(("done", {"sources": sources}))
            except Exception as exc:
                log.exception("guest library chat failed")
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


@router.get("/library/videos")
async def list_guest_library_videos(
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
    db=Depends(get_db),
):
    session = await _require_guest_learner(db, x_guest_token)
    workspace_id = await _guest_workspace_id(db, session)
    rows = await db.fetch(
        """
        SELECT d.id, d.original_name, d.file_type, d.status, d.chunk_count,
               d.error_message, d.doc_type, d.doc_domain,
               COALESCE(d.workspace_id, vd.workspace_id) AS workspace_id,
               d.workspace_id AS document_workspace_id,
               vd.workspace_id AS video_workspace_id,
               d.doc_metadata,
               vd.id AS video_id, vd.processing_status, vd.duration_seconds,
               vd.width, vd.height, vd.frame_count, vd.updated_at AS video_updated_at,
               vd.metadata AS video_metadata
        FROM documents d
        LEFT JOIN video_documents vd ON vd.document_id = d.id
        WHERE d.status != 'deleted'
          AND COALESCE(d.workspace_id, vd.workspace_id) = $1::uuid
          AND (
            d.file_type = 'video'
            OR d.doc_type = 'video'
            OR LOWER(d.original_name) ~ '\.(mp4|mov|m4v|avi|mkv|webm)$'
          )
        ORDER BY d.created_at DESC
        """,
        workspace_id,
    )
    return [_video_doc_row(r) for r in rows]


@router.get("/library/videos/{doc_id}/timeline")
async def get_guest_video_timeline(
    doc_id: str,
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
    db=Depends(get_db),
):
    session = await _require_guest_learner(db, x_guest_token)
    await _get_accessible_document(db, doc_id, str(session["guest_user_id"]))
    video = await db.fetchrow("SELECT * FROM video_documents WHERE document_id=$1", doc_id)
    if not video:
        raise HTTPException(404, "Video has not been processed yet")
    segments = await db.fetch(
        """SELECT id, segment_index, start_seconds, end_seconds, segment_type, title,
                  summary, transcript, ocr_text, thumbnail_path, confidence, metadata
           FROM video_segments WHERE video_document_id=$1 ORDER BY segment_index""",
        video["id"],
    )
    frames = await db.fetch(
        """SELECT id, segment_id, frame_index, timestamp_seconds, frame_path, thumbnail_path,
                  caption, ocr_text, metadata
           FROM video_frames WHERE video_document_id=$1 ORDER BY frame_index""",
        video["id"],
    )
    return {"video": _jsonable(dict(video)), "segments": [_jsonable(dict(r)) for r in segments], "frames": [_jsonable(dict(r)) for r in frames]}


@router.get("/library/videos/{doc_id}/frames/{frame_index}/view-url")
async def get_guest_video_frame_url(
    doc_id: str, frame_index: int,
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
    db=Depends(get_db),
):
    session = await _require_guest_learner(db, x_guest_token)
    await _get_accessible_document(db, doc_id, str(session["guest_user_id"]))
    row = await db.fetchrow(
        """SELECT vf.frame_path FROM video_frames vf JOIN video_documents vd ON vd.id = vf.video_document_id
           WHERE vd.document_id=$1 AND vf.frame_index=$2""",
        doc_id, frame_index,
    )
    if not row:
        raise HTTPException(404, "Frame not found")
    return {"url": await gcs.get_signed_url(row["frame_path"])}


@router.post("/library/videos/{doc_id}/ask")
async def ask_guest_video(
    doc_id: str,
    body: GuestVideoAskRequest,
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
    db=Depends(get_db),
):
    session = await _require_guest_learner(db, x_guest_token)
    if int(session["tutor_query_count"] or 0) >= GUEST_LEARNING_MAX_TUTOR_QUERIES:
        raise HTTPException(429, "Guest preview question limit reached for this session.")
    user_id = str(session["guest_user_id"])
    doc = await _get_accessible_document(db, doc_id, user_id)
    if doc.get("status") != "embedded":
        raise HTTPException(400, f"Video document must be embedded before Q&A. Current status: {doc.get('status')}")
    question = body.question.strip()
    if not question:
        raise HTTPException(400, "question is required")

    qvec = await embed_query(question)
    matches = await find_similar(qvec, user_id, [doc_id], limit=max(1, min(body.limit, 12)), query_text=question)
    context_parts, sources = [], []
    for idx, match in enumerate(matches, 1):
        context_parts.append(f"[Source {idx}] {match.get('doc_name')}\n{match.get('content')}")
        sources.append({
            "source": idx, "document_id": str(match.get("document_id")), "doc_name": match.get("doc_name"),
            "chunk_index": match.get("chunk_index"), "similarity": float(match.get("similarity") or 0),
        })
    system = rag_system(
        "\n\n".join(context_parts),
        f"{response_language_instruction(None)} For video answers, cite the timestamp when available and keep the answer grounded in the retrieved timeline chunks.",
    )
    tokens: list[str] = []

    async def on_token(t: str):
        tokens.append(t)

    await chat_stream([{"role": "user", "content": question}], system, on_token)
    await db.execute(
        "UPDATE guest_learning_sessions SET tutor_query_count=tutor_query_count+1, updated_at=NOW() WHERE id=$1",
        session["id"],
    )
    return {"answer": "".join(tokens).strip(), "sources": sources}


# ---------------------------------------------------------------------------
# Voice input/output for every guest-facing assistant (AI Tutor, Content
# Library chat) -- reuses the exact same Google Speech-to-Text / Gemini
# fallback / Google Text-to-Speech logic routes/voice.py already runs for
# signed-in users, just gated by a guest session instead of CurrentUser, and
# without that route's OTel tracing (guest traffic isn't part of that
# per-account trace story). Gated on the same question-limit counter the
# rest of this file uses rather than a bespoke rate limiter -- a guest who
# has used up their preview questions can't rack up unbounded Google Speech
# cost by only ever using voice, and one who hasn't is already bounded by it.
async def _guest_transcribe(data: bytes, content_type: str, language: str) -> str:
    google_speech_language = _resolve_google_speech_language(language)
    if google_speech_language:
        try:
            return await _transcribe_with_google_speech(data, content_type, google_speech_language)
        except Exception as exc:
            log.warning("Guest Google Speech transcription failed, falling back to Gemini: %s", exc)

    google_ai_key = os.getenv("GOOGLE_AI_KEY", "").strip()
    if not google_ai_key:
        raise HTTPException(500, "Voice transcription is not configured")
    model = os.getenv("GEMINI_AUDIO_MODEL", os.getenv("GEMINI_CHAT_MODEL", "gemini-2.5-flash")).removeprefix("models/")
    prompt = (
        "Transcribe this microphone audio into plain text only. "
        "Do not translate. Do not summarize. Do not add punctuation unless it is clearly spoken. "
        "If the audio is empty or unintelligible, return an empty string."
    )
    if language:
        prompt += f" The expected spoken language locale is {language}."
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": content_type, "data": base64.b64encode(data).decode("ascii")}},
            ],
        }],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 512},
    }
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(f"{GEMINI_BASE}/{model}:generateContent", params={"key": google_ai_key}, json=payload)
    if not resp.is_success:
        log.warning("Guest Gemini voice transcription failed %s: %s", resp.status_code, resp.text[:500])
        raise HTTPException(resp.status_code, "Voice transcription failed")
    body = resp.json()
    parts = body.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    return " ".join((p.get("text") or "").strip() for p in parts if p.get("text")).strip()


@router.post("/voice/transcribe")
async def guest_transcribe_voice(
    audio: UploadFile = File(...),
    language: str = Form(""),
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
    db=Depends(get_db),
):
    session = await _require_guest_learner(db, x_guest_token)
    if int(session["tutor_query_count"] or 0) >= GUEST_LEARNING_MAX_TUTOR_QUERIES:
        raise HTTPException(429, "Guest preview question limit reached for this session.")
    content_type = (audio.content_type or "application/octet-stream").split(";")[0].strip().lower()
    content_type = AUDIO_TYPE_ALIASES.get(content_type, content_type)
    if content_type not in SUPPORTED_AUDIO_TYPES:
        raise HTTPException(400, f"Unsupported audio format: {content_type}")
    data = await audio.read()
    if not data:
        raise HTTPException(400, "No audio received")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(413, f"Audio is too large. Max {MAX_AUDIO_BYTES // 1024 // 1024} MB")
    text = await _guest_transcribe(data, content_type, language)
    return {"text": text}


class GuestSpeakRequest(BaseModel):
    text: str
    language: str = "en"


@router.post("/voice/speak")
async def guest_speak_text(
    body: GuestSpeakRequest,
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
    db=Depends(get_db),
):
    session = await _require_guest_learner(db, x_guest_token)
    if int(session["tutor_query_count"] or 0) >= GUEST_LEARNING_MAX_TUTOR_QUERIES:
        raise HTTPException(429, "Guest preview question limit reached for this session.")
    raw_text = (body.text or "").strip()
    if not raw_text:
        raise HTTPException(400, "text is required")
    raw_text = _byte_truncate(raw_text, 3200)
    ssml = _build_speech_ssml(raw_text)
    shrink_attempts = 0
    while len(ssml.encode("utf-8")) > MAX_SSML_BYTES and raw_text and shrink_attempts < 10:
        raw_text = _byte_truncate(raw_text, max(0, len(raw_text.encode("utf-8")) - 500))
        ssml = _build_speech_ssml(raw_text)
        shrink_attempts += 1
    if ssml == "<speak></speak>" or len(ssml.encode("utf-8")) > MAX_SSML_BYTES:
        raise HTTPException(400, "Nothing left to speak after removing formatting")
    voice = TTS_VOICE_BY_LANGUAGE.get((body.language or "").strip().lower(), TTS_VOICE_BY_LANGUAGE["en"])
    api_key = (
        os.getenv("GOOGLE_TTS_API_KEY") or os.getenv("GOOGLE_SPEECH_API_KEY")
        or os.getenv("GOOGLE_AI_KEY") or os.getenv("GOOGLE_API_KEY") or ""
    ).strip()
    if not api_key:
        raise HTTPException(500, "Text-to-speech is not configured")
    payload = {"input": {"ssml": ssml}, "voice": voice, "audioConfig": {"audioEncoding": "MP3"}}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post("https://texttospeech.googleapis.com/v1/text:synthesize", params={"key": api_key}, json=payload)
    if not resp.is_success:
        log.warning("Guest text-to-speech failed %s: %s", resp.status_code, resp.text[:500])
        try:
            google_detail = resp.json().get("error", {}).get("message", "")
        except Exception:
            google_detail = ""
        raise HTTPException(502, f"Text-to-speech failed: {google_detail or resp.text[:200]}")
    audio_b64 = resp.json().get("audioContent") or ""
    if not audio_b64:
        raise HTTPException(502, "Text-to-speech returned no audio")
    return {"audio_base64": audio_b64, "mime_type": "audio/mpeg"}


class RegisterPublicCourseRequest(BaseModel):
    workspace_name: str
    course_title: str
    slug: str
    label: str = ""


@router.post("/admin/public-courses")
async def register_public_course(req: RegisterPublicCourseRequest, admin: AdminUser, db=Depends(get_db)):
    """Admin-only: register (or re-point) a no-login guest-preview slug
    onto an EXISTING course, resolved by workspace name + course title --
    so registering a second (or third) curated public course never needs
    raw SQL or a course UUID looked up by hand. Mirrors exactly what
    public_courses' own comment in database/models.py describes ("adding
    a course is an INSERT, not an env var change"); this is that INSERT,
    reachable over the API instead of a direct DB connection. _resolve_public_course
    above is what actually reads this table at guest-session-creation time."""
    row = await db.fetchrow(
        """SELECT c.id FROM learning_courses c JOIN workspaces w ON w.id=c.workspace_id
           WHERE LOWER(w.name)=LOWER($1) AND LOWER(c.title)=LOWER($2)""",
        req.workspace_name.strip(), req.course_title.strip(),
    )
    if not row:
        raise HTTPException(404, f"No course titled '{req.course_title}' found in workspace '{req.workspace_name}'")
    course_id = str(row["id"])
    await db.execute(
        """INSERT INTO public_courses (slug, course_id, label) VALUES ($1,$2,$3)
           ON CONFLICT (slug) DO UPDATE SET course_id=EXCLUDED.course_id, label=EXCLUDED.label""",
        req.slug.strip(), course_id, (req.label or req.course_title).strip(),
    )
    return {"slug": req.slug.strip(), "course_id": course_id}


@router.get("/admin/public-courses")
async def list_public_courses(admin: AdminUser, db=Depends(get_db)):
    rows = await db.fetch(
        """SELECT pc.slug, pc.course_id, pc.label, pc.created_at, c.title AS course_title, w.name AS workspace_name
           FROM public_courses pc
           JOIN learning_courses c ON c.id = pc.course_id
           JOIN workspaces w ON w.id = c.workspace_id
           ORDER BY pc.created_at""",
    )
    return [_jsonable(dict(r)) for r in rows]


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
