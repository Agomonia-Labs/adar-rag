from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

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
    result_data = _json(run.get("result_data")) or {}
    approved_packet = body.approved_packet or result_data.get("approved_packet")
    if not approved_packet:
        raise HTTPException(400, "No restaurant/menu packet available to approve")

    restaurant_id = await _save_restaurant_packet(db, user_id, run.get("workspace_id"), run_id, approved_packet)
    approved_packet = {**approved_packet, "restaurant_id": restaurant_id}
    await approve_vertical_run(db, run_id=run_id, user_id=user_id, approved_packet=approved_packet, notes=body.notes)
    await _persist_approved_restaurant_document(db, str(run["document_id"]), user_id, run.get("workspace_id"), approved_packet)
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
    rows = await db.fetch(
        f"""
        SELECT r.*,
               COUNT(mi.id)::int AS menu_count,
               MIN(mi.price) FILTER (WHERE mi.price IS NOT NULL) AS min_price,
               MAX(mi.price) FILTER (WHERE mi.price IS NOT NULL) AS max_price
        FROM restaurants r
        LEFT JOIN restaurant_menu_items mi ON mi.restaurant_id=r.id
        WHERE {_restaurant_access_sql("r")}
          AND ($2::uuid IS NULL OR r.workspace_id=$2::uuid)
        GROUP BY r.id
        ORDER BY r.updated_at DESC
        LIMIT 200
        """,
        user_id,
        workspace_id,
    )
    return {"restaurants": [_restaurant_row(row) for row in rows]}


@router.get("/restaurants/{restaurant_id}")
async def get_restaurant(restaurant_id: str, current_user: CurrentUser, db=Depends(get_db)):
    user_id = str(current_user["id"])
    row = await db.fetchrow(f"SELECT r.* FROM restaurants r WHERE r.id=$1 AND {_restaurant_access_sql('r', '$2')}", restaurant_id, user_id)
    if not row:
        raise HTTPException(404, "Restaurant not found")
    items = await db.fetch(
        """
        SELECT * FROM restaurant_menu_items
        WHERE restaurant_id=$1
        ORDER BY COALESCE(NULLIF(category, ''), 'zzz'), item_name
        """,
        restaurant_id,
    )
    return {
        "restaurant": _restaurant_row(row),
        "menu_items": [_menu_row(item) for item in items],
        "transcript": await _restaurant_source_transcript(db, dict(row), user_id),
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
    row = await db.fetchrow(f"SELECT r.* FROM restaurants r WHERE r.id=$1 AND {_restaurant_access_sql('r', '$2')}", restaurant_id, user_id)
    if not row:
        raise HTTPException(404, "Restaurant not found")
    if row.get("workspace_id"):
        from routes.workspaces import _require_role
        await _require_role(db, str(row["workspace_id"]), user_id, "editor")
    profile = body.restaurant_profile or {}
    name = str(profile.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Restaurant name is required")
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
        profile.get("email") or "",
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
    return await get_restaurant(restaurant_id, current_user, db)


@router.delete("/restaurants/{restaurant_id}")
async def delete_restaurant(
    restaurant_id: str,
    request: Request,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    row = await db.fetchrow(f"SELECT r.* FROM restaurants r WHERE r.id=$1 AND {_restaurant_access_sql('r', '$2')}", restaurant_id, user_id)
    if not row:
        raise HTTPException(404, "Restaurant not found")
    if row.get("workspace_id"):
        from routes.workspaces import _require_role
        await _require_role(db, str(row["workspace_id"]), user_id, "editor")
    await db.execute("DELETE FROM restaurants WHERE id=$1", restaurant_id)
    await audit(
        db,
        user_id=user_id,
        action="restaurant_menu_delete",
        resource_type="restaurant",
        resource_id=restaurant_id,
        metadata={"name": row.get("name")},
        ip_address=ip_from(request),
        user_agent=ua_from(request),
    )
    return {"deleted": True, "restaurant_id": restaurant_id}


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
    q = f"%{query.strip()}%" if query.strip() else "%"
    rows = await db.fetch(
        f"""
        SELECT mi.*, r.name AS restaurant_name, r.address, r.cuisine_type
        FROM restaurant_menu_items mi
        JOIN restaurants r ON r.id=mi.restaurant_id
        WHERE {_restaurant_access_sql("r")}
          AND ($2='' OR mi.item_name ILIKE $3 OR mi.description ILIKE $3 OR r.name ILIKE $3)
          AND ($4='' OR r.cuisine_type ILIKE '%' || $4 || '%')
          AND ($5='' OR EXISTS (
              SELECT 1 FROM jsonb_array_elements_text(mi.dietary_tags) tag
              WHERE tag ILIKE '%' || $5 || '%'
          ))
          AND ($6::numeric IS NULL OR mi.price <= $6::numeric)
          AND ($7::uuid IS NULL OR r.workspace_id=$7::uuid)
        ORDER BY mi.price NULLS LAST, r.name, mi.item_name
        LIMIT 100
        """,
        user_id,
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
    q = f"%{query.strip()}%"
    rows = await db.fetch(
        f"""
        SELECT mi.*, r.name AS restaurant_name, r.address, r.cuisine_type
        FROM restaurant_menu_items mi
        JOIN restaurants r ON r.id=mi.restaurant_id
        WHERE {_restaurant_access_sql("r")}
          AND (mi.item_name ILIKE $2 OR mi.description ILIKE $2)
          AND ($3='' OR r.cuisine_type ILIKE '%' || $3 || '%')
          AND ($4::uuid IS NULL OR r.workspace_id=$4::uuid)
        ORDER BY mi.price NULLS LAST, r.name, mi.item_name
        LIMIT 100
        """,
        user_id,
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


async def _save_restaurant_packet(db, user_id: str, workspace_id: str | None, run_id: str, packet: dict[str, Any]) -> str:
    profile = packet.get("restaurant_profile") or {}
    menu_items = packet.get("menu_items") if isinstance(packet.get("menu_items"), list) else []
    name = str(profile.get("name") or "Unnamed Restaurant").strip()
    address = str(profile.get("address") or "").strip()
    existing = await db.fetchrow(
        """
        SELECT id FROM restaurants
        WHERE user_id=$1
          AND COALESCE(workspace_id::text, '')=COALESCE($2::text, '')
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
            profile.get("email") or "",
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
            profile.get("email") or "",
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


def _clean(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


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


def _safe_filename(name: str, suffix: str = ".txt") -> str:
    base = re.sub(r"[^A-Za-z0-9._ -]+", "", name).strip().replace(" ", "_")
    base = base[:80] or "restaurant-menu-intake"
    return base if base.lower().endswith(suffix) else base + suffix


def _language_code(language: str) -> str:
    language = (language or "").strip().lower()
    return language.split("-")[0] if language else "en"
