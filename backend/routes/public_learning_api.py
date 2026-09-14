from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from auth.api_oauth import ApiPrincipal, enforce_api_usage, require_api_scope, validate_api_workspace_context
from database.connection import get_db
from routes import learning
from routes.chat import ChatRequest, chat_stream_endpoint


router = APIRouter(dependencies=[Depends(validate_api_workspace_context), Depends(enforce_api_usage)])

LearningReader = Annotated[ApiPrincipal, Depends(require_api_scope("learning:read"))]
LearningParticipant = Annotated[ApiPrincipal, Depends(require_api_scope("learning:participate"))]
LearningManager = Annotated[ApiPrincipal, Depends(require_api_scope("learning:manage"))]


class LearningTutorRequest(BaseModel):
    question: str = Field(min_length=1, max_length=12_000)
    module_id: str | None = None
    lesson_id: str | None = None
    history: list[dict[str, Any]] = Field(default_factory=list, max_length=30)
    response_language: Literal["en", "es", "bn", "hi", "ar"] | None = None
    redact_pii: bool = False


async def _require_workspace(request: Request, workspace_id: str) -> None:
    selected = getattr(request.state, "api_workspace_id", None)
    if not selected:
        raise HTTPException(400, "Knowledge Academy requires X-DocIntel-Workspace-ID")
    if str(selected) != str(workspace_id):
        raise HTTPException(403, "The learning resource is outside the selected workspace")


async def _require_course_workspace(request: Request, db, course_id: str) -> str:
    workspace_id = await db.fetchval(
        "SELECT workspace_id FROM learning_courses WHERE id=$1::uuid", course_id,
    )
    if not workspace_id:
        raise HTTPException(404, "Course not found")
    await _require_workspace(request, str(workspace_id))
    return str(workspace_id)


@router.get("/learning/domain-packs", summary="List Knowledge Academy domain packs")
async def api_list_learning_domain_packs(principal: LearningReader):
    return await learning.list_domain_packs(current_user=principal.user)


@router.get("/learning/courses", summary="List accessible Knowledge Academy courses")
async def api_list_learning_courses(request: Request, principal: LearningReader, workspace_id: str = Query(...), db=Depends(get_db)):
    await _require_workspace(request, workspace_id)
    return await learning.list_courses(workspace_id, current_user=principal.user, db=db)


@router.post("/learning/courses", status_code=201, summary="Create a Knowledge Academy course")
async def api_create_learning_course(request: Request, body: learning.CourseCreate, principal: LearningManager, db=Depends(get_db)):
    await _require_workspace(request, body.workspace_id)
    return await learning.create_course(body, request, current_user=principal.user, db=db)


@router.get("/learning/courses/{course_id}", summary="Get a governed course workspace")
async def api_get_learning_course(request: Request, course_id: str, principal: LearningReader, db=Depends(get_db)):
    await _require_course_workspace(request, db, course_id)
    return await learning.get_course(course_id, current_user=principal.user, db=db)


@router.get("/learning/courses/{course_id}/scope", summary="Resolve course, module, or lesson evidence scope")
async def api_resolve_learning_scope(request: Request, course_id: str, principal: LearningReader, module_id: str | None = None, lesson_id: str | None = None, db=Depends(get_db)):
    await _require_course_workspace(request, db, course_id)
    return await learning.resolve_learning_scope(course_id, current_user=principal.user, module_id=module_id, lesson_id=lesson_id, db=db)


@router.patch("/learning/courses/{course_id}", summary="Update course metadata or lifecycle status")
async def api_update_learning_course(request: Request, course_id: str, body: learning.CourseUpdate, principal: LearningManager, db=Depends(get_db)):
    await _require_course_workspace(request, db, course_id)
    return await learning.update_course(course_id, body, request, current_user=principal.user, db=db)


@router.delete("/learning/courses/{course_id}", summary="Delete a course and its learning records")
async def api_delete_learning_course(request: Request, course_id: str, principal: LearningManager, db=Depends(get_db)):
    await _require_course_workspace(request, db, course_id)
    return await learning.delete_course(course_id, request, current_user=principal.user, db=db)


@router.post("/learning/courses/{course_id}/members", summary="Enroll a course member")
async def api_enroll_learning_member(request: Request, course_id: str, body: learning.MemberCreate, principal: LearningManager, db=Depends(get_db)):
    await _require_course_workspace(request, db, course_id)
    return await learning.add_member(course_id, body, current_user=principal.user, db=db)


@router.delete("/learning/courses/{course_id}/members/{member_user_id}", summary="Remove a course member")
async def api_remove_learning_member(request: Request, course_id: str, member_user_id: str, principal: LearningManager, db=Depends(get_db)):
    await _require_course_workspace(request, db, course_id)
    return await learning.remove_member(course_id, member_user_id, current_user=principal.user, db=db)


@router.put("/learning/courses/{course_id}/curriculum", summary="Replace ordered modules and lessons")
async def api_save_learning_curriculum(request: Request, course_id: str, body: learning.CurriculumSave, principal: LearningManager, db=Depends(get_db)):
    await _require_course_workspace(request, db, course_id)
    return await learning.save_curriculum(course_id, body, current_user=principal.user, db=db)


@router.get("/learning/documents", summary="List workspace content eligible for course attachment")
async def api_list_learning_documents(request: Request, principal: LearningReader, workspace_id: str = Query(...), db=Depends(get_db)):
    await _require_workspace(request, workspace_id)
    return await learning.list_learning_documents(workspace_id, current_user=principal.user, db=db)


@router.post("/learning/courses/{course_id}/assets", summary="Attach document, audio, or video content")
async def api_attach_learning_content(request: Request, course_id: str, body: learning.AssetCreate, principal: LearningManager, db=Depends(get_db)):
    await _require_course_workspace(request, db, course_id)
    return await learning.add_asset(course_id, body, current_user=principal.user, db=db)


@router.patch("/learning/courses/{course_id}/assets/{asset_id}", summary="Replace a course content mapping")
async def api_update_learning_content_mapping(
    request: Request, course_id: str, asset_id: str, body: learning.AssetMappingUpdate,
    principal: LearningManager, db=Depends(get_db),
):
    await _require_course_workspace(request, db, course_id)
    return await learning.update_asset_mapping(
        course_id, asset_id, body, current_user=principal.user, db=db,
    )


@router.delete("/learning/courses/{course_id}/assets/{asset_id}", summary="Detach content without deleting its source document")
async def api_remove_learning_content(request: Request, course_id: str, asset_id: str, principal: LearningManager, db=Depends(get_db)):
    await _require_course_workspace(request, db, course_id)
    return await learning.remove_asset(course_id, asset_id, current_user=principal.user, db=db)


@router.post("/learning/courses/{course_id}/tutor/query/stream", summary="Ask the evidence-grounded AI Tutor")
async def api_ask_learning_tutor(request: Request, course_id: str, body: LearningTutorRequest, principal: LearningParticipant, db=Depends(get_db)):
    workspace_id = await _require_course_workspace(request, db, course_id)
    scope = await learning.resolve_learning_scope(course_id, current_user=principal.user, module_id=body.module_id, lesson_id=body.lesson_id, db=db)
    if str(scope.get("course_id")) != str(course_id):
        raise HTTPException(409, "Resolved learning scope does not match the selected course")
    if not scope["document_ids"]:
        raise HTTPException(409, "No embedded content is available in this learning scope")
    chat_request = ChatRequest(
        question=f"{scope['instruction']}\n\nSTUDENT QUESTION:\n{body.question}",
        document_ids=scope["document_ids"], history=body.history, workspace_id=workspace_id,
        redact_pii=body.redact_pii, response_language=body.response_language,
        evidence_ranges=scope.get("evidence_ranges") or [],
    )
    return await chat_stream_endpoint(request, chat_request, current_user=principal.user, db=db)


@router.post("/learning/courses/{course_id}/artifacts", status_code=201, summary="Save grounded learning material")
async def api_save_learning_artifact(request: Request, course_id: str, body: learning.ArtifactCreate, principal: LearningParticipant, db=Depends(get_db)):
    await _require_course_workspace(request, db, course_id)
    return await learning.save_artifact(course_id, body, current_user=principal.user, db=db)


@router.get("/learning/courses/{course_id}/artifacts", summary="List the caller's saved learning materials")
async def api_list_learning_artifacts(request: Request, course_id: str, principal: LearningReader, db=Depends(get_db)):
    await _require_course_workspace(request, db, course_id)
    course = await learning.get_course(course_id, current_user=principal.user, db=db)
    return course.get("artifacts") or []


@router.delete("/learning/courses/{course_id}/artifacts/{artifact_id}", summary="Delete a saved learning artifact")
async def api_delete_learning_artifact(request: Request, course_id: str, artifact_id: str, principal: LearningParticipant, db=Depends(get_db)):
    await _require_course_workspace(request, db, course_id)
    return await learning.delete_artifact(course_id, artifact_id, current_user=principal.user, db=db)


@router.post("/learning/courses/{course_id}/artifacts/{artifact_id}/attempts", summary="Grade and persist a practice-quiz attempt")
async def api_submit_learning_quiz(request: Request, course_id: str, artifact_id: str, body: learning.QuizAttemptCreate, principal: LearningParticipant, db=Depends(get_db)):
    await _require_course_workspace(request, db, course_id)
    return await learning.submit_quiz_attempt(course_id, artifact_id, body, current_user=principal.user, db=db)


@router.get("/learning/courses/{course_id}/progress", summary="Read learner or educator-visible quiz progress")
async def api_get_learning_progress(request: Request, course_id: str, principal: LearningReader, db=Depends(get_db)):
    await _require_course_workspace(request, db, course_id)
    return await learning.get_learning_progress(course_id, current_user=principal.user, db=db)


@router.post("/learning/courses/{course_id}/questions", status_code=201, summary="Escalate a question to a teacher or advisor")
async def api_ask_learning_person(request: Request, course_id: str, body: learning.QuestionCreate, principal: LearningParticipant, db=Depends(get_db)):
    await _require_course_workspace(request, db, course_id)
    return await learning.ask_human(course_id, body, request, current_user=principal.user, db=db)


@router.patch("/learning/courses/{course_id}/questions/{question_id}", summary="Answer, reassign, or close a learning question")
async def api_update_learning_question(request: Request, course_id: str, question_id: str, body: learning.QuestionUpdate, principal: LearningParticipant, db=Depends(get_db)):
    await _require_course_workspace(request, db, course_id)
    return await learning.answer_human(course_id, question_id, body, request, current_user=principal.user, db=db)
