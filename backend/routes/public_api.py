from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Request

from auth.api_oauth import (
    ApiPrincipal,
    get_api_principal,
    require_api_scope,
    validate_api_workspace_context,
)
from database.connection import get_db
from routes.chat import ChatRequest, chat_stream_endpoint
from routes.documents import (
    DirectUploadCompleteRequest,
    DirectUploadSessionRequest,
    complete_direct_upload,
    create_direct_upload_session,
    delete_document,
    get_chunks,
    get_document,
    list_documents,
    trigger_embedding,
)
from routes.summarize import SummarizeRequest, summarize_document
from routes.workspaces import get_workspace, list_workspace_documents, list_workspaces


router = APIRouter(dependencies=[Depends(validate_api_workspace_context)])
API_VERSION = "2026-08-31.3"

WorkspaceReader = Annotated[ApiPrincipal, Depends(require_api_scope("workspaces:read"))]
DocumentReader = Annotated[ApiPrincipal, Depends(require_api_scope("documents:read"))]
KnowledgeReader = Annotated[ApiPrincipal, Depends(require_api_scope("knowledge:query"))]
KnowledgeGenerator = Annotated[ApiPrincipal, Depends(require_api_scope("knowledge:generate"))]
DocumentWriter = Annotated[ApiPrincipal, Depends(require_api_scope("documents:write"))]


@router.get("")
async def api_catalog(principal: DocumentReader):
    return {
        "name": "ADAR DocIntel Public API",
        "version": API_VERSION,
        "client_id": principal.client_id,
        "scopes": sorted(principal.scopes),
        "capabilities": {
            "workspaces": "/api/v1/workspaces",
            "workspace_contexts": "/api/v1/me/workspaces",
            "documents": "/api/v1/documents",
            "create_upload": "/api/v1/uploads",
            "complete_upload": "/api/v1/uploads/complete",
            "grounded_query": "/api/v1/knowledge/query/stream",
            "document_summary": "/api/v1/summaries/documents/{document_id}/stream",
            "batches": "/api/v1/batches",
            "operations_catalog": "/api/v1/operations/catalog",
            "events": "/api/v1/events",
            "reviews": "/api/v1/reviews",
            "artifacts": "/api/v1/artifacts",
            "evaluations": "/api/v1/evaluations",
        },
    }


@router.get("/me")
async def api_current_identity(principal: ApiPrincipal = Depends(get_api_principal), db=Depends(get_db)):
    workspace_count = await db.fetchval(
        "SELECT COUNT(*) FROM workspace_members WHERE user_id=$1::uuid",
        principal.user_id,
    )
    return {
        "data": {
            "user_id": principal.user_id,
            "email": principal.user.get("email"),
            "full_name": principal.user.get("full_name") or "",
            "role": principal.user.get("role"),
            "client_id": principal.client_id,
            "scopes": sorted(principal.scopes),
            "workspace_count": int(workspace_count or 0),
        }
    }


@router.get("/workspaces")
async def api_list_workspaces(principal: WorkspaceReader, db=Depends(get_db)):
    return {"data": await list_workspaces(current_user=principal.user, db=db)}


@router.get("/workspaces/{workspace_id}")
async def api_get_workspace(workspace_id: str, principal: WorkspaceReader, db=Depends(get_db)):
    return {"data": await get_workspace(workspace_id, current_user=principal.user, db=db)}


@router.get("/workspaces/{workspace_id}/documents")
async def api_list_workspace_documents(workspace_id: str, principal: DocumentReader, db=Depends(get_db)):
    return {"data": await list_workspace_documents(workspace_id, current_user=principal.user, db=db)}


@router.get("/documents")
async def api_list_documents(
    request: Request,
    principal: DocumentReader,
    db=Depends(get_db),
):
    workspace_id = getattr(request.state, "api_workspace_id", None)
    if workspace_id:
        documents = await list_workspace_documents(
            workspace_id, current_user=principal.user, db=db
        )
        return {
            "data": documents,
            "context": {"workspace_id": workspace_id, "workspace_type": "team"},
        }
    return {
        "data": await list_documents(current_user=principal.user, db=db),
        "context": {"workspace_id": None, "workspace_type": "personal"},
    }


@router.get("/documents/{document_id}")
async def api_get_document(document_id: str, principal: DocumentReader, db=Depends(get_db)):
    return {"data": await get_document(document_id, current_user=principal.user, db=db)}


@router.get("/documents/{document_id}/chunks")
async def api_get_document_chunks(document_id: str, principal: DocumentReader, db=Depends(get_db)):
    return {"data": await get_chunks(document_id, current_user=principal.user, db=db)}


@router.post("/uploads", status_code=201)
async def api_create_upload(
    body: DirectUploadSessionRequest,
    principal: DocumentWriter,
    db=Depends(get_db),
):
    """Create a signed cloud upload URL so file bytes bypass the API service."""
    return await create_direct_upload_session(body, current_user=principal.user, db=db)


@router.post("/uploads/complete", status_code=202)
async def api_complete_upload(
    body: DirectUploadCompleteRequest,
    background_tasks: BackgroundTasks,
    principal: DocumentWriter,
    db=Depends(get_db),
):
    return await complete_direct_upload(
        body,
        background_tasks,
        current_user=principal.user,
        db=db,
    )


@router.post("/documents/{document_id}/embedding", status_code=202)
async def api_embed_document(
    document_id: str,
    background_tasks: BackgroundTasks,
    principal: DocumentWriter,
    db=Depends(get_db),
):
    return await trigger_embedding(
        document_id,
        background_tasks,
        current_user=principal.user,
        db=db,
    )


@router.delete("/documents/{document_id}")
async def api_delete_document(document_id: str, principal: DocumentWriter, db=Depends(get_db)):
    return await delete_document(document_id, current_user=principal.user, db=db)


@router.post("/knowledge/query/stream")
async def api_grounded_query(
    request: Request,
    body: ChatRequest,
    principal: KnowledgeReader,
    db=Depends(get_db),
):
    return await chat_stream_endpoint(request, body, current_user=principal.user, db=db)


@router.post("/summaries/documents/{document_id}/stream")
async def api_summarize_document(
    request: Request,
    document_id: str,
    body: SummarizeRequest,
    principal: KnowledgeGenerator,
    db=Depends(get_db),
):
    return await summarize_document(request, document_id, body, current_user=principal.user, db=db)
