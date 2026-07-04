from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel

from auth.dependencies import CurrentUser
from database.connection import get_db, get_pool
from services.adk_workflow import WorkflowConfigError, load_workflow_config, run_multi_agent_workflow
from services.audit import audit, ip_from, ua_from
from services.chunker import chunk_text
from services.llm import embed
from services.restaurant_agent_tools import RESTAURANT_AGENT_TOOLS
from services.restaurant_intelligence import RestaurantIntelligenceError
from services.notifications import send_restaurant_order_email
from services.text_safety import sanitize_text_for_storage
from services.usage import check_and_log_daily_event, check_document_limit, log_event
from services.vectordb import delete_document_vectors, store_chunk
from services.vertical_agent_runs import (
    approve_vertical_run,
    complete_vertical_run,
    create_vertical_run,
    fail_vertical_run,
    get_accessible_vertical_run,
    run_vertical_step,
    vertical_run_response,
)
import services.storage as gcs


router = APIRouter()
log = logging.getLogger("docintel.restaurant.route")

RESTAURANT_WORKFLOW_ID = "restaurant_menu_scribe_phase1"
RESTAURANT_VERTICAL = "restaurant"
MAX_RESTAURANT_AUDIO_BYTES = int(os.getenv("RESTAURANT_SCRIBE_MAX_MB", "25")) * 1024 * 1024
SUPPORTED_RESTAURANT_AUDIO_TYPES = {
    "audio/webm",
    "audio/mp4",
    "audio/mpeg",
    "audio/mp3",
    "audio/wav",
    "audio/x-wav",
    "audio/ogg",
}


class RestaurantApprovalRequest(BaseModel):
    approved_packet: dict | None = None
    notes: str | None = None


class RestaurantSaveRequest(BaseModel):
    restaurant_profile: dict
    menu_items: list[dict] = []


class RestaurantOrderItemRequest(BaseModel):
    menu_item_id: str | None = None
    item_name: str = ""
    category: str = ""
    unit_price: float | None = None
    currency: str = "USD"
    quantity: int = 1
    quantity_ordered: int | None = None
    instructions: str = ""


class RestaurantOrderDraftRequest(BaseModel):
    restaurant_id: str | None = None
    restaurant_name: str = ""
    workspace_id: str | None = None
    items: list[RestaurantOrderItemRequest]
    customer_name: str = ""
    customer_phone: str = ""
    customer_email: str = ""
    pickup_time_request: str = ""
    special_instructions: str = ""
    notes: str = ""


class RestaurantOrderStatusRequest(BaseModel):
    notes: str = ""
    workspace_id: str | None = None


class RestaurantFeedbackRequest(BaseModel):
    restaurant_id: str
    menu_item_id: str | None = None
    order_id: str | None = None
    rating: int
    feedback_text: str = ""
    language: str = ""
    source_type: str = "text"
    tags: list[str] = []
    signals: dict[str, Any] = {}
    metadata: dict[str, Any] = {}


class RestaurantFeedbackAnalyzeRequest(BaseModel):
    feedback_text: str
    language: str = ""
    current_rating: int | None = None
    restaurant_name: str = ""
    menu_item_name: str = ""


class RestaurantFeedbackStatusRequest(BaseModel):
    status: str = "acknowledged"
    owner_response: str = ""
    workspace_id: str | None = None


@router.post("/scribe-workflow")
async def run_restaurant_scribe_workflow(
    background_tasks: BackgroundTasks,
    request: Request,
    current_user: CurrentUser,
    audio: list[UploadFile] = File(...),
    authorized_confirmed: bool = Form(False),
    language: str = Form(""),
    intake_title: str = Form(""),
    workspace_id: str | None = Form(None),
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    if not authorized_confirmed:
        raise HTTPException(400, "You must confirm you are authorized to publish or update this restaurant menu")
    if workspace_id:
        from routes.workspaces import _require_role
        await _require_role(db, workspace_id, user_id, "editor")

    uploads = audio if isinstance(audio, list) else [audio]
    audio_segments = []
    total_audio_bytes = 0
    for index, upload in enumerate(uploads):
        content_type = (upload.content_type or "application/octet-stream").split(";")[0].strip().lower()
        if content_type not in SUPPORTED_RESTAURANT_AUDIO_TYPES:
            raise HTTPException(400, f"Unsupported audio format: {content_type}")
        content = await upload.read()
        if not content:
            continue
        total_audio_bytes += len(content)
        audio_segments.append({
            "index": index,
            "filename": (upload.filename or f"restaurant-menu-segment-{index + 1}.webm").replace("/", "_").replace("\\", "_"),
            "content_type": content_type,
            "bytes": content,
            "size": len(content),
        })
    if not audio_segments:
        raise HTTPException(400, "No audio received")
    if total_audio_bytes > MAX_RESTAURANT_AUDIO_BYTES:
        raise HTTPException(413, f"Audio is too large. Max {MAX_RESTAURANT_AUDIO_BYTES // 1024 // 1024} MB")

    await check_document_limit(db, user_id, quantity=1)
    await check_and_log_daily_event(
        db,
        user_id,
        "voice_transcription",
        "max_voice_transcriptions_day",
        metadata={"action": "restaurant_menu_scribe", "audio_bytes": total_audio_bytes, "audio_segments": len(audio_segments), "language": language},
    )

    doc_id = str(uuid.uuid4())
    intake_id = str(uuid.uuid4())
    title = (intake_title or "").strip() or f"Restaurant Menu Intake {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    transcript_filename = _safe_filename(title, suffix=".txt")
    transcript_gcs_path = gcs.source_path(user_id, doc_id, transcript_filename)
    audio_gcs_paths = []
    for segment in audio_segments:
        audio_gcs_path = f"users/{user_id}/documents/{doc_id}/restaurant/scribe/{intake_id}/{segment['index'] + 1:03d}_{segment['filename']}"
        await gcs.upload_bytes(audio_gcs_path, segment["bytes"], segment["content_type"])
        segment["gcs_path"] = audio_gcs_path
        audio_gcs_paths.append(audio_gcs_path)
    audio_filename = audio_segments[0]["filename"] if len(audio_segments) == 1 else f"{len(audio_segments)} audio segments"
    audio_gcs_path = audio_gcs_paths[0] if len(audio_gcs_paths) == 1 else f"users/{user_id}/documents/{doc_id}/restaurant/scribe/{intake_id}/"
    content_type = audio_segments[0]["content_type"]

    await db.execute(
        """
        INSERT INTO documents
          (id, user_id, workspace_id, filename, original_name, file_type, file_size,
           gcs_source_path, gcs_chunks_dir, status, doc_type, doc_domain, doc_language, classified_at, doc_metadata)
        VALUES ($1,$2,$3,$4,$5,'text',$6,$7,$8,'chunking','restaurant_menu','restaurant',$9,NOW(),$10::jsonb)
        """,
        doc_id,
        user_id,
        workspace_id,
        transcript_filename,
        title,
        total_audio_bytes,
        transcript_gcs_path,
        gcs.chunks_dir(user_id, doc_id),
        _language_code(language),
        json.dumps({
            "source_kind": "restaurant_menu_scribe",
            "audio_gcs_path": audio_gcs_path,
            "audio_gcs_paths": audio_gcs_paths,
            "audio_filename": audio_filename,
            "audio_mime_type": content_type,
            "audio_segment_count": len(audio_segments),
            "authorized_confirmed": True,
            "intake_id": intake_id,
        }),
    )
    await log_event(db, user_id, "upload", metadata={
        "doc_id": doc_id,
        "filename": transcript_filename,
        "file_size": total_audio_bytes,
        "file_type": "restaurant_menu_audio",
        "source_kind": "restaurant_menu_scribe",
    })

    config = load_workflow_config(RESTAURANT_WORKFLOW_ID)
    run = await create_vertical_run(
        db,
        workflow_id=RESTAURANT_WORKFLOW_ID,
        workflow_version=config.get("version") or "restaurant-menu-scribe-v1",
        vertical=RESTAURANT_VERTICAL,
        document_id=doc_id,
        user_id=user_id,
        workspace_id=workspace_id,
        input_data={
            "document_name": title,
            "doc_type": "restaurant_menu",
            "doc_domain": "restaurant",
            "audio_filename": audio_filename,
            "audio_gcs_path": audio_gcs_path,
            "audio_gcs_paths": audio_gcs_paths,
            "audio_mime_type": content_type,
            "audio_size": total_audio_bytes,
            "audio_segment_count": len(audio_segments),
            "language": language,
            "authorized_confirmed": True,
            "intake_id": intake_id,
        },
    )
    run_id = str(run["id"])
    background_tasks.add_task(
        _execute_restaurant_workflow_background,
        run_id,
        doc_id,
        user_id,
        audio_segments,
        audio_gcs_path,
        audio_filename,
        content_type,
        total_audio_bytes,
        language,
        transcript_gcs_path,
        transcript_filename,
        workspace_id,
        ip_from(request),
        ua_from(request),
    )
    response = await vertical_run_response(db, run)
    response["created_document"] = {"doc_id": doc_id, "filename": transcript_filename, "original_name": title}
    return response


@router.get("/agent-runs/{run_id}")
async def get_restaurant_agent_run(run_id: str, current_user: CurrentUser, db=Depends(get_db)):
    run = await get_accessible_vertical_run(db, run_id, str(current_user["id"]))
    if run.get("vertical") != RESTAURANT_VERTICAL:
        raise HTTPException(404, "Restaurant agent run not found")
    return await vertical_run_response(db, run)


@router.post("/agent-runs/{run_id}/approve")
async def approve_restaurant_agent_run(
    run_id: str,
    body: RestaurantApprovalRequest,
    request: Request,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    run = await get_accessible_vertical_run(db, run_id, user_id)
    if run.get("vertical") != RESTAURANT_VERTICAL:
        raise HTTPException(404, "Restaurant agent run not found")
    workspace_id = str(run["workspace_id"]) if run.get("workspace_id") else None
    workflow_owner_id = str(run["user_id"])
    if workspace_id:
        from routes.workspaces import _require_role
        await _require_role(db, workspace_id, user_id, "editor")
    result_data = _json(run.get("result_data")) or {}
    approved_packet = body.approved_packet or result_data.get("approved_packet")
    if not approved_packet:
        raise HTTPException(400, "No restaurant/menu packet available to approve")

    restaurant_owner_id = workflow_owner_id if workspace_id else user_id
    restaurant_id = await _save_restaurant_packet(db, restaurant_owner_id, workspace_id, run_id, approved_packet)
    approved_packet = {**approved_packet, "restaurant_id": restaurant_id}
    await approve_vertical_run(db, run_id=run_id, user_id=user_id, approved_packet=approved_packet, notes=body.notes)
    await _persist_approved_restaurant_document(db, str(run["document_id"]), workflow_owner_id, workspace_id, approved_packet)
    await audit(
        db,
        user_id=user_id,
        action="restaurant_menu_approve",
        resource_type="restaurant",
        resource_id=restaurant_id,
        metadata={"run_id": run_id, "document_id": str(run["document_id"])},
        ip_address=ip_from(request),
        user_agent=ua_from(request),
    )
    fresh = await get_accessible_vertical_run(db, run_id, user_id)
    response = await vertical_run_response(db, fresh)
    response["restaurant_id"] = restaurant_id
    return response


@router.get("/restaurants")
async def list_restaurants(current_user: CurrentUser, workspace_id: str | None = Query(None), db=Depends(get_db)):
    user_id = str(current_user["id"])
    user_email = str(current_user.get("email") or "")
    restored_count = 0
    if await _can_restore_restaurants(db, user_id, workspace_id):
        restored_count = await _restore_approved_restaurants_if_needed(db, user_id, workspace_id)
    rows = await db.fetch(
        f"""
        SELECT r.*,
               wm.role AS viewer_role,
               COUNT(mi.id)::int AS menu_count,
               MIN(mi.price) FILTER (WHERE mi.price IS NOT NULL) AS min_price,
               MAX(mi.price) FILTER (WHERE mi.price IS NOT NULL) AS max_price,
               COALESCE(fb.rating_count, 0)::int AS rating_count,
               fb.avg_rating AS avg_rating,
               COALESCE(fb.verified_rating_count, 0)::int AS verified_rating_count
        FROM restaurants r
        LEFT JOIN restaurant_menu_items mi ON mi.restaurant_id=r.id
        LEFT JOIN workspace_members wm ON wm.workspace_id=r.workspace_id AND wm.user_id=$1::uuid
        LEFT JOIN (
            SELECT restaurant_id,
                   COUNT(*)::int AS rating_count,
                   ROUND(AVG(rating)::numeric, 2) AS avg_rating,
                   COUNT(*) FILTER (WHERE verified_order)::int AS verified_rating_count
            FROM restaurant_feedback
            WHERE status <> 'dismissed'
            GROUP BY restaurant_id
        ) fb ON fb.restaurant_id=r.id
        WHERE (($2::uuid IS NULL AND r.workspace_id IS NULL) OR r.workspace_id=$2::uuid)
        GROUP BY r.id, wm.role, fb.rating_count, fb.avg_rating, fb.verified_rating_count
        ORDER BY r.workspace_id NULLS LAST, r.updated_at DESC
        LIMIT 200
        """,
        user_id,
        workspace_id,
    )
    restaurant_rows = []
    for row in rows:
        data = _restaurant_row(row)
        data["can_manage"] = _restaurant_can_manage(dict(row), user_id, user_email)
        restaurant_rows.append(data)
    return {"restaurants": restaurant_rows, "restored_count": restored_count}


@router.get("/debug/restore-status")
async def restaurant_restore_status(current_user: CurrentUser, workspace_id: str | None = Query(None), db=Depends(get_db)):
    user_id = str(current_user["id"])
    counts = await db.fetchrow(
        f"""
        SELECT
          COUNT(DISTINCT r.id)::int AS restaurants,
          COUNT(mi.id)::int AS menu_items,
          COUNT(DISTINCT r.id) FILTER (WHERE r.workspace_id IS NULL)::int AS personal_restaurants,
          COUNT(DISTINCT r.id) FILTER (WHERE r.workspace_id IS NOT NULL)::int AS workspace_restaurants
        FROM restaurants r
        LEFT JOIN restaurant_menu_items mi ON mi.restaurant_id=r.id
        WHERE {_restaurant_access_sql("r")}
          AND ($2::uuid IS NULL OR r.workspace_id=$2::uuid OR r.workspace_id IS NULL)
        """,
        user_id,
        workspace_id,
    )
    runs = await db.fetchrow(
        f"""
        SELECT
          COUNT(*)::int AS total_runs,
          COUNT(*) FILTER (WHERE status='approved')::int AS approved_runs,
          COUNT(*) FILTER (WHERE status='pending_approval')::int AS pending_approval_runs,
          COUNT(*) FILTER (WHERE result_data ? 'approved_packet')::int AS approved_packet_runs,
          COUNT(*) FILTER (WHERE result_data ? 'review_packet')::int AS review_packet_runs,
          COUNT(*) FILTER (WHERE result_data ? 'restaurant_profile' AND result_data ? 'menu_items')::int AS direct_packet_runs
        FROM vertical_agent_runs r
        WHERE r.vertical=$2
          AND ($3::uuid IS NULL OR r.workspace_id=$3::uuid OR r.workspace_id IS NULL)
          AND {_restaurant_access_sql("r")}
        """,
        user_id,
        RESTAURANT_VERTICAL,
        workspace_id,
    )
    docs = await db.fetchrow(
        """
        SELECT
          COUNT(*)::int AS restaurant_documents,
          COUNT(*) FILTER (WHERE status='deleted')::int AS deleted_restaurant_documents,
          COUNT(*) FILTER (WHERE status='error')::int AS error_restaurant_documents
        FROM documents
        WHERE user_id=$1::uuid
          AND doc_domain='restaurant'
          AND ($2::uuid IS NULL OR workspace_id=$2::uuid OR workspace_id IS NULL)
        """,
        user_id,
        workspace_id,
    )
    audit_rows = await db.fetch(
        """
        SELECT action, resource_type, resource_id, metadata, created_at
        FROM audit_log
        WHERE user_id=$1::uuid
          AND action IN ('restaurant_menu_delete', 'restaurant_menu_update', 'delete_document', 'restaurant_menu_approve')
        ORDER BY created_at DESC
        LIMIT 20
        """,
        user_id,
    )
    return {
        "workspace_id": workspace_id,
        "restaurants": dict(counts or {}),
        "restaurant_runs": dict(runs or {}),
        "restaurant_documents": dict(docs or {}),
        "recent_audit_events": [_order_row(row) for row in audit_rows],
        "possible_causes": _restaurant_missing_data_causes(
            dict(counts or {}),
            dict(runs or {}),
            dict(docs or {}),
            [dict(row) for row in audit_rows],
        ),
    }


@router.get("/restaurants/{restaurant_id}")
async def get_restaurant(
    restaurant_id: str,
    current_user: CurrentUser,
    workspace_id: str | None = Query(None),
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    user_email = str(current_user.get("email") or "")
    row = await db.fetchrow(
        """
        SELECT r.*, wm.role AS viewer_role,
               COALESCE(fb.rating_count, 0)::int AS rating_count,
               fb.avg_rating AS avg_rating,
               COALESCE(fb.verified_rating_count, 0)::int AS verified_rating_count
        FROM restaurants r
        LEFT JOIN workspace_members wm ON wm.workspace_id=r.workspace_id AND wm.user_id=$2::uuid
        LEFT JOIN (
            SELECT restaurant_id,
                   COUNT(*)::int AS rating_count,
                   ROUND(AVG(rating)::numeric, 2) AS avg_rating,
                   COUNT(*) FILTER (WHERE verified_order)::int AS verified_rating_count
            FROM restaurant_feedback
            WHERE status <> 'dismissed'
            GROUP BY restaurant_id
        ) fb ON fb.restaurant_id=r.id
        WHERE r.id=$1
          AND (($3::uuid IS NULL AND r.workspace_id IS NULL) OR r.workspace_id=$3::uuid)
        """,
        restaurant_id,
        user_id,
        workspace_id,
    )
    if not row:
        raise HTTPException(404, "Restaurant not found")
    items = await db.fetch(
        """
        SELECT mi.*,
               COALESCE(fb.rating_count, 0)::int AS rating_count,
               fb.avg_rating AS avg_rating,
               COALESCE(fb.verified_rating_count, 0)::int AS verified_rating_count
        FROM restaurant_menu_items mi
        LEFT JOIN (
            SELECT menu_item_id,
                   COUNT(*)::int AS rating_count,
                   ROUND(AVG(rating)::numeric, 2) AS avg_rating,
                   COUNT(*) FILTER (WHERE verified_order)::int AS verified_rating_count
            FROM restaurant_feedback
            WHERE status <> 'dismissed' AND menu_item_id IS NOT NULL
            GROUP BY menu_item_id
        ) fb ON fb.menu_item_id=mi.id
        WHERE mi.restaurant_id=$1
        ORDER BY COALESCE(NULLIF(category, ''), 'zzz'), item_name
        """,
        restaurant_id,
    )
    can_manage = _restaurant_can_manage(dict(row), user_id, user_email)
    restaurant = _restaurant_row(row)
    restaurant["can_manage"] = can_manage
    return {
        "restaurant": restaurant,
        "menu_items": [_menu_row(item) for item in items],
        "transcript": await _restaurant_source_transcript(db, dict(row), user_id) if can_manage else "",
    }


@router.put("/restaurants/{restaurant_id}")
async def update_restaurant(
    restaurant_id: str,
    body: RestaurantSaveRequest,
    request: Request,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    user_email = str(current_user.get("email") or "")
    row = await db.fetchrow(
        f"""
        SELECT r.*, wm.role AS viewer_role
        FROM restaurants r
        LEFT JOIN workspace_members wm ON wm.workspace_id=r.workspace_id AND wm.user_id=$2::uuid
        WHERE r.id=$1 AND {_restaurant_catalog_manage_sql('r', '$2', '$3')}
        """,
        restaurant_id,
        user_id,
        user_email,
    )
    if not row:
        raise HTTPException(403, "Only a matching restaurant email or workspace owner/editor can update this restaurant")
    profile = body.restaurant_profile or {}
    name = str(profile.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Restaurant name is required")
    email = _required_restaurant_email(profile)
    await db.execute(
        """
        UPDATE restaurants
        SET name=$2, description=$3, cuisine_type=$4, address=$5, phone=$6,
            email=$7, website=$8, hours=$9::jsonb, service_options=$10::jsonb,
            payment_options=$11::jsonb, metadata=COALESCE(metadata, '{}'::jsonb) || $12::jsonb,
            updated_at=NOW()
        WHERE id=$1
        """,
        restaurant_id,
        name,
        profile.get("description") or "",
        profile.get("cuisine_type") or "",
        profile.get("address") or "",
        profile.get("phone") or "",
        email,
        profile.get("website") or "",
        json.dumps(profile.get("hours") if isinstance(profile.get("hours"), dict) else {}),
        json.dumps(profile.get("service_options") if isinstance(profile.get("service_options"), list) else []),
        json.dumps(profile.get("payment_options") if isinstance(profile.get("payment_options"), list) else []),
        json.dumps({"last_manual_update_at": datetime.utcnow().isoformat() + "Z"}),
    )
    await db.execute("DELETE FROM restaurant_menu_items WHERE restaurant_id=$1", restaurant_id)
    for item in body.menu_items or []:
        if not isinstance(item, dict) or not str(item.get("item_name") or "").strip():
            continue
        await db.execute(
            """
            INSERT INTO restaurant_menu_items
              (restaurant_id, user_id, workspace_id, category, item_name, price, currency,
               quantity, description, ingredients, dietary_tags, spice_level, availability, options, metadata)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11::jsonb,$12,$13,$14::jsonb,$15::jsonb)
            """,
            restaurant_id,
            user_id,
            row.get("workspace_id"),
            item.get("category") or "",
            item.get("item_name") or "",
            item.get("price"),
            item.get("currency") or "USD",
            item.get("quantity") or "",
            item.get("description") or "",
            json.dumps(item.get("ingredients") if isinstance(item.get("ingredients"), list) else []),
            json.dumps(item.get("dietary_tags") if isinstance(item.get("dietary_tags"), list) else []),
            item.get("spice_level") or "",
            item.get("availability") or "available",
            json.dumps(item.get("options") if isinstance(item.get("options"), list) else []),
            json.dumps({"source": "manual_update"}),
        )
    await audit(
        db,
        user_id=user_id,
        action="restaurant_menu_update",
        resource_type="restaurant",
        resource_id=restaurant_id,
        metadata={"menu_count": len(body.menu_items or [])},
        ip_address=ip_from(request),
        user_agent=ua_from(request),
    )
    return await get_restaurant(
        restaurant_id,
        current_user,
        str(row["workspace_id"]) if row.get("workspace_id") else None,
        db,
    )


@router.delete("/restaurants/{restaurant_id}")
async def delete_restaurant(
    restaurant_id: str,
    request: Request,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    user_email = str(current_user.get("email") or "")
    row = await db.fetchrow(
        f"""
        SELECT r.*, wm.role AS viewer_role
        FROM restaurants r
        LEFT JOIN workspace_members wm ON wm.workspace_id=r.workspace_id AND wm.user_id=$2::uuid
        WHERE r.id=$1 AND {_restaurant_catalog_manage_sql('r', '$2', '$3')}
        """,
        restaurant_id,
        user_id,
        user_email,
    )
    if not row:
        raise HTTPException(403, "Only a matching restaurant email or workspace owner/editor can delete this restaurant")
    source_doc_rows = await db.fetch(
        """
        SELECT DISTINCT d.id, d.user_id
        FROM documents d
        WHERE d.id IN (
            SELECT r.document_id
            FROM vertical_agent_runs r
            WHERE r.id=$1
        )
        OR d.doc_metadata->>'restaurant_id'=$2
        """,
        row.get("source_run_id"),
        restaurant_id,
    )

    warnings: list[str] = []
    for doc in source_doc_rows:
        doc_id = str(doc["id"])
        doc_user_id = str(doc["user_id"])
        try:
            await gcs.delete_prefix(f"users/{doc_user_id}/documents/{doc_id}/")
        except Exception as exc:
            warnings.append(f"GCS cleanup skipped for document {doc_id}: {exc}")
            log.warning("Restaurant delete GCS cleanup failed restaurant_id=%s doc_id=%s: %s", restaurant_id, doc_id, exc)
        try:
            await delete_document_vectors(doc_id)
        except Exception as exc:
            warnings.append(f"Vector cleanup skipped for document {doc_id}: {exc}")
            log.warning("Restaurant delete vector cleanup failed restaurant_id=%s doc_id=%s: %s", restaurant_id, doc_id, exc)

    async with db.transaction():
        for doc in source_doc_rows:
            await db.execute("DELETE FROM documents WHERE id=$1", str(doc["id"]))
        await db.execute("DELETE FROM restaurants WHERE id=$1", restaurant_id)

    await audit(
        db,
        user_id=user_id,
        action="restaurant_menu_delete",
        resource_type="restaurant",
        resource_id=restaurant_id,
        metadata={
            "name": row.get("name"),
            "deleted_document_ids": [str(doc["id"]) for doc in source_doc_rows],
            "cleanup_warnings": warnings,
        },
        ip_address=ip_from(request),
        user_agent=ua_from(request),
    )
    return {
        "deleted": True,
        "restaurant_id": restaurant_id,
        "deleted_document_ids": [str(doc["id"]) for doc in source_doc_rows],
        "warnings": warnings,
    }


@router.get("/menu/search")
async def search_menu(
    current_user: CurrentUser,
    query: str = Query(""),
    cuisine_type: str = Query(""),
    dietary_tag: str = Query(""),
    max_price: float | None = Query(None),
    workspace_id: str | None = Query(None),
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    if await _can_restore_restaurants(db, user_id, workspace_id):
        await _restore_approved_restaurants_if_needed(db, user_id, workspace_id)
    q = f"%{query.strip()}%" if query.strip() else "%"
    rows = await db.fetch(
        f"""
        SELECT mi.*, r.name AS restaurant_name, r.address, r.address AS restaurant_address,
               r.phone AS restaurant_phone, r.email AS restaurant_email, r.cuisine_type,
               COALESCE(fb.rating_count, 0)::int AS rating_count,
               fb.avg_rating AS avg_rating,
               COALESCE(fb.verified_rating_count, 0)::int AS verified_rating_count,
               COALESCE(fb.feedback_signals, '[]'::jsonb) AS feedback_signals
        FROM restaurant_menu_items mi
        JOIN restaurants r ON r.id=mi.restaurant_id
        LEFT JOIN (
            SELECT menu_item_id,
                   COUNT(*)::int AS rating_count,
                   ROUND(AVG(rating)::numeric, 2) AS avg_rating,
                   COUNT(*) FILTER (WHERE verified_order)::int AS verified_rating_count,
                   COALESCE(jsonb_agg(signals) FILTER (WHERE signals <> '{{}}'::jsonb), '[]'::jsonb) AS feedback_signals
            FROM restaurant_feedback
            WHERE status <> 'dismissed' AND menu_item_id IS NOT NULL
            GROUP BY menu_item_id
        ) fb ON fb.menu_item_id=mi.id
        WHERE TRUE
          AND ($1='' OR mi.item_name ILIKE $2 OR mi.description ILIKE $2 OR r.name ILIKE $2)
          AND ($3='' OR r.cuisine_type ILIKE '%' || $3 || '%')
          AND ($4='' OR EXISTS (
              SELECT 1 FROM jsonb_array_elements_text(mi.dietary_tags) tag
              WHERE tag ILIKE '%' || $4 || '%'
          ))
          AND ($5::numeric IS NULL OR mi.price <= $5::numeric)
          AND ($6::uuid IS NULL OR r.workspace_id=$6::uuid OR r.workspace_id IS NULL)
        ORDER BY r.workspace_id NULLS LAST, mi.price NULLS LAST, r.name, mi.item_name
        LIMIT 100
        """,
        query.strip(),
        q,
        cuisine_type.strip(),
        dietary_tag.strip(),
        max_price,
        workspace_id,
    )
    return {"items": [_menu_search_row(row) for row in rows]}


@router.get("/menu/compare")
async def compare_menu_prices(
    current_user: CurrentUser,
    query: str = Query(..., min_length=1),
    cuisine_type: str = Query(""),
    workspace_id: str | None = Query(None),
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    if await _can_restore_restaurants(db, user_id, workspace_id):
        await _restore_approved_restaurants_if_needed(db, user_id, workspace_id)
    q = f"%{query.strip()}%"
    rows = await db.fetch(
        f"""
        SELECT mi.*, r.name AS restaurant_name, r.address, r.address AS restaurant_address,
               r.phone AS restaurant_phone, r.email AS restaurant_email, r.cuisine_type,
               COALESCE(fb.rating_count, 0)::int AS rating_count,
               fb.avg_rating AS avg_rating,
               COALESCE(fb.verified_rating_count, 0)::int AS verified_rating_count,
               COALESCE(fb.feedback_signals, '[]'::jsonb) AS feedback_signals
        FROM restaurant_menu_items mi
        JOIN restaurants r ON r.id=mi.restaurant_id
        LEFT JOIN (
            SELECT menu_item_id,
                   COUNT(*)::int AS rating_count,
                   ROUND(AVG(rating)::numeric, 2) AS avg_rating,
                   COUNT(*) FILTER (WHERE verified_order)::int AS verified_rating_count,
                   COALESCE(jsonb_agg(signals) FILTER (WHERE signals <> '{{}}'::jsonb), '[]'::jsonb) AS feedback_signals
            FROM restaurant_feedback
            WHERE status <> 'dismissed' AND menu_item_id IS NOT NULL
            GROUP BY menu_item_id
        ) fb ON fb.menu_item_id=mi.id
        WHERE TRUE
          AND (mi.item_name ILIKE $1 OR mi.description ILIKE $1)
          AND ($2='' OR r.cuisine_type ILIKE '%' || $2 || '%')
          AND ($3::uuid IS NULL OR r.workspace_id=$3::uuid OR r.workspace_id IS NULL)
        ORDER BY r.workspace_id NULLS LAST, mi.price NULLS LAST, r.name, mi.item_name
        LIMIT 100
        """,
        q,
        cuisine_type.strip(),
        workspace_id,
    )
    items = [_menu_search_row(row) for row in rows]
    prices = [item["price"] for item in items if item.get("price") is not None]
    return {
        "query": query,
        "count": len(items),
        "lowest_price": min(prices) if prices else None,
        "highest_price": max(prices) if prices else None,
        "items": items,
    }


@router.get("/menu/recommend")
async def recommend_restaurant_menu(
    current_user: CurrentUser,
    query: str = Query("", description="Menu item, cuisine, craving, or dietary preference"),
    cuisine_type: str = Query(""),
    max_price: float | None = Query(None),
    workspace_id: str | None = Query(None),
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    if await _can_restore_restaurants(db, user_id, workspace_id):
        await _restore_approved_restaurants_if_needed(db, user_id, workspace_id)
    q = f"%{query.strip()}%" if query.strip() else "%"
    rows = await db.fetch(
        f"""
        SELECT mi.*, r.name AS restaurant_name, r.address, r.address AS restaurant_address,
               r.phone AS restaurant_phone, r.email AS restaurant_email, r.cuisine_type,
               COALESCE(fb.rating_count, 0)::int AS rating_count,
               fb.avg_rating AS avg_rating,
               COALESCE(fb.verified_rating_count, 0)::int AS verified_rating_count
        FROM restaurant_menu_items mi
        JOIN restaurants r ON r.id=mi.restaurant_id
        LEFT JOIN (
            SELECT menu_item_id,
                   COUNT(*)::int AS rating_count,
                   ROUND(AVG(rating)::numeric, 2) AS avg_rating,
                   COUNT(*) FILTER (WHERE verified_order)::int AS verified_rating_count
            FROM restaurant_feedback
            WHERE status <> 'dismissed' AND menu_item_id IS NOT NULL
            GROUP BY menu_item_id
        ) fb ON fb.menu_item_id=mi.id
        WHERE TRUE
          AND ($1='' OR mi.item_name ILIKE $2 OR mi.description ILIKE $2 OR r.name ILIKE $2 OR r.cuisine_type ILIKE $2)
          AND ($3='' OR r.cuisine_type ILIKE '%' || $3 || '%')
          AND ($4::numeric IS NULL OR mi.price <= $4::numeric)
          AND ($5::uuid IS NULL OR r.workspace_id=$5::uuid OR r.workspace_id IS NULL)
          AND LOWER(COALESCE(mi.availability, 'available')) <> 'unavailable'
        LIMIT 150
        """,
        query.strip(),
        q,
        cuisine_type.strip(),
        max_price,
        workspace_id,
    )
    items = []
    prices = [float(row["price"]) for row in rows if row["price"] is not None]
    max_seen_price = max(prices) if prices else None
    for row in rows:
        item = _menu_search_row(row)
        item["recommendation_score"] = _restaurant_recommendation_score(item, query, max_seen_price)
        item["recommendation_reason"] = _restaurant_recommendation_reason(item)
        items.append(item)
    items.sort(key=lambda item: (-float(item.get("recommendation_score") or 0), item.get("price") is None, item.get("price") or 0, item.get("restaurant_name") or ""))
    return {"query": query, "count": len(items), "items": items[:50]}


@router.post("/orders/draft")
async def create_restaurant_order_draft(
    body: RestaurantOrderDraftRequest,
    request: Request,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    if not body.items:
        raise HTTPException(400, "Add at least one menu item to the cart")

    item_ids = []
    for item in body.items:
        raw = str(item.menu_item_id or "").strip()
        if not raw:
            continue
        try:
            item_ids.append(UUID(raw))
        except ValueError:
            if not (item.item_name or "").strip():
                raise HTTPException(400, f"Invalid menu item id: {raw}")
            item.menu_item_id = None
    ad_hoc_items = [item for item in body.items if not str(item.menu_item_id or "").strip()]
    if ad_hoc_items and not body.restaurant_id:
        body.restaurant_id = await _resolve_order_restaurant_id(
            db,
            user_id=user_id,
            restaurant_name=body.restaurant_name,
            workspace_id=body.workspace_id,
        )
    quantity_by_item = {str(item.menu_item_id): _order_item_quantity(item) for item in body.items if item.menu_item_id}
    instructions_by_item = {str(item.menu_item_id): item.instructions or "" for item in body.items if item.menu_item_id}

    menu_rows = []
    if item_ids:
        menu_rows = await db.fetch(
            f"""
            SELECT mi.*, r.name AS restaurant_name, r.address, r.phone AS restaurant_phone,
                   r.email AS restaurant_email, r.user_id AS restaurant_owner_id,
                   r.workspace_id AS restaurant_workspace_id
            FROM restaurant_menu_items mi
            JOIN restaurants r ON r.id=mi.restaurant_id
            WHERE mi.id = ANY($1::uuid[])
            ORDER BY mi.item_name
            """,
            item_ids,
        )
        if len(menu_rows) != len({str(item_id) for item_id in item_ids}):
            found_item_ids = {str(row["id"]) for row in menu_rows}
            missing_item_ids = {str(item_id) for item_id in item_ids} - found_item_ids
            if not body.restaurant_id:
                raise HTTPException(404, "One or more menu items were not found or are not accessible")
            for item in body.items:
                if str(item.menu_item_id or "") in missing_item_ids and (item.item_name or "").strip():
                    item.menu_item_id = None
            unresolved_missing = [
                str(item.menu_item_id)
                for item in body.items
                if str(item.menu_item_id or "") in missing_item_ids
            ]
            if unresolved_missing:
                raise HTTPException(404, "One or more menu items were not found or are not accessible")
            ad_hoc_items = [item for item in body.items if not str(item.menu_item_id or "").strip()]
            quantity_by_item = {str(item.menu_item_id): _order_item_quantity(item) for item in body.items if item.menu_item_id}
            instructions_by_item = {str(item.menu_item_id): item.instructions or "" for item in body.items if item.menu_item_id}

    restaurant_ids = {str(row["restaurant_id"]) for row in menu_rows}
    if ad_hoc_items:
        restaurant_ids.add(str(body.restaurant_id))
    if body.restaurant_id and str(body.restaurant_id) not in restaurant_ids:
        raise HTTPException(400, "Selected items do not belong to the requested restaurant")
    if len(restaurant_ids) != 1:
        raise HTTPException(400, "A carryout cart can include items from one restaurant only")

    restaurant_id = next(iter(restaurant_ids))
    restaurant = await db.fetchrow(
        f"""
        SELECT id, name AS restaurant_name, address, phone AS restaurant_phone,
               email AS restaurant_email, user_id AS restaurant_owner_id,
               workspace_id, workspace_id AS restaurant_workspace_id
        FROM restaurants r
        WHERE r.id=$1::uuid
        """,
        restaurant_id,
    )
    if not restaurant:
        raise HTTPException(404, "Restaurant was not found or is not accessible")
    requested_workspace_id = _id_text(body.workspace_id)
    restaurant_workspace_raw = _row_get(restaurant, "workspace_id")
    restaurant_workspace_id = str(restaurant_workspace_raw) if restaurant_workspace_raw else None
    if requested_workspace_id != restaurant_workspace_id:
        raise HTTPException(403, "Carryout orders must be created in the same workspace as the restaurant menu")
    for row in menu_rows:
        menu_workspace_raw = _row_get(row, "restaurant_workspace_id")
        menu_workspace_id = str(menu_workspace_raw) if menu_workspace_raw else None
        if menu_workspace_id != restaurant_workspace_id:
            raise HTTPException(400, "All menu items must belong to the selected restaurant workspace")
    first = menu_rows[0] if menu_rows else restaurant
    workspace_id = restaurant_workspace_id
    subtotal = Decimal("0.00")
    currency = "USD"
    order_id = str(uuid.uuid4())

    async with db.transaction():
        for row in menu_rows:
            price = row["price"]
            qty = quantity_by_item.get(str(row["id"]), 1)
            currency = row["currency"] or currency
            if price is not None:
                subtotal += Decimal(str(price)) * qty
        for item in ad_hoc_items:
            if item.unit_price is not None:
                subtotal += Decimal(str(item.unit_price)) * _order_item_quantity(item)
            currency = item.currency or currency

        await db.execute(
            """
            INSERT INTO restaurant_orders
              (id, restaurant_id, customer_user_id, workspace_id, status, fulfillment_type,
               customer_name, customer_phone, customer_email, pickup_time_request,
               special_instructions, subtotal, currency, metadata)
            VALUES ($1,$2,$3,$4,'draft','carryout',$5,$6,$7,$8,$9,$10,$11,$12::jsonb)
            """,
            order_id,
            restaurant_id,
            user_id,
            workspace_id,
            body.customer_name or "",
            body.customer_phone or "",
            body.customer_email or current_user.get("email") or "",
            body.pickup_time_request or "",
            body.special_instructions or "",
            subtotal,
            currency,
            json.dumps({"source": "docintel_conversation_cart", "notes": body.notes or ""}),
        )

        for row in menu_rows:
            menu_item_id = str(row["id"])
            qty = quantity_by_item.get(menu_item_id, 1)
            unit_price = row["price"]
            line_total = Decimal("0.00") if unit_price is None else Decimal(str(unit_price)) * qty
            await db.execute(
                """
                INSERT INTO restaurant_order_items
                  (order_id, menu_item_id, item_name, category, quantity, unit_price,
                   line_total, currency, instructions, metadata)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb)
                """,
                order_id,
                menu_item_id,
                row["item_name"],
                row["category"] or "",
                qty,
                unit_price,
                line_total,
                row["currency"] or currency,
                instructions_by_item.get(menu_item_id, ""),
                json.dumps({"restaurant_name": _row_get(first, "restaurant_name")}),
            )
        for item in ad_hoc_items:
            item_name = (item.item_name or "").strip()
            if not item_name:
                raise HTTPException(400, "Item name is required for chat-derived order items")
            qty = _order_item_quantity(item)
            unit_price = Decimal(str(item.unit_price)) if item.unit_price is not None else None
            line_total = Decimal("0.00") if unit_price is None else unit_price * qty
            await db.execute(
                """
                INSERT INTO restaurant_order_items
                  (order_id, menu_item_id, item_name, category, quantity, unit_price,
                   line_total, currency, instructions, metadata)
                VALUES ($1,NULL,$2,$3,$4,$5,$6,$7,$8,$9::jsonb)
                """,
                order_id,
                item_name,
                item.category or "",
                qty,
                unit_price,
                line_total,
                item.currency or currency,
                item.instructions or "",
                json.dumps({"restaurant_name": _row_get(first, "restaurant_name"), "source": "chat_answer"}),
            )
        await _add_order_event(db, order_id, user_id, "draft_created", None, "draft", body.notes or "", {"item_count": len(menu_rows) + len(ad_hoc_items)})

    await audit(
        db,
        user_id=user_id,
        action="restaurant_order_draft",
        resource_type="restaurant_order",
        resource_id=order_id,
        metadata={"restaurant_id": restaurant_id, "subtotal": float(subtotal), "fulfillment_type": "carryout"},
        ip_address=ip_from(request),
        user_agent=ua_from(request),
    )
    return await _fetch_order_response(db, order_id, user_id, workspace_id=workspace_id)


@router.post("/orders/{order_id}/submit")
async def submit_restaurant_order(
    order_id: str,
    body: RestaurantOrderStatusRequest,
    request: Request,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    user_email = str(current_user.get("email") or "")
    order = await _fetch_order_record(db, order_id, user_id, user_email, body.workspace_id)
    if not order:
        raise HTTPException(404, "Order not found")
    if str(order["customer_user_id"]) != user_id:
        raise HTTPException(403, "Only the customer can submit this order")
    if order["status"] != "draft":
        raise HTTPException(400, f"Only draft orders can be submitted; current status is {order['status']}")

    await _transition_order(
        db,
        order,
        actor_id=user_id,
        to_status="submitted",
        event_type="submitted",
        notes=body.notes or "Customer submitted carryout order",
        timestamp_column="submitted_at",
        notify_owner=True,
    )
    await _safe_order_notification(
        _notify_restaurant_customer(db, order, user_id, "submitted", "submitted"),
        order,
        "customer submitted",
    )
    await audit(
        db,
        user_id=user_id,
        action="restaurant_order_submit",
        resource_type="restaurant_order",
        resource_id=order_id,
        metadata={"restaurant_id": str(order["restaurant_id"]), "fulfillment_type": "carryout"},
        ip_address=ip_from(request),
        user_agent=ua_from(request),
    )
    return await _fetch_order_response(db, order_id, user_id, user_email, body.workspace_id)


@router.post("/feedback/analyze")
async def analyze_restaurant_feedback(body: RestaurantFeedbackAnalyzeRequest, current_user: CurrentUser):
    text = sanitize_text_for_storage(body.feedback_text or "").strip()
    if not text:
        raise HTTPException(400, "Feedback text is required")
    analysis = await _analyze_restaurant_feedback_text(
        text,
        language=body.language,
        current_rating=body.current_rating,
        restaurant_name=body.restaurant_name,
        menu_item_name=body.menu_item_name,
    )
    return {"analysis": analysis}


@router.post("/feedback")
async def submit_restaurant_feedback(
    body: RestaurantFeedbackRequest,
    request: Request,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    if body.rating < 1 or body.rating > 5:
        raise HTTPException(400, "Rating must be between 1 and 5")
    restaurant = await db.fetchrow(
        """
        SELECT r.*, wm.role AS viewer_role
        FROM restaurants r
        LEFT JOIN workspace_members wm ON wm.workspace_id=r.workspace_id AND wm.user_id=$2::uuid
        WHERE r.id=$1::uuid
          AND (r.user_id=$2::uuid OR r.workspace_id IS NULL OR wm.user_id=$2::uuid)
        """,
        body.restaurant_id,
        user_id,
    )
    if not restaurant:
        raise HTTPException(404, "Restaurant not found or not accessible")
    menu_item_id = _id_text(body.menu_item_id)
    if menu_item_id:
        exists = await db.fetchval(
            "SELECT 1 FROM restaurant_menu_items WHERE id=$1::uuid AND restaurant_id=$2::uuid",
            menu_item_id,
            body.restaurant_id,
        )
        if not exists:
            raise HTTPException(400, "Menu item does not belong to this restaurant")
    order_id = _id_text(body.order_id)
    verified_order = False
    if order_id:
        verified_order = bool(await db.fetchval(
            """
            SELECT 1
            FROM restaurant_orders o
            LEFT JOIN restaurant_order_items oi ON oi.order_id=o.id
            WHERE o.id=$1::uuid
              AND o.restaurant_id=$2::uuid
              AND o.customer_user_id=$3::uuid
              AND ($4::uuid IS NULL OR oi.menu_item_id=$4::uuid)
            """,
            order_id,
            body.restaurant_id,
            user_id,
            menu_item_id,
        ))
        if not verified_order:
            raise HTTPException(403, "Order is not linked to this customer, restaurant, or menu item")
    feedback_id = str(uuid.uuid4())
    workspace_id = str(restaurant["workspace_id"]) if restaurant.get("workspace_id") else None
    signals = body.signals if isinstance(body.signals, dict) else {}
    if body.feedback_text and not signals.get("suggested_rating"):
        analysis = await _analyze_restaurant_feedback_text(
            body.feedback_text,
            language=body.language,
            current_rating=body.rating,
            prefer_gemini=False,
        )
        signals = {**analysis, **signals}
    await db.execute(
        """
        INSERT INTO restaurant_feedback
          (id, restaurant_id, menu_item_id, order_id, customer_user_id, workspace_id,
           rating, feedback_text, language, source_type, tags, signals, verified_order, metadata)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12::jsonb,$13,$14::jsonb)
        """,
        feedback_id,
        body.restaurant_id,
        menu_item_id,
        order_id,
        user_id,
        workspace_id,
        body.rating,
        sanitize_text_for_storage(body.feedback_text or ""),
        body.language or "",
        body.source_type or "text",
        json.dumps([str(tag).strip() for tag in body.tags if str(tag).strip()][:12]),
        json.dumps(signals),
        verified_order,
        json.dumps(body.metadata if isinstance(body.metadata, dict) else {}),
    )
    await audit(
        db,
        user_id=user_id,
        action="restaurant_feedback_submit",
        resource_type="restaurant_feedback",
        resource_id=feedback_id,
        metadata={"restaurant_id": body.restaurant_id, "menu_item_id": menu_item_id, "order_id": order_id, "rating": body.rating, "verified_order": verified_order},
        ip_address=ip_from(request),
        user_agent=ua_from(request),
    )
    return {"feedback": await _fetch_feedback_row(db, feedback_id)}


@router.get("/feedback")
async def list_my_restaurant_feedback(
    current_user: CurrentUser,
    workspace_id: str | None = Query(None),
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    rows = await db.fetch(
        """
        SELECT f.*, r.name AS restaurant_name, mi.item_name AS menu_item_name
        FROM restaurant_feedback f
        JOIN restaurants r ON r.id=f.restaurant_id
        LEFT JOIN restaurant_menu_items mi ON mi.id=f.menu_item_id
        WHERE f.customer_user_id=$1::uuid
          AND (($2::uuid IS NULL AND f.workspace_id IS NULL) OR f.workspace_id=$2::uuid)
        ORDER BY f.created_at DESC
        LIMIT 100
        """,
        user_id,
        workspace_id,
    )
    return {"feedback": [_feedback_row(row) for row in rows]}


@router.get("/owner/feedback")
async def list_restaurant_owner_feedback(
    current_user: CurrentUser,
    status: str = Query(""),
    workspace_id: str | None = Query(None),
    db=Depends(get_db),
):
    user_email = str(current_user.get("email") or "")
    rows = await db.fetch(
        f"""
        SELECT f.*, r.name AS restaurant_name, r.email AS restaurant_email,
               mi.item_name AS menu_item_name, u.email AS customer_email
        FROM restaurant_feedback f
        JOIN restaurants r ON r.id=f.restaurant_id
        LEFT JOIN restaurant_menu_items mi ON mi.id=f.menu_item_id
        LEFT JOIN users u ON u.id=f.customer_user_id
        WHERE {_restaurant_order_manage_sql("r", "$1", "$1")}
          AND ($2='' OR f.status=$2)
          AND (($3::uuid IS NULL AND f.workspace_id IS NULL AND r.workspace_id IS NULL)
               OR (f.workspace_id=$3::uuid AND r.workspace_id=$3::uuid))
        ORDER BY f.created_at DESC
        LIMIT 150
        """,
        user_email,
        status.strip(),
        workspace_id,
    )
    return {"feedback": [_feedback_row(row, mask_customer=False) for row in rows]}


@router.post("/owner/feedback/{feedback_id}/status")
async def update_restaurant_feedback_status(
    feedback_id: str,
    body: RestaurantFeedbackStatusRequest,
    request: Request,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    user_email = str(current_user.get("email") or "")
    allowed = {"submitted", "acknowledged", "responded", "resolved", "dismissed"}
    status = body.status if body.status in allowed else "acknowledged"
    row = await db.fetchrow(
        f"""
        SELECT f.*, r.email AS restaurant_email, r.name AS restaurant_name
        FROM restaurant_feedback f
        JOIN restaurants r ON r.id=f.restaurant_id
        WHERE f.id=$1::uuid
          AND {_restaurant_order_manage_sql("r", "$2", "$2")}
          AND (($3::uuid IS NULL AND f.workspace_id IS NULL AND r.workspace_id IS NULL)
               OR (f.workspace_id=$3::uuid AND r.workspace_id=$3::uuid))
        """,
        feedback_id,
        user_email,
        body.workspace_id,
    )
    if not row:
        raise HTTPException(404, "Feedback not found or not manageable")
    await db.execute(
        """
        UPDATE restaurant_feedback
        SET status=$2, owner_response=$3, updated_at=NOW()
        WHERE id=$1::uuid
        """,
        feedback_id,
        status,
        sanitize_text_for_storage(body.owner_response or ""),
    )
    await audit(
        db,
        user_id=user_id,
        action="restaurant_feedback_status",
        resource_type="restaurant_feedback",
        resource_id=feedback_id,
        metadata={"status": status, "restaurant_id": str(row["restaurant_id"])},
        ip_address=ip_from(request),
        user_agent=ua_from(request),
    )
    return {"feedback": await _fetch_feedback_row(db, feedback_id, mask_customer=False)}


@router.get("/orders")
async def list_my_restaurant_orders(
    current_user: CurrentUser,
    workspace_id: str | None = Query(None),
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    rows = await db.fetch(
        """
        SELECT o.*, r.name AS restaurant_name, r.address AS restaurant_address, r.phone AS restaurant_phone,
               r.email AS restaurant_email,
               COUNT(oi.id)::int AS item_count,
               COALESCE(
                 jsonb_agg(
                   jsonb_build_object(
                     'id', oi.id,
                     'menu_item_id', oi.menu_item_id,
                     'item_name', oi.item_name,
                     'category', oi.category,
                     'quantity', oi.quantity,
                     'unit_price', oi.unit_price,
                     'line_total', oi.line_total,
                     'currency', oi.currency,
                     'instructions', oi.instructions
                   )
                   ORDER BY oi.created_at, oi.item_name
                 ) FILTER (WHERE oi.id IS NOT NULL),
                 '[]'::jsonb
               ) AS order_items
        FROM restaurant_orders o
        JOIN restaurants r ON r.id=o.restaurant_id
        LEFT JOIN restaurant_order_items oi ON oi.order_id=o.id
        WHERE o.customer_user_id=$1::uuid
          AND (($2::uuid IS NULL AND o.workspace_id IS NULL) OR o.workspace_id=$2::uuid)
        GROUP BY o.id, r.id
        ORDER BY o.created_at DESC
        LIMIT 100
        """,
        user_id,
        workspace_id,
    )
    return {"orders": [_order_row(row) for row in rows]}


@router.get("/orders/{order_id}")
async def get_restaurant_order(
    order_id: str,
    current_user: CurrentUser,
    workspace_id: str | None = Query(None),
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    user_email = str(current_user.get("email") or "")
    order = await _fetch_order_record(db, order_id, user_id, user_email, workspace_id)
    if not order:
        raise HTTPException(404, "Order not found")
    return await _fetch_order_response(db, order_id, user_id, user_email, workspace_id)


@router.get("/owner/orders")
async def list_restaurant_owner_orders(
    current_user: CurrentUser,
    status: str = Query(""),
    workspace_id: str | None = Query(None),
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    user_email = str(current_user.get("email") or "")
    rows = await db.fetch(
        f"""
        SELECT o.*, r.name AS restaurant_name, r.address AS restaurant_address, r.phone AS restaurant_phone,
               r.email AS restaurant_email,
               COUNT(oi.id)::int AS item_count,
               COALESCE(
                 jsonb_agg(
                   jsonb_build_object(
                     'id', oi.id,
                     'menu_item_id', oi.menu_item_id,
                     'item_name', oi.item_name,
                     'category', oi.category,
                     'quantity', oi.quantity,
                     'unit_price', oi.unit_price,
                     'line_total', oi.line_total,
                     'currency', oi.currency,
                     'instructions', oi.instructions
                   )
                   ORDER BY oi.created_at, oi.item_name
                 ) FILTER (WHERE oi.id IS NOT NULL),
                 '[]'::jsonb
               ) AS order_items
        FROM restaurant_orders o
        JOIN restaurants r ON r.id=o.restaurant_id
        LEFT JOIN restaurant_order_items oi ON oi.order_id=o.id
        WHERE {_restaurant_order_manage_sql("r", "$1", "$1")}
          AND o.status <> 'draft'
          AND ($2='' OR o.status=$2)
          AND (($3::uuid IS NULL AND o.workspace_id IS NULL AND r.workspace_id IS NULL)
               OR (o.workspace_id=$3::uuid AND r.workspace_id=$3::uuid))
        GROUP BY o.id, r.id
        ORDER BY o.created_at DESC
        LIMIT 150
        """,
        user_email,
        status.strip(),
        workspace_id,
    )
    return {"orders": [_order_row(row) for row in rows]}


@router.post("/owner/orders/{order_id}/accept")
async def accept_restaurant_order(
    order_id: str,
    body: RestaurantOrderStatusRequest,
    request: Request,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    return await _owner_transition_order_endpoint(db, request, current_user, order_id, "accepted", "accepted", body.notes or "Restaurant accepted carryout order", "accepted_at", body.workspace_id)


@router.post("/owner/orders/{order_id}/reject")
async def reject_restaurant_order(
    order_id: str,
    body: RestaurantOrderStatusRequest,
    request: Request,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    return await _owner_transition_order_endpoint(db, request, current_user, order_id, "rejected", "rejected", body.notes or "Restaurant rejected carryout order", "rejected_at", body.workspace_id)


@router.post("/owner/orders/{order_id}/ready")
async def ready_restaurant_order(
    order_id: str,
    body: RestaurantOrderStatusRequest,
    request: Request,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    return await _owner_transition_order_endpoint(db, request, current_user, order_id, "ready_for_pickup", "ready_for_pickup", body.notes or "Restaurant marked order ready for pickup", "ready_at", body.workspace_id)


@router.post("/owner/orders/{order_id}/complete")
async def complete_restaurant_order(
    order_id: str,
    body: RestaurantOrderStatusRequest,
    request: Request,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    return await _owner_transition_order_endpoint(db, request, current_user, order_id, "completed", "completed", body.notes or "Order completed", "completed_at", body.workspace_id)


async def _execute_restaurant_workflow_background(
    run_id: str,
    doc_id: str,
    user_id: str,
    audio_segments: list[dict[str, Any]],
    audio_gcs_path: str,
    audio_filename: str,
    audio_mime_type: str,
    audio_size: int,
    language: str,
    transcript_gcs_path: str,
    transcript_filename: str,
    workspace_id: str | None,
    ip_address: str | None,
    user_agent: str | None,
) -> None:
    pool = get_pool()
    async with pool.acquire() as db:
        try:
            workflow_context = {
                "document_id": doc_id,
                "document_name": transcript_filename,
                "doc_type": "restaurant_menu",
                "doc_domain": "restaurant",
                "audio_bytes": audio_segments[0]["bytes"] if len(audio_segments) == 1 else None,
                "audio_segments": audio_segments,
                "audio_gcs_path": audio_gcs_path,
                "audio_filename": audio_filename,
                "audio_mime_type": audio_mime_type,
                "audio_size": audio_size,
                "audio_segment_count": len(audio_segments),
                "language": language,
                "authorized_confirmed": True,
            }
            workflow = await run_multi_agent_workflow(
                RESTAURANT_WORKFLOW_ID,
                workflow_context,
                RESTAURANT_AGENT_TOOLS,
                lambda agent, agent_call: run_vertical_step(
                    db,
                    run_id,
                    agent.get("name") or agent.get("id") or "Agent",
                    agent.get("input_summary") or "",
                    agent_call,
                ),
            )
            await complete_vertical_run(db, run_id, workflow["result"], status="pending_approval")
            await _persist_workflow_transcript(
                db,
                doc_id=doc_id,
                user_id=user_id,
                workspace_id=workspace_id,
                result=workflow["result"],
                transcript_gcs_path=transcript_gcs_path,
                transcript_filename=transcript_filename,
                status="pending_approval",
            )
            await log_event(db, user_id, "restaurant_menu_scribe", metadata={"doc_id": doc_id, "run_id": run_id})
            await audit(
                db,
                user_id=user_id,
                action="restaurant_menu_scribe_workflow",
                resource_type="document",
                resource_id=doc_id,
                metadata={"run_id": run_id, "workflow_id": RESTAURANT_WORKFLOW_ID, "audio_gcs_path": audio_gcs_path},
                ip_address=ip_address,
                user_agent=user_agent,
            )
        except (RestaurantIntelligenceError, WorkflowConfigError) as exc:
            log.warning("Restaurant scribe workflow failed run_id=%s doc_id=%s: %s", run_id, doc_id, exc)
            await fail_vertical_run(db, run_id, str(exc))
            await db.execute("UPDATE documents SET status='error', error_message=$2, updated_at=NOW() WHERE id=$1", doc_id, str(exc)[:500])
        except Exception as exc:
            log.exception("Restaurant scribe workflow crashed run_id=%s doc_id=%s", run_id, doc_id)
            await fail_vertical_run(db, run_id, str(exc))
            await db.execute("UPDATE documents SET status='error', error_message=$2, updated_at=NOW() WHERE id=$1", doc_id, str(exc)[:500])


async def _save_restaurant_packet(
    db,
    user_id: str,
    workspace_id: str | None,
    run_id: str,
    packet: dict[str, Any],
    *,
    require_email: bool = True,
) -> str:
    user_id = _id_text(user_id)
    workspace_id = _id_text(workspace_id)
    run_id = _id_text(run_id)
    profile = packet.get("restaurant_profile") or {}
    menu_items = packet.get("menu_items") if isinstance(packet.get("menu_items"), list) else []
    name = str(profile.get("name") or "Unnamed Restaurant").strip()
    address = str(profile.get("address") or "").strip()
    email = _required_restaurant_email(profile) if require_email else str(profile.get("email") or "").strip()
    existing = await db.fetchrow(
        """
        SELECT id FROM restaurants
        WHERE user_id=$1
          AND workspace_id IS NOT DISTINCT FROM $2::uuid
          AND LOWER(name)=LOWER($3)
          AND LOWER(address)=LOWER($4)
        LIMIT 1
        """,
        user_id,
        workspace_id,
        name,
        address,
    )
    if existing:
        restaurant_id = str(existing["id"])
        await db.execute(
            """
            UPDATE restaurants
            SET source_run_id=$2, description=$3, cuisine_type=$4, address=$5, phone=$6,
                email=$7, website=$8, hours=$9::jsonb, service_options=$10::jsonb,
                payment_options=$11::jsonb, metadata=$12::jsonb, updated_at=NOW()
            WHERE id=$1
            """,
            restaurant_id,
            run_id,
            profile.get("description") or "",
            profile.get("cuisine_type") or "",
            address,
            profile.get("phone") or "",
            email,
            profile.get("website") or "",
            json.dumps(profile.get("hours") if isinstance(profile.get("hours"), dict) else {}),
            json.dumps(profile.get("service_options") if isinstance(profile.get("service_options"), list) else []),
            json.dumps(profile.get("payment_options") if isinstance(profile.get("payment_options"), list) else []),
            json.dumps({"source": "restaurant_menu_scribe", "approved_at": datetime.utcnow().isoformat() + "Z"}),
        )
        await db.execute("DELETE FROM restaurant_menu_items WHERE restaurant_id=$1", restaurant_id)
    else:
        restaurant_id = str(uuid.uuid4())
        await db.execute(
            """
            INSERT INTO restaurants
              (id, user_id, workspace_id, source_run_id, name, description, cuisine_type, address,
               phone, email, website, hours, service_options, payment_options, metadata)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb,$13::jsonb,$14::jsonb,$15::jsonb)
            """,
            restaurant_id,
            user_id,
            workspace_id,
            run_id,
            name,
            profile.get("description") or "",
            profile.get("cuisine_type") or "",
            address,
            profile.get("phone") or "",
            email,
            profile.get("website") or "",
            json.dumps(profile.get("hours") if isinstance(profile.get("hours"), dict) else {}),
            json.dumps(profile.get("service_options") if isinstance(profile.get("service_options"), list) else []),
            json.dumps(profile.get("payment_options") if isinstance(profile.get("payment_options"), list) else []),
            json.dumps({"source": "restaurant_menu_scribe", "approved_at": datetime.utcnow().isoformat() + "Z"}),
        )
    for item in menu_items:
        if not isinstance(item, dict) or not str(item.get("item_name") or "").strip():
            continue
        await db.execute(
            """
            INSERT INTO restaurant_menu_items
              (restaurant_id, user_id, workspace_id, category, item_name, price, currency,
               quantity, description, ingredients, dietary_tags, spice_level, availability, options, metadata)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11::jsonb,$12,$13,$14::jsonb,$15::jsonb)
            """,
            restaurant_id,
            user_id,
            workspace_id,
            item.get("category") or "",
            item.get("item_name") or "",
            item.get("price"),
            item.get("currency") or "USD",
            item.get("quantity") or "",
            item.get("description") or "",
            json.dumps(item.get("ingredients") if isinstance(item.get("ingredients"), list) else []),
            json.dumps(item.get("dietary_tags") if isinstance(item.get("dietary_tags"), list) else []),
            item.get("spice_level") or "",
            item.get("availability") or "available",
            json.dumps(item.get("options") if isinstance(item.get("options"), list) else []),
            json.dumps({"source_run_id": run_id}),
        )
    return restaurant_id


async def _restore_approved_restaurants_if_needed(db, user_id: str, workspace_id: str | None = None) -> int:
    """Backfill restaurants from older approved restaurant scribe runs.

    Earlier restaurant scribe runs may have the approved packet in
    vertical_agent_runs but no corresponding restaurants row. The list/search
    endpoints call this opportunistically so old approved menu data comes back
    without a separate manual migration.
    """
    user_id = _id_text(user_id)
    workspace_id = _id_text(workspace_id)
    runs = await db.fetch(
        f"""
        SELECT r.id, r.user_id, r.workspace_id, r.result_data
        FROM vertical_agent_runs r
        WHERE r.vertical=$2
          AND ($3::uuid IS NULL OR r.workspace_id=$3::uuid OR r.workspace_id IS NULL)
          AND {_restaurant_access_sql("r")}
          AND NOT EXISTS (
              SELECT 1 FROM restaurants existing
              JOIN restaurant_menu_items existing_item ON existing_item.restaurant_id=existing.id
              WHERE existing.source_run_id=r.id
          )
        ORDER BY r.updated_at DESC
        LIMIT 50
        """,
        user_id,
        RESTAURANT_VERTICAL,
        workspace_id,
    )
    restored = 0
    for run in runs:
        result = _json(run.get("result_data")) or {}
        packet = _restaurant_packet_from_result(result)
        if not isinstance(packet, dict):
            continue
        profile = packet.get("restaurant_profile")
        menu_items = packet.get("menu_items")
        if not isinstance(profile, dict) or not isinstance(menu_items, list):
            continue
        owner_id = str(run["user_id"])
        run_workspace_id = str(run["workspace_id"]) if run.get("workspace_id") else None
        await _save_restaurant_packet(db, owner_id, run_workspace_id, str(run["id"]), packet, require_email=False)
        restored += 1
    if restored:
        log.info("Restored %s approved restaurant scribe run(s) for user_id=%s workspace_id=%s", restored, user_id, workspace_id)
    return restored


def _restaurant_packet_from_result(result: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    for key in ("approved_packet", "review_packet", "restaurant_packet", "packet"):
        packet = result.get(key)
        if isinstance(packet, dict) and isinstance(packet.get("restaurant_profile"), dict) and isinstance(packet.get("menu_items"), list):
            return packet
    if isinstance(result.get("restaurant_profile"), dict) and isinstance(result.get("menu_items"), list):
        return result
    nested = result.get("result")
    if isinstance(nested, dict):
        return _restaurant_packet_from_result(nested)
    return None


def _restaurant_missing_data_causes(counts: dict[str, Any], runs: dict[str, Any], docs: dict[str, Any], audit_rows) -> list[str]:
    causes: list[str] = []
    restaurants = int(counts.get("restaurants") or 0)
    menu_items = int(counts.get("menu_items") or 0)
    total_runs = int(runs.get("total_runs") or 0)
    packet_runs = (
        int(runs.get("approved_packet_runs") or 0)
        + int(runs.get("review_packet_runs") or 0)
        + int(runs.get("direct_packet_runs") or 0)
    )
    if restaurants == 0 and total_runs == 0:
        causes.append("No restaurant rows and no restaurant agent runs are visible to this user/workspace. Most likely this is a different database, different account, different workspace, or the data was deleted with the source document/user.")
    if restaurants == 0 and total_runs > 0 and packet_runs == 0:
        causes.append("Restaurant agent runs exist, but none contain a recognizable restaurant_profile/menu_items packet. The workflow may have failed before extraction or stored an unexpected result shape.")
    if restaurants == 0 and packet_runs > 0:
        causes.append("Recoverable restaurant packets exist in vertical agent runs but are not appearing as saved restaurants. Refresh should restore them; if not, check backend logs for restore errors.")
    if restaurants > 0 and menu_items == 0:
        causes.append("Restaurant records exist but menu item rows are empty. This can happen if Edit/Save was submitted with an empty menu, because saved menus are replaced during update.")
    for row in audit_rows:
        action = row.get("action")
        if action == "restaurant_menu_delete":
            causes.append("Recent audit log contains restaurant_menu_delete, so a saved restaurant was explicitly deleted from the Restaurant panel.")
            break
    for row in audit_rows:
        action = row.get("action")
        if action == "restaurant_menu_update":
            causes.append("Recent audit log contains restaurant_menu_update. If that update had an empty menu payload, old menu items would have been replaced with no items.")
            break
    deleted_docs = int(docs.get("deleted_restaurant_documents") or 0)
    if deleted_docs:
        causes.append("Some restaurant documents are marked deleted. Deleting a source document can cascade-delete vertical agent runs, which removes the restore source.")
    if not causes:
        causes.append("Restaurant data appears present from counts. If the UI is empty, the issue is likely frontend filtering, workspace selection, auth/account mismatch, or stale deployed frontend/backend.")
    return causes


async def _persist_workflow_transcript(
    db,
    doc_id: str,
    user_id: str,
    workspace_id: str | None,
    result: dict[str, Any],
    transcript_gcs_path: str,
    transcript_filename: str,
    status: str,
) -> None:
    doc_id = _id_text(doc_id)
    user_id = _id_text(user_id)
    workspace_id = _id_text(workspace_id)
    transcript = ((result.get("conversation_transcript") or {}).get("transcript_text") or "").strip()
    text = transcript or "Restaurant menu scribe transcript was empty."
    await gcs.upload_text(transcript_gcs_path, text)
    await db.execute(
        """
        UPDATE documents
        SET filename=$2, original_name=$3, file_size=$4, status=$5, chunk_count=0, updated_at=NOW(),
            doc_metadata=COALESCE(doc_metadata, '{}'::jsonb) || $6::jsonb
        WHERE id=$1
        """,
        doc_id,
        transcript_filename,
        transcript_filename,
        len(text.encode("utf-8")),
        status,
        json.dumps({"restaurant_scribe_generated_at": datetime.utcnow().isoformat() + "Z", "workspace_id": workspace_id}),
    )


async def _persist_approved_restaurant_document(db, doc_id: str, user_id: str, workspace_id: str | None, packet: dict[str, Any]) -> None:
    doc_id = _id_text(doc_id)
    user_id = _id_text(user_id)
    workspace_id = _id_text(workspace_id)
    profile = packet.get("restaurant_profile") or {}
    lines = [
        f"Restaurant: {profile.get('name') or 'Unnamed Restaurant'}",
        f"Cuisine: {profile.get('cuisine_type') or ''}",
        f"Address: {profile.get('address') or ''}",
        f"Phone: {profile.get('phone') or ''}",
        f"Description: {profile.get('description') or ''}",
        "",
        "Menu:",
    ]
    for item in packet.get("menu_items") or []:
        if not isinstance(item, dict):
            continue
        price = item.get("price")
        price_text = f"${price}" if price is not None else "price not provided"
        lines.append(f"- {item.get('item_name')}: {price_text}; {item.get('quantity') or ''}; {item.get('description') or ''}")
    text = sanitize_text_for_storage("\n".join(lines))
    source_path = await db.fetchval("SELECT gcs_source_path FROM documents WHERE id=$1", doc_id)
    if source_path:
        await gcs.upload_text(source_path, text)
    await delete_document_vectors(doc_id)
    doc_meta = {
        "document_id": doc_id,
        "user_id": user_id,
        "filename": f"{profile.get('name') or 'Restaurant'} Menu",
        "file_type": "text",
        "source_kind": "restaurant_menu_scribe",
        "workflow_id": RESTAURANT_WORKFLOW_ID,
        "restaurant_id": packet.get("restaurant_id"),
    }
    chunks = chunk_text(text, doc_meta=doc_meta)
    if not chunks:
        raise RestaurantIntelligenceError("Restaurant menu approval produced no chunks")
    for chunk in chunks:
        await gcs.upload_text(gcs.chunk_path(user_id, doc_id, chunk.index), chunk.text)
    now = datetime.now(timezone.utc).isoformat()
    meta_obj = {
        "document": {
            "id": doc_id,
            "user_id": user_id,
            "filename": doc_meta["filename"],
            "file_type": "text",
            "total_chunks": len(chunks),
            "created_at": now,
            "source_kind": "restaurant_menu_scribe",
            "workflow_id": RESTAURANT_WORKFLOW_ID,
            "restaurant_id": packet.get("restaurant_id"),
        },
        "chunks": [
            {
                "index": c.index,
                "word_count": c.word_count,
                "char_count": c.char_count,
                "gcs_path": gcs.chunk_path(user_id, doc_id, c.index),
                "source_kind": "restaurant_menu_scribe",
                "restaurant_id": packet.get("restaurant_id"),
            }
            for c in chunks
        ],
    }
    await gcs.upload_json(gcs.metadata_path(user_id, doc_id), meta_obj)
    await db.execute(
        """
        UPDATE documents
        SET status='embedding', chunk_count=$2, file_size=$3, updated_at=NOW(),
            doc_metadata=COALESCE(doc_metadata, '{}'::jsonb) || $4::jsonb
        WHERE id=$1
        """,
        doc_id,
        len(chunks),
        len(text.encode("utf-8")),
        json.dumps({"restaurant_id": packet.get("restaurant_id"), "approved_restaurant_menu": True}),
    )
    await check_and_log_daily_event(
        db,
        user_id,
        "embedding",
        "max_embeds_day",
        quantity=len(chunks),
        metadata={"doc_id": doc_id, "chunk_count": len(chunks), "source_kind": "restaurant_menu_scribe"},
    )
    for chunk in chunks:
        await store_chunk(
            document_id=doc_id,
            user_id=user_id,
            workspace_id=workspace_id,
            chunk_index=chunk.index,
            chunk_total=len(chunks),
            content=chunk.text,
            embedding=await embed(chunk.text),
            chunk_metadata=chunk.to_metadata(),
        )
    await db.execute(
        """
        UPDATE documents
        SET status='embedded', chunk_count=$2, file_size=$3, updated_at=NOW(),
            doc_metadata=COALESCE(doc_metadata, '{}'::jsonb) || $4::jsonb
        WHERE id=$1
        """,
        doc_id,
        len(chunks),
        len(text.encode("utf-8")),
        json.dumps({"restaurant_id": packet.get("restaurant_id"), "approved_restaurant_menu": True}),
    )


def _restaurant_access_sql(alias: str, user_param: str = "$1") -> str:
    return (
        f"({alias}.user_id={user_param}::uuid OR EXISTS ("
        f"SELECT 1 FROM workspace_members wm WHERE wm.workspace_id={alias}.workspace_id AND wm.user_id={user_param}::uuid"
        f"))"
    )


def _restaurant_manage_sql(alias: str, user_param: str = "$1") -> str:
    return (
        f"({alias}.user_id={user_param}::uuid OR EXISTS ("
        f"SELECT 1 FROM workspace_members wm WHERE wm.workspace_id={alias}.workspace_id "
        f"AND wm.user_id={user_param}::uuid AND wm.role IN ('editor','owner')"
        f"))"
    )


def _restaurant_order_manage_sql(alias: str, user_param: str = "$1", email_param: str = "$2") -> str:
    return (
        f"(COALESCE({alias}.email, '') <> '' AND COALESCE({email_param}::text, '') <> '' "
        f"AND LOWER({alias}.email)=LOWER({email_param}::text))"
    )


def _restaurant_catalog_manage_sql(alias: str, user_param: str = "$1", email_param: str = "$2") -> str:
    return (
        f"({_restaurant_order_manage_sql(alias, user_param, email_param)} OR EXISTS ("
        f"SELECT 1 FROM workspace_members wm WHERE wm.workspace_id={alias}.workspace_id "
        f"AND wm.user_id={user_param}::uuid AND wm.role='owner'"
        f"))"
    )


async def _can_restore_restaurants(db, user_id: str, workspace_id: str | None = None) -> bool:
    if not workspace_id:
        return True
    role = await db.fetchval(
        """
        SELECT role
        FROM workspace_members
        WHERE workspace_id=$1::uuid AND user_id=$2::uuid
        """,
        workspace_id,
        user_id,
    )
    return role in {"editor", "owner"}


def _restaurant_can_manage(row: dict[str, Any], user_id: str, user_email: str = "") -> bool:
    restaurant_email = str(row.get("email") or "").strip().lower()
    login_email = str(user_email or "").strip().lower()
    if restaurant_email and login_email and restaurant_email == login_email:
        return True
    return row.get("viewer_role") == "owner"


def _order_item_quantity(item: RestaurantOrderItemRequest) -> int:
    return max(1, int(item.quantity_ordered or item.quantity or 1))


async def _resolve_order_restaurant_id(
    db,
    user_id: str,
    restaurant_name: str,
    workspace_id: str | None = None,
) -> str:
    name = (restaurant_name or "").strip()
    if not name:
        raise HTTPException(400, "Restaurant name is required for chat-derived order items")
    row = await db.fetchrow(
        f"""
        SELECT r.id
        FROM restaurants r
        WHERE LOWER(r.name)=LOWER($2)
          AND ($3::uuid IS NULL OR r.workspace_id=$3::uuid OR r.workspace_id IS NULL)
          AND {_restaurant_access_sql("r")}
        ORDER BY r.workspace_id NULLS LAST, r.updated_at DESC
        LIMIT 1
        """,
        user_id,
        name,
        workspace_id,
    )
    if row:
        return str(row["id"])
    restaurant_id = str(uuid.uuid4())
    await db.execute(
        """
        INSERT INTO restaurants
          (id, user_id, workspace_id, name, description, cuisine_type, metadata)
        VALUES ($1,$2,$3::uuid,$4,$5,$6,$7::jsonb)
        """,
        restaurant_id,
        user_id,
        workspace_id,
        name,
        "Created from a chat answer so carryout orders can be reviewed.",
        "",
        json.dumps({"source": "chat_answer_order"}),
    )
    return restaurant_id


async def _restaurant_source_transcript(db, restaurant: dict[str, Any], user_id: str) -> str:
    source_run_id = restaurant.get("source_run_id")
    if not source_run_id:
        return ""
    run = await db.fetchrow(
        f"""
        SELECT r.result_data
        FROM vertical_agent_runs r
        WHERE r.id=$1
          AND {_restaurant_access_sql("r", "$2")}
        """,
        source_run_id,
        user_id,
    )
    if not run:
        return ""
    result = _json(run.get("result_data")) or {}
    packet = result.get("approved_packet") if isinstance(result.get("approved_packet"), dict) else result
    transcript = packet.get("conversation_transcript") if isinstance(packet, dict) else {}
    if isinstance(transcript, dict):
        return str(transcript.get("transcript_text") or "")
    return ""


def _restaurant_row(row) -> dict[str, Any]:
    data = dict(row)
    return {key: _clean(value) for key, value in data.items()}


def _menu_row(row) -> dict[str, Any]:
    data = dict(row)
    return {key: _clean(value) for key, value in data.items()}


def _menu_search_row(row) -> dict[str, Any]:
    data = _menu_row(row)
    return data


def _row_get(row, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


async def _fetch_order_record(
    db,
    order_id: str,
    user_id: str,
    user_email: str = "",
    workspace_id: str | None = None,
):
    return await db.fetchrow(
        f"""
        SELECT o.*, r.name AS restaurant_name, r.address AS restaurant_address,
               r.phone AS restaurant_phone, r.email AS restaurant_email,
               r.user_id AS restaurant_owner_id, owner.email AS restaurant_owner_email,
               r.workspace_id AS restaurant_workspace_id
        FROM restaurant_orders o
        JOIN restaurants r ON r.id=o.restaurant_id
        LEFT JOIN users owner ON owner.id=r.user_id
        WHERE o.id=$1::uuid
          AND (o.customer_user_id=$2::uuid OR {_restaurant_order_manage_sql("r", "$2", "$3")})
          AND (($4::uuid IS NULL AND o.workspace_id IS NULL AND r.workspace_id IS NULL)
               OR (o.workspace_id=$4::uuid AND r.workspace_id=$4::uuid))
        """,
        order_id,
        user_id,
        user_email,
        workspace_id,
    )


async def _fetch_order_response(
    db,
    order_id: str,
    user_id: str,
    user_email: str = "",
    workspace_id: str | None = None,
) -> dict[str, Any]:
    order = await _fetch_order_record(db, order_id, user_id, user_email, workspace_id)
    if not order:
        raise HTTPException(404, "Order not found")
    items = await db.fetch(
        """
        SELECT * FROM restaurant_order_items
        WHERE order_id=$1::uuid
        ORDER BY created_at, item_name
        """,
        order_id,
    )
    events = await db.fetch(
        """
        SELECT e.*, u.email AS actor_email
        FROM restaurant_order_events e
        LEFT JOIN users u ON u.id=e.actor_id
        WHERE e.order_id=$1::uuid
        ORDER BY e.created_at
        """,
        order_id,
    )
    return {
        "order": _order_row(order),
        "items": [_order_row(item) for item in items],
        "events": [_order_row(event) for event in events],
    }


async def _transition_order(
    db,
    order,
    *,
    actor_id: str,
    to_status: str,
    event_type: str,
    notes: str = "",
    timestamp_column: str | None = None,
    notify_owner: bool = False,
) -> None:
    timestamp_columns = {
        "submitted_at",
        "accepted_at",
        "confirmed_at",
        "ready_at",
        "completed_at",
        "cancelled_at",
        "rejected_at",
    }
    from_status = order["status"]
    assignments = ["status=$2", "updated_at=NOW()"]
    if timestamp_column:
        if timestamp_column not in timestamp_columns:
            raise HTTPException(500, "Invalid order timestamp column")
        assignments.append(f"{timestamp_column}=NOW()")
        if timestamp_column == "accepted_at":
            assignments.append("confirmed_at=NOW()")
    await db.execute(
        f"UPDATE restaurant_orders SET {', '.join(assignments)} WHERE id=$1::uuid",
        str(order["id"]),
        to_status,
    )
    await _add_order_event(db, str(order["id"]), actor_id, event_type, from_status, to_status, notes)
    if notify_owner:
        await _safe_order_notification(
            _notify_restaurant_owner(db, order, actor_id, event_type),
            order,
            f"restaurant owner {event_type}",
        )


async def _owner_transition_order_endpoint(
    db,
    request: Request,
    current_user: CurrentUser,
    order_id: str,
    to_status: str,
    event_type: str,
    notes: str,
    timestamp_column: str,
    workspace_id: str | None,
) -> dict[str, Any]:
    user_id = str(current_user["id"])
    user_email = str(current_user.get("email") or "")
    order = await _fetch_order_record(db, order_id, user_id, user_email, workspace_id)
    if not order:
        raise HTTPException(404, "Order not found")
    await _require_restaurant_order_owner(db, order, user_id, user_email)

    allowed = {
        "accepted": {"submitted"},
        "rejected": {"submitted"},
        "ready_for_pickup": {"accepted", "confirmed"},
        "completed": {"ready_for_pickup"},
    }
    if order["status"] not in allowed.get(to_status, set()):
        raise HTTPException(400, f"Cannot change order from {order['status']} to {to_status}")

    await _transition_order(
        db,
        order,
        actor_id=user_id,
        to_status=to_status,
        event_type=event_type,
        notes=notes,
        timestamp_column=timestamp_column,
    )
    await _safe_order_notification(
        _notify_restaurant_customer(db, order, user_id, event_type, to_status),
        order,
        f"customer {event_type}",
    )
    await audit(
        db,
        user_id=user_id,
        action=f"restaurant_order_{event_type}",
        resource_type="restaurant_order",
        resource_id=order_id,
        metadata={"restaurant_id": str(order["restaurant_id"]), "from_status": order["status"], "to_status": to_status},
        ip_address=ip_from(request),
        user_agent=ua_from(request),
    )
    return await _fetch_order_response(db, order_id, user_id, user_email, workspace_id)


async def _require_restaurant_order_owner(db, order, user_id: str, user_email: str = "") -> None:
    restaurant_email = str(_row_get(order, "restaurant_email") or "").strip().lower()
    login_email = str(user_email or "").strip().lower()
    if restaurant_email and login_email and restaurant_email == login_email:
        return
    raise HTTPException(403, "Only the restaurant owner can update this order")


async def _add_order_event(
    db,
    order_id: str,
    actor_id: str | None,
    event_type: str,
    from_status: str | None,
    to_status: str | None,
    notes: str = "",
    metadata: dict | None = None,
) -> None:
    await db.execute(
        """
        INSERT INTO restaurant_order_events
          (order_id, actor_id, event_type, from_status, to_status, notes, metadata)
        VALUES ($1::uuid,$2::uuid,$3,$4,$5,$6,$7::jsonb)
        """,
        order_id,
        actor_id,
        event_type,
        from_status,
        to_status,
        notes or "",
        json.dumps(metadata or {}),
    )


async def _notify_restaurant_owner(db, order, actor_id: str, event_type: str) -> None:
    owner_id = str(order["restaurant_owner_id"]) if order.get("restaurant_owner_id") else None
    if not owner_id:
        return
    restaurant_name = order.get("restaurant_name") or "restaurant"
    customer = order.get("customer_name") or order.get("customer_email") or "a customer"
    message = f"New carryout order from {customer} for {restaurant_name}"
    await _record_restaurant_notification(
        db,
        order,
        owner_id,
        channel="in_app",
        status="unread",
        message=message,
        metadata={"event_type": event_type, "actor_id": actor_id, "audience": "restaurant_owner"},
    )
    await _send_order_email_to_user(
        db,
        owner_id,
        email=order.get("restaurant_email") or order.get("restaurant_owner_email"),
        audience="restaurant_owner",
        status=event_type,
        message=message,
        data={
            "type": "restaurant_order",
            "order_id": str(order["id"]),
            "restaurant_id": str(order["restaurant_id"]),
            "event_type": event_type,
            "audience": "restaurant_owner",
        },
        order=order,
    )


async def _safe_order_notification(notification, order, label: str) -> None:
    try:
        await notification
    except Exception as exc:
        try:
            order_id = order.get("id")
        except Exception:
            order_id = "unknown"
        log.warning(
            "Restaurant order notification failed label=%s order_id=%s: %s",
            label,
            order_id,
            exc,
        )


async def _notify_restaurant_customer(db, order, actor_id: str, event_type: str, to_status: str) -> None:
    customer_id = str(order["customer_user_id"]) if order.get("customer_user_id") else None
    if not customer_id:
        return
    restaurant_name = order.get("restaurant_name") or "restaurant"
    messages = {
        "submitted": f"Your carryout order was submitted to {restaurant_name}.",
        "accepted": f"{restaurant_name} accepted your carryout order.",
        "rejected": f"{restaurant_name} could not accept your carryout order.",
        "ready_for_pickup": f"Your carryout order is ready for pickup at {restaurant_name}.",
        "completed": f"Your carryout order at {restaurant_name} is complete.",
    }
    message = messages.get(to_status, f"Your carryout order at {restaurant_name} is now {to_status}.")
    await _record_restaurant_notification(
        db,
        order,
        customer_id,
        channel="in_app",
        status="unread",
        message=message,
        metadata={"event_type": event_type, "actor_id": actor_id, "audience": "customer", "to_status": to_status},
    )
    await _send_order_email_to_user(
        db,
        customer_id,
        email=order.get("customer_email"),
        audience="customer",
        status=to_status,
        message=message,
        data={
            "type": "restaurant_order",
            "order_id": str(order["id"]),
            "restaurant_id": str(order["restaurant_id"]),
            "event_type": event_type,
            "status": to_status,
            "audience": "customer",
        },
        order=order,
    )


async def _record_restaurant_notification(
    db,
    order,
    user_id: str,
    *,
    channel: str,
    status: str,
    message: str,
    metadata: dict,
) -> None:
    await db.execute(
        """
        INSERT INTO restaurant_notifications
          (restaurant_id, order_id, user_id, channel, status, message, metadata)
        VALUES ($1::uuid,$2::uuid,$3::uuid,$4,$5,$6,$7::jsonb)
        """,
        str(order["restaurant_id"]),
        str(order["id"]),
        user_id,
        channel,
        status,
        message,
        json.dumps(metadata or {}),
    )


async def _send_order_email_to_user(
    db,
    user_id: str,
    *,
    email: str | None,
    audience: str,
    status: str,
    message: str,
    data: dict,
    order,
) -> None:
    to_email = (email or "").strip()
    if not to_email:
        log.info("No email available for restaurant order notification user_id=%s order_id=%s", user_id, order["id"])
        return
    subtotal = ""
    if order.get("subtotal") is not None:
        subtotal = f"{order.get('currency') or 'USD'} {float(order.get('subtotal')):.2f}"
    order_items = await _fetch_order_email_items(db, str(order["id"]))
    ok = await send_restaurant_order_email(
        to_email,
        audience=audience,
        restaurant_name=order.get("restaurant_name") or "restaurant",
        order_id=str(order["id"]),
        restaurant_id=str(order["restaurant_id"]),
        status=status,
        message=message,
        customer_name=order.get("customer_name") or order.get("customer_email") or "",
        subtotal=subtotal,
        order_items=order_items,
        app_url=os.getenv("APP_URL", "https://docintel.adar.agomoniai.com"),
    )
    await _record_restaurant_notification(
        db,
        order,
        user_id,
        channel="email",
        status="sent" if ok else "failed",
        message=message,
        metadata={**data, "email": _mask_email(to_email), "provider": "smtp"},
    )


async def _fetch_order_email_items(db, order_id: str) -> list[dict[str, Any]]:
    rows = await db.fetch(
        """
        SELECT item_name, category, quantity AS quantity_ordered, unit_price, line_total, currency, instructions
        FROM restaurant_order_items
        WHERE order_id=$1::uuid
        ORDER BY created_at, item_name
        """,
        order_id,
    )
    return [_order_row(row) for row in rows]


def _mask_email(email: str) -> str:
    name, _, domain = email.partition("@")
    if not domain:
        return ""
    if len(name) <= 2:
        masked = name[:1] + "*"
    else:
        masked = name[:2] + "***"
    return f"{masked}@{domain}"


def _order_row(row) -> dict[str, Any]:
    data = dict(row)
    cleaned = {key: _clean(value) for key, value in data.items()}
    order_items = cleaned.get("order_items")
    if isinstance(order_items, str):
        try:
            cleaned["order_items"] = json.loads(order_items)
        except json.JSONDecodeError:
            cleaned["order_items"] = []
    elif order_items is None:
        cleaned["order_items"] = []
    return cleaned


async def _fetch_feedback_row(db, feedback_id: str, mask_customer: bool = True) -> dict[str, Any]:
    row = await db.fetchrow(
        """
        SELECT f.*, r.name AS restaurant_name, r.email AS restaurant_email,
               mi.item_name AS menu_item_name, u.email AS customer_email
        FROM restaurant_feedback f
        JOIN restaurants r ON r.id=f.restaurant_id
        LEFT JOIN restaurant_menu_items mi ON mi.id=f.menu_item_id
        LEFT JOIN users u ON u.id=f.customer_user_id
        WHERE f.id=$1::uuid
        """,
        feedback_id,
    )
    return _feedback_row(row, mask_customer=mask_customer) if row else {}


def _feedback_row(row, mask_customer: bool = True) -> dict[str, Any]:
    data = dict(row)
    cleaned = {key: _clean(value) for key, value in data.items()}
    for key in ("tags", "signals", "metadata"):
        value = cleaned.get(key)
        if isinstance(value, str):
            try:
                cleaned[key] = json.loads(value)
            except json.JSONDecodeError:
                cleaned[key] = [] if key == "tags" else {}
        elif value is None:
            cleaned[key] = [] if key == "tags" else {}
    if mask_customer and cleaned.get("customer_email"):
        cleaned["customer_email"] = _mask_email(str(cleaned["customer_email"]))
    return cleaned


def _required_restaurant_email(profile: dict[str, Any]) -> str:
    email = str((profile or {}).get("email") or "").strip()
    if not email:
        raise HTTPException(400, "Restaurant email is required")
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise HTTPException(400, "Restaurant email is invalid")
    return email


def _clean(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _id_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


FEEDBACK_TOPICS = ["taste", "value", "portion", "freshness", "spice", "packaging", "wait_time", "accuracy"]
TOPIC_QUERY_TERMS = {
    "taste": ["taste", "tasty", "flavor", "flavour", "delicious", "best", "good"],
    "value": ["value", "cheap", "price", "affordable", "deal", "budget", "cost"],
    "portion": ["portion", "large", "size", "quantity", "enough", "filling"],
    "freshness": ["fresh", "freshness", "hot", "warm", "quality"],
    "spice": ["spice", "spicy", "mild", "heat"],
    "packaging": ["packaging", "packed", "spill", "container"],
    "wait_time": ["fast", "quick", "pickup", "wait", "ready", "delay", "slow"],
    "accuracy": ["accurate", "correct", "wrong", "missing", "order accuracy"],
}
POSITIVE_TERMS = {
    "great", "good", "excellent", "amazing", "delicious", "fresh", "hot", "fast", "quick",
    "large", "generous", "perfect", "accurate", "friendly", "recommend", "best", "love",
    "loved", "tasty", "worth", "affordable",
}
NEGATIVE_TERMS = {
    "bad", "poor", "cold", "late", "slow", "small", "stale", "wrong", "missing", "bland",
    "expensive", "overpriced", "salty", "burnt", "dry", "delay", "delayed", "not good",
    "disappointed", "terrible",
}


async def _analyze_restaurant_feedback_text(
    text: str,
    language: str = "",
    current_rating: int | None = None,
    restaurant_name: str = "",
    menu_item_name: str = "",
    prefer_gemini: bool = True,
) -> dict[str, Any]:
    text = sanitize_text_for_storage(text or "").strip()
    if not text:
        return _heuristic_feedback_analysis("")
    google_ai_key = os.getenv("GOOGLE_AI_KEY", "").strip()
    if not prefer_gemini or not google_ai_key:
        return _heuristic_feedback_analysis(text, current_rating=current_rating, analyzer="heuristic")

    model = os.getenv("GEMINI_CHAT_MODEL", "gemini-2.5-flash").removeprefix("models/")
    prompt = f"""
Analyze restaurant customer feedback and return only JSON.

Feedback:
{text[:3000]}

Context:
restaurant={restaurant_name}
menu_item={menu_item_name}
language={language}
current_rating={current_rating}

Return this exact JSON shape:
{{
  "suggested_rating": 1-5,
  "overall_sentiment": "positive|neutral|negative|mixed",
  "confidence": 0.0-1.0,
  "tags": ["taste","value","portion","freshness","spice","packaging","wait_time","accuracy"],
  "topic_sentiment": {{
    "taste": {{"sentiment":"positive|neutral|negative|mixed|unknown","score": -1.0 to 1.0}},
    "value": {{"sentiment":"positive|neutral|negative|mixed|unknown","score": -1.0 to 1.0}},
    "portion": {{"sentiment":"positive|neutral|negative|mixed|unknown","score": -1.0 to 1.0}},
    "freshness": {{"sentiment":"positive|neutral|negative|mixed|unknown","score": -1.0 to 1.0}},
    "spice": {{"sentiment":"positive|neutral|negative|mixed|unknown","score": -1.0 to 1.0}},
    "packaging": {{"sentiment":"positive|neutral|negative|mixed|unknown","score": -1.0 to 1.0}},
    "wait_time": {{"sentiment":"positive|neutral|negative|mixed|unknown","score": -1.0 to 1.0}},
    "accuracy": {{"sentiment":"positive|neutral|negative|mixed|unknown","score": -1.0 to 1.0}}
  }},
  "reason": "short human-readable explanation"
}}
"""
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0,
            "maxOutputTokens": 900,
            "responseMimeType": "application/json",
        },
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                params={"key": google_ai_key},
                json=payload,
            )
        if not resp.is_success:
            log.warning("Restaurant feedback sentiment failed %s: %s", resp.status_code, resp.text[:300])
            return _heuristic_feedback_analysis(text, current_rating=current_rating, analyzer="heuristic_after_gemini_error")
        parts = resp.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
        raw = " ".join((part.get("text") or "").strip() for part in parts if part.get("text")).strip()
        data = _json_from_text(raw)
        return _normalize_feedback_analysis(data, fallback_text=text, current_rating=current_rating, analyzer="gemini")
    except Exception as exc:
        log.warning("Restaurant feedback sentiment fallback: %s", exc)
        return _heuristic_feedback_analysis(text, current_rating=current_rating, analyzer="heuristic_after_exception")


def _heuristic_feedback_analysis(text: str, current_rating: int | None = None, analyzer: str = "heuristic") -> dict[str, Any]:
    lower = (text or "").lower()
    pos = sum(1 for term in POSITIVE_TERMS if term in lower)
    neg = sum(1 for term in NEGATIVE_TERMS if term in lower)
    if pos > neg:
        sentiment = "positive"
    elif neg > pos:
        sentiment = "negative"
    elif pos and neg:
        sentiment = "mixed"
    else:
        sentiment = "neutral"
    suggested = current_rating if current_rating and 1 <= current_rating <= 5 else 3
    if sentiment == "positive":
        suggested = 5 if pos >= neg + 2 else 4
    elif sentiment == "negative":
        suggested = 1 if neg >= pos + 2 else 2
    elif sentiment == "mixed":
        suggested = 3

    topics: dict[str, dict[str, Any]] = {}
    tags: list[str] = []
    for topic, terms in TOPIC_QUERY_TERMS.items():
        mentioned = any(term in lower for term in terms)
        topic_pos = sum(1 for term in terms + list(POSITIVE_TERMS) if term in lower) if mentioned else 0
        topic_neg = sum(1 for term in terms + list(NEGATIVE_TERMS) if term in lower) if mentioned else 0
        score = 0.0
        topic_sentiment = "unknown"
        if mentioned:
            tags.append(topic)
            score = max(-1.0, min(1.0, (topic_pos - topic_neg) / max(topic_pos + topic_neg, 1)))
            topic_sentiment = "positive" if score > 0.15 else "negative" if score < -0.15 else "neutral"
        topics[topic] = {"sentiment": topic_sentiment, "score": round(score, 2)}

    return {
        "suggested_rating": int(max(1, min(5, suggested))),
        "overall_sentiment": sentiment,
        "confidence": 0.55 if text else 0.0,
        "tags": tags[:8],
        "topic_sentiment": topics,
        "reason": "Suggested from semantic sentiment keywords. Customer can override before submitting.",
        "analyzer": analyzer,
    }


def _normalize_feedback_analysis(
    data: dict[str, Any],
    fallback_text: str = "",
    current_rating: int | None = None,
    analyzer: str = "gemini",
) -> dict[str, Any]:
    if not isinstance(data, dict):
        return _heuristic_feedback_analysis(fallback_text, current_rating=current_rating, analyzer="heuristic_after_invalid_json")
    topics = data.get("topic_sentiment") if isinstance(data.get("topic_sentiment"), dict) else {}
    normalized_topics: dict[str, dict[str, Any]] = {}
    for topic in FEEDBACK_TOPICS:
        value = topics.get(topic) if isinstance(topics.get(topic), dict) else {}
        score = value.get("score", 0)
        try:
            score = float(score)
        except (TypeError, ValueError):
            score = 0.0
        sentiment = str(value.get("sentiment") or "unknown").lower()
        if sentiment not in {"positive", "neutral", "negative", "mixed", "unknown"}:
            sentiment = "unknown"
        normalized_topics[topic] = {"sentiment": sentiment, "score": round(max(-1.0, min(1.0, score)), 2)}
    try:
        rating = int(data.get("suggested_rating") or current_rating or 3)
    except (TypeError, ValueError):
        rating = current_rating or 3
    sentiment = str(data.get("overall_sentiment") or "neutral").lower()
    if sentiment not in {"positive", "neutral", "negative", "mixed"}:
        sentiment = "neutral"
    tags = [str(tag).strip().lower().replace(" ", "_") for tag in (data.get("tags") or []) if str(tag).strip()]
    tags = [tag for tag in tags if tag in FEEDBACK_TOPICS][:8]
    return {
        "suggested_rating": int(max(1, min(5, rating))),
        "overall_sentiment": sentiment,
        "confidence": max(0.0, min(1.0, float(data.get("confidence") or 0.7))),
        "tags": tags,
        "topic_sentiment": normalized_topics,
        "reason": str(data.get("reason") or "Suggested from semantic sentiment. Customer can override before submitting.")[:400],
        "analyzer": analyzer,
    }


def _json_from_text(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"```(?:json)?\s*", "", text or "").replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        return json.loads(match.group(0)) if match else {}


def _feedback_intent_score(item: dict[str, Any], query: str) -> float:
    signals = item.get("feedback_signals") or []
    if isinstance(signals, str):
        try:
            signals = json.loads(signals)
        except json.JSONDecodeError:
            signals = []
    if not isinstance(signals, list):
        return 0.0
    lower_query = (query or "").lower()
    target_topics = [
        topic for topic, terms in TOPIC_QUERY_TERMS.items()
        if any(term in lower_query for term in terms)
    ]
    if not target_topics:
        target_topics = ["taste", "value"]
    scores: list[float] = []
    for signal in signals:
        if not isinstance(signal, dict):
            continue
        topics = signal.get("topic_sentiment") if isinstance(signal.get("topic_sentiment"), dict) else {}
        for topic in target_topics:
            value = topics.get(topic)
            if isinstance(value, dict):
                try:
                    scores.append(float(value.get("score") or 0))
                except (TypeError, ValueError):
                    pass
    if not scores:
        return 0.0
    avg = sum(scores) / len(scores)
    return max(-1.0, min(1.0, avg))


def _restaurant_recommendation_score(item: dict[str, Any], query: str, max_seen_price: float | None = None) -> float:
    text = " ".join(
        str(item.get(key) or "")
        for key in ("item_name", "description", "category", "restaurant_name", "cuisine_type")
    ).lower()
    terms = [term for term in re.findall(r"[a-z0-9]+", (query or "").lower()) if len(term) > 1]
    text_score = 0.35 if not terms else min(1.0, sum(1 for term in terms if term in text) / max(len(terms), 1))

    avg_rating = float(item.get("avg_rating") or 0)
    rating_score = avg_rating / 5 if avg_rating else 0.0
    verified_score = min(1.0, float(item.get("verified_rating_count") or 0) / 10)
    popularity_score = min(1.0, float(item.get("rating_count") or 0) / 20)
    intent_score = (_feedback_intent_score(item, query) + 1.0) / 2.0

    price_score = 0.35
    if item.get("price") is not None and max_seen_price:
        price_score = max(0.0, 1.0 - (float(item["price"]) / max_seen_price))

    score = (
        text_score * 0.35
        + rating_score * 0.25
        + verified_score * 0.15
        + price_score * 0.12
        + popularity_score * 0.08
        + intent_score * 0.05
    )
    return round(score * 100, 2)


def _restaurant_recommendation_reason(item: dict[str, Any]) -> str:
    reasons: list[str] = []
    if item.get("avg_rating"):
        reasons.append(f"{float(item['avg_rating']):.1f}/5 from {int(item.get('rating_count') or 0)} rating(s)")
    if item.get("verified_rating_count"):
        reasons.append(f"{int(item['verified_rating_count'])} verified order rating(s)")
    if item.get("price") is not None:
        reasons.append(f"{item.get('currency') or 'USD'} {float(item['price']):.2f}")
    intent = _feedback_intent_score(item, "")
    if intent > 0.15:
        reasons.append("positive customer sentiment signals")
    return "; ".join(reasons) or "Recommended from menu match and current availability"


def _safe_filename(name: str, suffix: str = ".txt") -> str:
    base = re.sub(r"[^A-Za-z0-9._ -]+", "", name).strip().replace(" ", "_")
    base = base[:80] or "restaurant-menu-intake"
    return base if base.lower().endswith(suffix) else base + suffix


def _language_code(language: str) -> str:
    language = (language or "").strip().lower()
    return language.split("-")[0] if language else "en"
