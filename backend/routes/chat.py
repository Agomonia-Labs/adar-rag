# routes/chat.py — 3-stage RAG: Hybrid Retrieval → Gemini Re-rank → Generate
from __future__ import annotations
import json, asyncio, logging, re
from difflib import SequenceMatcher
from typing import Any
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import StreamingResponse

from auth.dependencies import CurrentUser
from database.connection import get_db, get_pool
from services.llm import embed_query, chat_stream, rag_system
from services.vectordb import find_similar, TOP_K, RERANK_FETCH_K
from services.usage import check_and_log_daily_event
from services.reranker import rerank, RERANK_ENABLED
from services.language import primary_language, response_language_instruction
from services.pii import redact_text
from services.tracing import start_trace, finish_trace, span, record_llm_event

router = APIRouter()
log = logging.getLogger("docintel.chat.route")

# Fetch more candidates when re-ranking so the re-ranker has enough to work with
_FETCH_K = RERANK_FETCH_K if RERANK_ENABLED else TOP_K


class ChatRequest(BaseModel):
    question:     str
    document_ids: list[str]
    history:      list[dict] = []
    workspace_id: str | None = None
    redact_pii:   bool = False
    agent_mode:   str = "auto"  # auto | off | force


@router.post("/stream")
async def chat_stream_endpoint(
    request: Request,
    req: ChatRequest,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    if not req.question.strip():
        raise HTTPException(400, "question must not be empty")
    if not req.document_ids:
        raise HTTPException(400, "Select at least one embedded document to query")

    user_id = str(current_user["id"])
    rows = await db.fetch(
        """SELECT d.id, d.status, d.doc_language, d.doc_type, d.doc_domain,
                  d.original_name, d.workspace_id
           FROM documents d
           WHERE d.id = ANY($1::uuid[])
             AND d.status != 'deleted'
             AND (
               d.user_id = $2
               OR EXISTS (
                 SELECT 1 FROM workspace_members wm
                 WHERE wm.workspace_id = d.workspace_id
                   AND wm.user_id = $2
               )
             )""",
        req.document_ids, user_id,
    )
    found_ids    = {str(r["id"]) for r in rows}
    not_found    = set(req.document_ids) - found_ids
    not_embedded = {str(r["id"]) for r in rows if r["status"] != "embedded"}
    response_lang = primary_language([r["doc_language"] for r in rows])

    if not_found:
        raise HTTPException(403, f"Documents not found or not accessible: {not_found}")
    if not_embedded:
        raise HTTPException(400, f"Documents not yet embedded: {not_embedded}")

    doc_rows = [dict(r) for r in rows]
    trace_id = await start_trace(
        "chat",
        trace_id=getattr(request.state, "trace_id", None),
        user_id=user_id,
        workspace_id=req.workspace_id,
        input_text=req.question,
        client_info={
            "ip": request.client.host if request.client else None,
            "user_agent": request.headers.get("user-agent"),
        },
        metadata={
            "document_ids": req.document_ids,
            "history_count": len(req.history or []),
            "response_language": response_lang,
            "rerank_enabled": RERANK_ENABLED,
            "redact_pii": req.redact_pii,
            "agent_mode": req.agent_mode,
        },
    )

    async def generate():
        queue: asyncio.Queue = asyncio.Queue()
        output_tokens: list[str] = []

        async def on_token(t: str):
            output_tokens.append(t)
            await queue.put(("token", t))

        async def run():
            try:
                question_for_model = redact_text(req.question, req.redact_pii).text
                # ── Atomically reserve daily query usage before expensive model calls ──
                async with span("usage_limit", trace_id=trace_id, metadata={"event_type": "query"}):
                    async with get_pool().acquire() as _conn:
                        await check_and_log_daily_event(
                            _conn,
                            user_id,
                            "query",
                            "max_queries_day",
                            metadata={"doc_count": len(req.document_ids)},
                        )

                # ── Stage 1: Embed query ──────────────────────────────────
                async with span("query_embedding", trace_id=trace_id, metadata={"input_hash": "sha256"}) as sp:
                    query_vec = await embed_query(question_for_model)
                    await record_llm_event(
                        trace_id=trace_id,
                        span_id=sp,
                        provider="gemini",
                        model="embedding",
                        operation="embed_query",
                        user_prompt=question_for_model,
                        tool_response={"embedding_dim": len(query_vec)},
                    )

                # ── Stage 2: Hybrid retrieval (vector + BM25 + RRF) ──────
                #    Fetch RERANK_FETCH_K candidates — more than final TOP_K
                async with span("hybrid_retrieval", trace_id=trace_id, metadata={"fetch_k": _FETCH_K}) as sp:
                    candidates = await find_similar(
                        query_embedding=query_vec,
                        query_text=question_for_model,   # enables BM25 + RRF fusion
                        user_id=user_id,
                        document_ids=req.document_ids,
                        limit=_FETCH_K,
                    )
                    await record_llm_event(
                        trace_id=trace_id,
                        span_id=sp,
                        provider="postgres",
                        model="pgvector+fts",
                        operation="hybrid_retrieval",
                        user_prompt=question_for_model,
                        tool_request={"document_ids": req.document_ids, "limit": _FETCH_K},
                        tool_response={"candidates": _chunk_trace(candidates, redact_pii=req.redact_pii)},
                    )

                # ── Stage 3: Gemini cross-encoder re-ranking ──────────────
                #    Gemini scores each (query, chunk) pair together —
                #    far more accurate than independent bi-encoder scores
                async with span("gemini_rerank", trace_id=trace_id, metadata={"top_k": TOP_K, "candidate_count": len(candidates)}) as sp:
                    chunks = await rerank(
                        query=question_for_model,
                        chunks=candidates,
                        top_k=TOP_K,
                    )
                    await record_llm_event(
                        trace_id=trace_id,
                        span_id=sp,
                        provider="gemini",
                        model="rerank",
                        operation="rerank",
                        user_prompt=question_for_model,
                        tool_request={"candidates": _chunk_trace(candidates, redact_pii=req.redact_pii)},
                        tool_response={"ranked": _chunk_trace(chunks, redact_pii=req.redact_pii)},
                    )

                # ── Stage 4: Build grounded context ──────────────────────
                async with span("prompt_build", trace_id=trace_id, metadata={"chunk_count": len(chunks)}):
                    context = _build_context(chunks, redact_pii=req.redact_pii)

                restaurant_context = ""
                restaurant_meta: dict = {"enabled": False}
                if _is_restaurant_context(doc_rows, question_for_model):
                    async with span("restaurant_db_context", trace_id=trace_id, metadata={"enabled": True}) as sp:
                        try:
                            async with get_pool().acquire() as _conn:
                                restaurant_context, restaurant_meta = await _restaurant_db_context(
                                    _conn,
                                    user_id=user_id,
                                    workspace_id=req.workspace_id,
                                    question=question_for_model,
                                    chunks=chunks,
                                )
                        except Exception as exc:
                            log.warning("Restaurant DB context lookup failed; falling back to RAG only: %s", exc)
                            restaurant_context = ""
                            restaurant_meta = {"enabled": True, "error": str(exc), "fallback": "rag_only"}
                        await record_llm_event(
                            trace_id=trace_id,
                            span_id=sp,
                            provider="postgres",
                            model="restaurant_menu_store",
                            operation="restaurant_db_context",
                            user_prompt=question_for_model,
                            tool_response=restaurant_meta,
                        )
                    if restaurant_context:
                        context = f"{restaurant_context}\n\n---\n\n{context}"

                agentic_context = ""
                agentic_meta: dict = {"enabled": req.agent_mode != "off"}
                if req.agent_mode != "off":
                    async with span("agentic_context", trace_id=trace_id, metadata={"mode": req.agent_mode}) as sp:
                        try:
                            async with get_pool().acquire() as _conn:
                                agentic_context, agentic_meta = await _load_agentic_context(
                                    _conn,
                                    doc_rows,
                                    question_for_model,
                                    redact_pii=req.redact_pii,
                                    force=req.agent_mode == "force",
                                )
                        except Exception as exc:
                            log.warning("Chat agentic context lookup failed; falling back to RAG only: %s", exc)
                            agentic_context = ""
                            agentic_meta = {
                                "enabled": True,
                                "error": str(exc),
                                "fallback": "rag_only",
                            }
                        await record_llm_event(
                            trace_id=trace_id,
                            span_id=sp,
                            provider="postgres",
                            model="agent_workflow_store",
                            operation="agentic_context_lookup",
                            user_prompt=question_for_model,
                            tool_request={
                                "document_ids": req.document_ids,
                                "agent_mode": req.agent_mode,
                            },
                            tool_response=agentic_meta,
                        )
                    if agentic_context:
                        context = f"{agentic_context}\n\n---\n\n{context}"

                # ── Stage 5: Stream LLM answer ────────────────────────────
                messages = [
                    {"role": m["role"], "content": redact_text(m["content"], req.redact_pii).text}
                    for m in req.history[-12:]
                    if m.get("role") and m.get("content")
                ] + [{"role": "user", "content": question_for_model}]

                language_instruction = response_language_instruction(response_lang)
                if agentic_context:
                    language_instruction = (
                        f"{language_instruction}\n\n"
                        "AGENTIC WORKFLOW RULE:\n"
                        "The retrieved context may include [Agentic Source N] blocks produced by the lease or healthcare "
                        "agentic workflow. For domain-specific questions about lease parties, rent, obligations, critical "
                        "dates, clause risks, healthcare labs, medications, visit summaries, doctor-patient conversations, "
                        "SOAP notes, follow-ups, prior authorization evidence, or care gaps, use "
                        "those agentic blocks first because they are structured review outputs. Cite them inline as "
                        "[Agentic Source N]. Use [Source N] retrieved chunks to verify or fill details. If an agentic block "
                        "is partial, say what is missing and answer from document chunks where possible."
                    )
                if restaurant_context:
                    language_instruction = (
                        f"{language_instruction}\n\n"
                        "RESTAURANT ORDERING RULE:\n"
                        "For restaurant menu, price comparison, carryout, pickup, or ordering questions, use "
                        "[Restaurant DB Source] rows first when they are present. Include restaurant name, item, price, "
                        "restaurant ID, email, phone, and address when available so the user can add the item to a "
                        "carryout cart. If the user asks to order an item, provide an Order Details table with Field "
                        "and Value rows for Restaurant, Item, Price, Menu Item ID, Restaurant ID, Email, Phone, and "
                        "Address, then tell the user to click the Add button and place the carryout order from the cart. "
                        "Do not say you cannot help place orders; explain that restaurant acceptance/cancellation happens "
                        "after submission. If document sources disagree with the restaurant DB rows, state the difference "
                        "briefly and prefer the DB row for ordering/contact fields."
                    )
                system_prompt = rag_system(context, language_instruction)
                async with span("llm_generate", trace_id=trace_id, metadata={"provider": "gemini", "message_count": len(messages)}) as sp:
                    await chat_stream(messages, system_prompt, on_token)
                    await record_llm_event(
                        trace_id=trace_id,
                        span_id=sp,
                        provider="gemini",
                        model="chat",
                        operation="chat_generate",
                        system_prompt=system_prompt,
                        user_prompt=question_for_model,
                        tool_request={"messages": messages, "sources": _chunk_trace(chunks, redact_pii=req.redact_pii)},
                        llm_response=redact_text("".join(output_tokens), req.redact_pii).text,
                    )
                restaurant_actions = {}
                await finish_trace(trace_id, "success")
                await queue.put(("done", {
                    "sources": _sanitise(chunks, redact_pii=req.redact_pii),
                    "actions": restaurant_actions,
                }))

            except HTTPException as exc:
                await finish_trace(trace_id, "error", str(exc.detail))
                await queue.put(("error", exc.detail))
            except Exception as exc:
                await finish_trace(trace_id, "error", str(exc))
                await queue.put(("error", str(exc)))

        task = asyncio.create_task(run())

        while True:
            item = await queue.get()
            if   item[0] == "token": yield f"data: {json.dumps({'type':'token','text':item[1]})}\n\n"
            elif item[0] == "done":
                payload = item[1] if isinstance(item[1], dict) else {"sources": item[1]}
                yield f"data: {json.dumps({'type':'done', **payload})}\n\n"
                break
            elif item[0] == "error": yield f"data: {json.dumps({'type':'error','error':item[1]})}\n\n"; break

        await task

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Trace-Id": trace_id},
    )


LEASE_TERMS = {
    "lease", "landlord", "tenant", "rent", "base rent", "security deposit",
    "commencement", "expiration", "renewal", "amendment", "extension",
    "premises", "obligation", "critical date", "clause", "termination",
    "assignment", "sublease", "insurance", "maintenance", "cam",
}

HEALTHCARE_TERMS = {
    "health", "healthcare", "clinical", "patient", "lab", "labs", "medication",
    "medicine", "diagnosis", "visit", "after visit", "discharge", "follow up",
    "follow-up", "care gap", "allergy", "vital", "provider", "doctor",
    "referral", "prior authorization", "claim", "procedure", "test result",
}

RESTAURANT_TERMS = {
    "restaurant", "menu", "food", "dish", "item", "price", "prices", "order",
    "carryout", "pickup", "takeout", "compare", "biryani", "naan", "curry",
    "appetizer", "entree", "dessert", "beverage", "drink", "lunch", "dinner",
}


async def _load_agentic_context(
    db,
    docs: list[dict],
    question: str,
    redact_pii: bool = False,
    force: bool = False,
) -> tuple[str, dict]:
    targets = _agentic_targets(docs, question, force=force)
    meta = {
        "enabled": True,
        "targets": targets,
        "loaded": [],
        "missing": [],
    }
    if not targets:
        return "", meta

    blocks: list[str] = []
    source_index = 1
    for doc in docs:
        doc_id = str(doc.get("id"))
        if "lease" in targets and _is_lease_doc(doc, question, force=force):
            block = await _lease_agentic_block(db, doc_id, doc, source_index, redact_pii=redact_pii)
            if block:
                blocks.append(block)
                source_index += _count_agentic_sources(block)
                meta["loaded"].append({"document_id": doc_id, "vertical": "lease"})
            else:
                meta["missing"].append({"document_id": doc_id, "vertical": "lease"})

        if "healthcare" in targets and _is_healthcare_doc(doc, question, force=force):
            block = await _healthcare_agentic_block(db, doc_id, doc, source_index, redact_pii=redact_pii)
            if block:
                blocks.append(block)
                source_index += _count_agentic_sources(block)
                meta["loaded"].append({"document_id": doc_id, "vertical": "healthcare"})
            else:
                meta["missing"].append({"document_id": doc_id, "vertical": "healthcare"})

    if not blocks:
        return "", meta
    return "\n\n---\n\n".join(blocks), meta


def _count_agentic_sources(block: str) -> int:
    return max(1, len(set(re.findall(r"\[Agentic Source\s+(\d+):", block or ""))))


def _agentic_targets(docs: list[dict], question: str, force: bool = False) -> list[str]:
    targets: list[str] = []
    if force or any(_is_lease_doc(doc, question, force=force) for doc in docs):
        targets.append("lease")
    if force or any(_is_healthcare_doc(doc, question, force=force) for doc in docs):
        targets.append("healthcare")
    return targets


def _is_lease_doc(doc: dict, question: str, force: bool = False) -> bool:
    if force:
        return True
    doc_type = (doc.get("doc_type") or "").lower()
    doc_domain = (doc.get("doc_domain") or "").lower()
    name = (doc.get("original_name") or "").lower()
    q = question.lower()
    metadata_match = "lease" in doc_type or doc_domain in {"real_estate", "real estate"} or "lease" in name
    question_match = any(term in q for term in LEASE_TERMS)
    return metadata_match or question_match


def _is_healthcare_doc(doc: dict, question: str, force: bool = False) -> bool:
    if force:
        return True
    doc_type = (doc.get("doc_type") or "").lower()
    doc_domain = (doc.get("doc_domain") or "").lower()
    name = (doc.get("original_name") or "").lower()
    q = question.lower()
    metadata_match = (
        "health" in doc_type
        or "clinical" in doc_type
        or doc_domain in {"healthcare", "medical", "clinical"}
        or any(term in name for term in ("lab", "visit", "medication", "clinical", "health"))
    )
    question_match = any(term in q for term in HEALTHCARE_TERMS)
    return metadata_match or question_match


async def _restaurant_chat_actions(
    db,
    user_id: str,
    workspace_id: str | None,
    docs: list[dict],
    question: str,
    answer_text: str = "",
    chunks: list[dict] | None = None,
) -> dict:
    if not _is_restaurant_context(docs, question):
        return {}

    safe_workspace_id = _uuid_or_none(workspace_id)
    terms = _restaurant_query_terms(f"{question}\n{answer_text}")
    rows = await _fetch_restaurant_menu_rows(db, user_id, safe_workspace_id, terms, limit=250)
    restaurant_rows = await db.fetch(
        f"""
        SELECT r.id AS restaurant_id, r.name AS restaurant_name, r.address, r.phone,
               r.email, r.cuisine_type
        FROM restaurants r
        WHERE {_restaurant_access_sql("r")}
          AND ($2::uuid IS NULL OR r.workspace_id=$2::uuid OR r.workspace_id IS NULL)
        ORDER BY r.name
        LIMIT 500
        """,
        user_id,
        safe_workspace_id,
    )
    context_parts = _restaurant_context_parts(question, answer_text, chunks or [])
    scored = []
    for row in rows:
        score = _restaurant_menu_match_score(row, context_parts)
        if score >= 35:
            scored.append((score, row))
    scored.sort(key=lambda item: (-item[0], item[1]["restaurant_name"] or "", item[1]["item_name"] or ""))
    items = [
        {**_restaurant_menu_action_row(row), "action_score": round(score, 3)}
        for score, row in scored[:10]
    ]
    items = _merge_restaurant_answer_actions(items, rows, restaurant_rows, answer_text)
    if not items:
        return {}
    return {
        "type": "restaurant_menu_actions",
        "source": "restaurant_menu_semantic_lookup",
        "restaurant_menu_items": items,
    }


async def _restaurant_db_context(
    db,
    *,
    user_id: str,
    workspace_id: str | None,
    question: str,
    chunks: list[dict] | None = None,
) -> tuple[str, dict]:
    safe_workspace_id = _uuid_or_none(workspace_id)
    terms = _restaurant_query_terms(question)
    rows = await _fetch_restaurant_menu_rows(db, user_id, safe_workspace_id, terms, limit=120)
    selected = rows[:12]
    if not selected:
        return "", {"enabled": True, "matched_rows": 0, "available_rows": len(rows)}

    lines = ["[Restaurant DB Source: menu/contact rows for ordering]"]
    for index, row in enumerate(selected, start=1):
        price = row["price"]
        price_text = "not set" if price is None else f"{row['currency'] or 'USD'} {float(price):.2f}"
        lines.append(
            " | ".join(
                [
                    f"{index}. restaurant={row['restaurant_name'] or ''}",
                    f"restaurant_id={row['restaurant_id']}",
                    f"email={row['email'] or 'not provided'}",
                    f"phone={row['phone'] or 'not provided'}",
                    f"address={row['address'] or 'not provided'}",
                    f"cuisine={row['cuisine_type'] or 'not provided'}",
                    f"menu_item_id={row['id']}",
                    f"item={row['item_name'] or ''}",
                    f"category={row['category'] or ''}",
                    f"price={price_text}",
                ]
            )
        )
    return "\n".join(lines), {
        "enabled": True,
        "matched_rows": len(selected),
        "available_rows": len(rows),
        "restaurants": sorted({str(row["restaurant_name"]) for row in selected if row["restaurant_name"]}),
    }


async def _fetch_restaurant_menu_rows(db, user_id: str, workspace_id: str | None, terms: list[str], limit: int = 120):
    return await db.fetch(
        f"""
        SELECT mi.id, mi.restaurant_id, mi.category, mi.item_name, mi.price,
               mi.currency, mi.quantity, mi.description, mi.availability,
               mi.dietary_tags, mi.spice_level,
               r.name AS restaurant_name, r.address, r.phone, r.email, r.cuisine_type
        FROM restaurant_menu_items mi
        JOIN restaurants r ON r.id=mi.restaurant_id
        WHERE {_restaurant_access_sql("r")}
          AND ($2::uuid IS NULL OR r.workspace_id=$2::uuid OR r.workspace_id IS NULL)
          AND LOWER(COALESCE(mi.availability, 'available')) <> 'unavailable'
          AND (
            cardinality($3::text[]) = 0
            OR EXISTS (
              SELECT 1 FROM unnest($3::text[]) term
              WHERE mi.item_name ILIKE '%' || term || '%'
                 OR COALESCE(mi.description, '') ILIKE '%' || term || '%'
                 OR COALESCE(mi.category, '') ILIKE '%' || term || '%'
                 OR r.name ILIKE '%' || term || '%'
            )
          )
        ORDER BY r.name, mi.category, mi.item_name
        LIMIT $4
        """,
        user_id,
        workspace_id,
        terms,
        limit,
    )


def _is_restaurant_context(docs: list[dict], question: str) -> bool:
    q = (question or "").lower()
    question_match = any(term in q for term in RESTAURANT_TERMS)
    doc_match = False
    for doc in docs:
        doc_type = (doc.get("doc_type") or "").lower()
        doc_domain = (doc.get("doc_domain") or "").lower()
        name = (doc.get("original_name") or "").lower()
        if (
            "restaurant" in doc_type
            or "menu" in doc_type
            or doc_domain in {"restaurant", "restaurants", "food", "food_service"}
            or any(term in name for term in ("restaurant", "menu", "food"))
        ):
            doc_match = True
            break
    return question_match or doc_match


def _merge_restaurant_answer_actions(items: list[dict], rows: list, restaurant_rows: list, answer_text: str) -> list[dict]:
    existing_keys = {
        (
            str(item.get("restaurant_id") or ""),
            _normalise_restaurant_text(item.get("item_name") or ""),
        )
        for item in items
    }
    restaurant_lookup: dict[str, dict] = {}
    for row in restaurant_rows:
        restaurant = _restaurant_contact_action_row(row)
        key = _normalise_restaurant_text(restaurant.get("restaurant_name") or "")
        if key:
            restaurant_lookup[key] = restaurant
    for row in rows:
        restaurant = _restaurant_menu_action_row(row)
        key = _normalise_restaurant_text(restaurant.get("restaurant_name") or "")
        if key and key not in restaurant_lookup:
            restaurant_lookup[key] = restaurant

    for extracted in _extract_restaurant_answer_rows(answer_text):
        restaurant_key = _normalise_restaurant_text(extracted["restaurant_name"])
        restaurant = restaurant_lookup.get(restaurant_key)
        if not restaurant:
            restaurant = _best_restaurant_name_match(restaurant_key, restaurant_lookup)
        if not restaurant:
            continue
        menu_match = _best_menu_item_match(extracted, rows, restaurant.get("restaurant_id"))
        action = _restaurant_menu_action_row(menu_match) if menu_match else restaurant
        dedupe_key = (
            str(action.get("restaurant_id") or ""),
            _normalise_restaurant_text(extracted["item_name"]),
        )
        if dedupe_key in existing_keys:
            continue
        existing_keys.add(dedupe_key)
        items.append({
            "id": action.get("id") or f"chat:{action.get('restaurant_id')}:{abs(hash(dedupe_key))}",
            "menu_item_id": action.get("id") if menu_match else None,
            "restaurant_id": action.get("restaurant_id"),
            "restaurant_name": action.get("restaurant_name"),
            "address": action.get("address"),
            "phone": action.get("phone"),
            "email": action.get("email"),
            "cuisine_type": action.get("cuisine_type"),
            "category": action.get("category") or extracted.get("category") or "",
            "item_name": extracted["item_name"],
            "price": action.get("price") if action.get("price") is not None else extracted.get("price"),
            "currency": action.get("currency") or "USD",
            "quantity": action.get("quantity") or "",
            "description": action.get("description") or "Matched from the chat answer. Restaurant can confirm final availability and price.",
            "availability": action.get("availability") or "chat_answer",
            "action_score": 60 if menu_match else 42,
            "source": "db_menu_item_match" if menu_match else "db_restaurant_match",
        })
    return items[:10]


def _extract_restaurant_answer_rows(answer_text: str) -> list[dict]:
    rows: list[dict] = []
    for raw_line in (answer_text or "").splitlines():
        line = raw_line.strip()
        if not line or "---" in line:
            continue
        cells = [cell.strip(" *") for cell in (line.split("|") if "|" in line else re.split(r"\t+", line)) if cell.strip(" *")]
        if len(cells) < 3:
            cells = re.split(r"\s{2,}", line)
        if len(cells) < 3:
            continue
        joined = " ".join(cells).lower()
        if "restaurant" in joined and ("price" in joined or "source" in joined):
            continue
        price = _extract_price(" ".join(cells[2:]))
        if price is None:
            continue
        rows.append({
            "restaurant_name": cells[0],
            "item_name": cells[1],
            "price": price,
        })
    return rows


def _extract_price(text: str) -> float | None:
    match = re.search(r"\$?\s*([0-9]+(?:\.[0-9]{1,2})?)", text or "")
    return float(match.group(1)) if match else None


def _best_restaurant_name_match(name: str, restaurants: dict[str, dict]) -> dict | None:
    best_key = ""
    best_score = 0.0
    for key in restaurants:
        score = SequenceMatcher(None, name, key).ratio()
        if score > best_score:
            best_score = score
            best_key = key
    return restaurants.get(best_key) if best_score >= 0.82 else None


def _best_menu_item_match(extracted: dict, rows: list, restaurant_id: str | None) -> Any | None:
    restaurant_id = str(restaurant_id or "")
    target_item = _normalise_restaurant_text(extracted.get("item_name") or "")
    target_price = extracted.get("price")
    if not restaurant_id or not target_item:
        return None

    best_row = None
    best_score = 0.0
    target_tokens = set(_meaningful_restaurant_tokens(target_item))
    for row in rows:
        if str(row["restaurant_id"]) != restaurant_id:
            continue
        item_name = _normalise_restaurant_text(row["item_name"] or "")
        if not item_name:
            continue
        item_tokens = set(_meaningful_restaurant_tokens(item_name))
        token_score = 0.0
        if target_tokens or item_tokens:
            token_score = len(target_tokens & item_tokens) / max(len(target_tokens | item_tokens), 1)
        score = max(SequenceMatcher(None, target_item, item_name).ratio(), token_score) * 100
        if target_price is not None and row["price"] is not None:
            try:
                if abs(float(row["price"]) - float(target_price)) < 0.01:
                    score += 12
            except (TypeError, ValueError):
                pass
        if score > best_score:
            best_score = score
            best_row = row
    return best_row if best_score >= 58 else None


def _restaurant_context_parts(question: str, answer_text: str, chunks: list[dict]) -> dict:
    source_text = " ".join((chunk.get("content") or "")[:500] for chunk in chunks[:2])
    answer_hint = (answer_text or "")[:2500]
    return {
        "question": _normalise_restaurant_text(question),
        "answer": _normalise_restaurant_text(answer_hint),
        "source": _normalise_restaurant_text(source_text),
        "all": _normalise_restaurant_text(f"{question}\n{answer_hint}\n{source_text}")[:3500],
    }


def _restaurant_menu_match_score(row, context_parts: dict) -> float:
    question_text = context_parts.get("question", "")
    answer_text = context_parts.get("answer", "")
    source_text = context_parts.get("source", "")
    context_text = context_parts.get("all", "")
    item_name = _normalise_restaurant_text(row["item_name"] or "")
    restaurant_name = _normalise_restaurant_text(row["restaurant_name"] or "")
    category = _normalise_restaurant_text(row["category"] or "")
    description = _normalise_restaurant_text(row["description"] or "")
    row_text = " ".join(part for part in (item_name, restaurant_name, category, description) if part)

    item_tokens = _meaningful_restaurant_tokens(item_name)
    context_tokens = set(_meaningful_restaurant_tokens(context_text))
    if not item_tokens or not context_tokens:
        return 0.0

    exact_overlap = len([token for token in item_tokens if token in context_tokens]) / len(item_tokens)
    fuzzy_overlap = len([
        token for token in item_tokens
        if token in context_tokens or any(SequenceMatcher(None, token, other).ratio() >= 0.84 for other in context_tokens)
    ]) / len(item_tokens)

    score = max(exact_overlap, fuzzy_overlap) * 70
    if item_name and item_name in answer_text:
        score += 45
    elif item_name and item_name in question_text:
        score += 35
    elif item_name and item_name in source_text:
        score += 16
    elif row_text:
        score += SequenceMatcher(None, item_name, context_text[: max(len(item_name) * 4, 80)]).ratio() * 8
    if restaurant_name and restaurant_name in answer_text:
        score += 16
    elif restaurant_name and restaurant_name in context_text:
        score += 12
    if category and category in answer_text:
        score += 5
    price = row["price"]
    if price is not None:
        price_text = f"{float(price):.2f}".rstrip("0").rstrip(".")
        if price_text and price_text in context_text:
            score += 8
    return score


def _meaningful_restaurant_tokens(text: str) -> list[str]:
    stop = {
        "what", "which", "show", "give", "tell", "from", "with", "that", "this",
        "have", "has", "near", "menu", "item", "items", "food", "restaurant",
        "restaurants", "price", "prices", "compare", "order", "please", "list",
        "their", "there", "about", "does", "available", "carryout", "pickup",
        "source", "table", "following", "here", "based", "document",
        "and", "the", "for", "you", "are", "all", "can", "get", "one", "two",
    }
    terms = []
    raw_terms = re.findall(r"[a-zA-Z][a-zA-Z0-9'\-]{1,}", text or "")
    for raw in raw_terms:
        term = raw.lower().strip("-'")
        if len(term) < 2 or term in stop or term in terms:
            continue
        terms.append(term)
    return terms


def _restaurant_query_terms(text: str) -> list[str]:
    tokens = _meaningful_restaurant_tokens(text)
    preferred = [
        token for token in tokens
        if token not in {
            "comparison", "matched", "answer", "confirm", "final", "availability",
            "contact", "details", "email", "phone", "address", "source", "eval",
            "id", "usd",
        }
    ]
    return preferred[:4]


def _normalise_restaurant_text(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-zA-Z0-9.\s'-]", " ", text or "").lower()).strip()


def _uuid_or_none(value: str | None) -> str | None:
    if not value:
        return None
    return value if re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", value) else None


def _restaurant_access_sql(alias: str, user_param: str = "$1") -> str:
    return (
        f"({alias}.user_id={user_param}::uuid OR EXISTS ("
        f"SELECT 1 FROM workspace_members wm WHERE wm.workspace_id={alias}.workspace_id AND wm.user_id={user_param}::uuid"
        f"))"
    )


def _restaurant_menu_action_row(row) -> dict:
    data = dict(row)
    return {key: _action_json_value(value) for key, value in data.items()}


def _restaurant_contact_action_row(row) -> dict:
    data = dict(row)
    data.setdefault("id", None)
    data.setdefault("category", "")
    data.setdefault("item_name", "")
    data.setdefault("price", None)
    data.setdefault("currency", "USD")
    data.setdefault("quantity", "")
    data.setdefault("description", "")
    data.setdefault("availability", "")
    return {key: _action_json_value(value) for key, value in data.items()}


def _action_json_value(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "__str__") and value.__class__.__module__.startswith(("uuid", "decimal")):
        if value.__class__.__name__ == "Decimal":
            return float(value)
        return str(value)
    if isinstance(value, list):
        return [_action_json_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _action_json_value(v) for k, v in value.items()}
    return value


async def _lease_agentic_block(db, doc_id: str, doc: dict, source_index: int, redact_pii: bool = False) -> str:
    run = await db.fetchrow(
        """
        SELECT id, status, workflow_version, result_data, completed_at, updated_at
        FROM lease_agent_runs
        WHERE document_id=$1
        ORDER BY created_at DESC
        LIMIT 1
        """,
        doc_id,
    )
    payload: dict | None = None
    metadata: dict = {"source": "lease_agent_workflow"}
    if run:
        payload = _json(run["result_data"]) or {}
        metadata.update(
            {
                "run_id": str(run["id"]),
                "status": run["status"],
                "workflow_version": run["workflow_version"],
                "completed_at": _iso(run["completed_at"]),
                "updated_at": _iso(run["updated_at"]),
            }
        )
        evaluation = await _latest_agent_eval(db, "lease", str(run["id"]))
        if evaluation:
            metadata["evaluation"] = evaluation

    abstract = await db.fetchrow(
        """
        SELECT abstract_data, confidence, status, updated_at
        FROM lease_abstracts
        WHERE document_id=$1
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        doc_id,
    )
    if abstract:
        saved = _json(abstract["abstract_data"]) or {}
        if payload:
            payload = {
                **payload,
                "saved_lease_abstract": saved,
            }
        else:
            payload = saved
            metadata["source"] = "saved_lease_abstract"
        metadata["saved_abstract"] = {
            "status": abstract["status"],
            "confidence": abstract["confidence"],
            "updated_at": _iso(abstract["updated_at"]),
        }

    obligations = []
    if run:
        obligation_rows = await db.fetch(
            """
            SELECT title, party, category, priority, due_date, trigger, source, status, notes, approved
            FROM lease_obligations
            WHERE document_id=$1 AND run_id=$2
            ORDER BY due_date NULLS LAST, priority, title
            LIMIT 25
            """,
            doc_id,
            str(run["id"]),
        )
        obligations = [_row_to_json(r) for r in obligation_rows]
    if obligations:
        payload = payload or {}
        payload["saved_obligations"] = obligations

    return _agentic_block("lease", doc, source_index, metadata, payload, redact_pii=redact_pii)


async def _healthcare_agentic_block(db, doc_id: str, doc: dict, source_index: int, redact_pii: bool = False) -> str:
    runs = await db.fetch(
        """
        SELECT DISTINCT ON (workflow_id)
               id, status, workflow_id, workflow_version, result_data, completed_at, updated_at, created_at
        FROM vertical_agent_runs
        WHERE document_id=$1
          AND vertical='healthcare'
          AND workflow_id = ANY($2::text[])
        ORDER BY workflow_id, created_at DESC
        """,
        doc_id,
        ["healthcare_phase1", "healthcare_prior_auth_phase1", "healthcare_transcription_phase1"],
    )
    if not runs:
        return ""
    blocks = []
    for offset, run in enumerate(sorted(runs, key=lambda r: _healthcare_workflow_order(r["workflow_id"]))):
        payload = _json(run["result_data"]) or {}
        metadata = {
            "source": "healthcare_agent_workflow",
            "run_id": str(run["id"]),
            "status": run["status"],
            "workflow_id": run["workflow_id"],
            "workflow_version": run["workflow_version"],
            "completed_at": _iso(run["completed_at"]),
            "updated_at": _iso(run["updated_at"]),
        }
        evaluation = await _latest_agent_eval(db, "healthcare", str(run["id"]))
        if evaluation:
            metadata["evaluation"] = evaluation
        block = _agentic_block("healthcare", doc, source_index + offset, metadata, payload, redact_pii=redact_pii)
        if block:
            blocks.append(block)
    return "\n\n---\n\n".join(blocks)


def _healthcare_workflow_order(workflow_id: str) -> int:
    return {
        "healthcare_phase1": 1,
        "healthcare_transcription_phase1": 2,
        "healthcare_prior_auth_phase1": 3,
    }.get(workflow_id or "", 99)


async def _latest_agent_eval(db, vertical: str, run_id: str) -> dict | None:
    row = await db.fetchrow(
        """
        SELECT overall_score, gate_status, recommendations, policy, created_at
        FROM agent_workflow_evaluations
        WHERE vertical=$1 AND run_id=$2
        ORDER BY created_at DESC
        LIMIT 1
        """,
        vertical,
        run_id,
    )
    if not row:
        return None
    return {
        "overall_score": row["overall_score"],
        "gate_status": row["gate_status"],
        "recommendations": _json(row["recommendations"]) or [],
        "policy": _json(row["policy"]) or {},
        "created_at": _iso(row["created_at"]),
    }


def _agentic_block(
    vertical: str,
    doc: dict,
    source_index: int,
    metadata: dict,
    payload: dict | None,
    redact_pii: bool = False,
) -> str:
    if not payload:
        return ""
    compact = _compact_json(_trim_agentic_payload(payload))
    if redact_pii:
        compact = redact_text(compact, True).text
    return (
        f"[Agentic Source {source_index}: {vertical} workflow | "
        f"document \"{doc.get('original_name') or doc.get('id')}\" | metadata]\n"
        f"{_compact_json(metadata, max_chars=1800)}\n\n"
        f"[Agentic Source {source_index}: {vertical} structured findings]\n"
        f"{compact}"
    )


def _trim_agentic_payload(payload: dict) -> dict:
    keys = [
        "summary",
        "abstract",
        "approved_abstract",
        "saved_lease_abstract",
        "critical_dates",
        "obligation_checklist",
        "saved_obligations",
        "clause_flags",
        "risk_flags",
        "clinical_summary",
        "lab_results",
        "medications",
        "care_gaps",
        "follow_up_actions",
        "patient_timeline",
        "administrative_flags",
        "prior_auth_request",
        "policy_criteria",
        "evidence_map",
        "gap_detection",
        "prior_auth_packet",
        "policy_documents",
        "conversation_transcript",
        "conversation_intake",
        "soap_note",
        "patient_summary",
        "followup_checklist",
        "scribe_governance",
        "approved_packet",
        "agent_quality",
        "confidence",
    ]
    trimmed = {key: payload[key] for key in keys if key in payload and payload[key] not in (None, "", [], {})}
    return trimmed or payload


def _compact_json(value, max_chars: int = 9000) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str, indent=2)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... [truncated agentic workflow context]"


def _row_to_json(row) -> dict:
    return {key: _iso(value) if hasattr(value, "isoformat") else value for key, value in dict(row).items()}


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _json(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _chunk_trace(chunks: list[dict], redact_pii: bool = False) -> list[dict]:
    return [
        {
            "document_id": str(c.get("document_id", "")),
            "doc_name": c.get("doc_name") or c.get("original_name"),
            "chunk_index": c.get("chunk_index"),
            "chunk_total": c.get("chunk_total"),
            "similarity": c.get("similarity"),
            "rerank_score": c.get("rerank_score"),
            "match_type": c.get("match_type"),
            "content_preview": redact_text((c.get("content") or "")[:500], redact_pii).text,
        }
        for c in chunks
    ]


def _build_context(chunks: list[dict], redact_pii: bool = False) -> str:
    if not chunks:
        return "No relevant chunks found."
    parts = []
    for i, c in enumerate(chunks):
        match_type   = c.get("match_type", "vector")
        rerank_score = c.get("rerank_score")
        icon  = {"hybrid": "⚡", "keyword": "🔤", "vector": "🔍"}.get(match_type, "🔍")
        score = (f"rerank {rerank_score*100:.1f}%" if rerank_score is not None
                 else f"relevance {c.get('similarity', 0)*100:.1f}%")
        content = redact_text(c.get("content", ""), redact_pii).text
        parts.append(
            f"[Source {i+1}: \"{c.get('doc_name','')}\" | "
            f"chunk {(c.get('chunk_index') or 0)+1}/{c.get('chunk_total','?')} | "
            f"{icon} {match_type} | {score}]\n{content}"
        )
    return "\n\n---\n\n".join(parts)


def _sanitise(chunks: list[dict], redact_pii: bool = False) -> list[dict]:
    return [
        {
            "doc_name":     c.get("doc_name"),
            "chunk_index":  c.get("chunk_index"),
            "chunk_total":  c.get("chunk_total"),
            "similarity":   round(float(c.get("similarity") or 0), 4),
            "rerank_score": round(float(c.get("rerank_score") or 0), 4)
                            if c.get("rerank_score") is not None else None,
            "match_type":   c.get("match_type", "vector"),
            "preview":      redact_text((c.get("content") or "")[:300], redact_pii).text,
        }
        for c in chunks
    ]
