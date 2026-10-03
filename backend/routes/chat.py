# routes/chat.py — 3-stage RAG: Hybrid Retrieval → Gemini Re-rank → Generate
from __future__ import annotations
import json, asyncio, logging, re
from contextlib import suppress
from difflib import SequenceMatcher
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID
from pydantic import BaseModel, Field, model_validator
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

# Bounds the gap between SSE events yielded to the client, not the total
# request duration -- every token/done/error resets the clock (see the
# asyncio.wait_for(queue.get(), ...) below), so a slow-but-steady answer of
# any length is fine. It only fires when run() genuinely stops producing
# *anything* for this long: a stage inside it (embedding, retrieval, re-rank,
# agentic/restaurant context, or the final generation call) hanging on an
# await with no bound of its own. Before this, that hang was invisible to the
# client too -- the SSE consumer loop below did a plain `await queue.get()`,
# so the request just sat open forever with no token/done/error ever sent
# ("Flashcards for entire lesson runs forever" traced back to this). Mirrors
# routes/summarize.py's STALL_TIMEOUT_SECONDS, which already had this guard;
# kept equal to llm.py's Gemini-branch httpx timeout for chat_stream so this
# friendlier message is what callers see instead of a raw httpx.ReadTimeout.
STALL_TIMEOUT_SECONDS = 240


class EvidenceWindow(BaseModel):
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_window(self):
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must be greater than start_seconds")
        return self


class DocumentEvidenceRange(BaseModel):
    document_id: str
    ranges: list[EvidenceWindow] = Field(min_length=1)


class ChatRequest(BaseModel):
    question:     str
    document_ids: list[str]
    history:      list[dict] = Field(default_factory=list)
    workspace_id: str | None = None
    redact_pii:   bool = False
    agent_mode:   str = "auto"  # auto | off | force
    response_language: Literal["en", "es", "bn", "hi", "ar", "fr"] | None = None
    evidence_ranges: list[DocumentEvidenceRange] = Field(default_factory=list)
    # ADAR Eats' customer-facing menu assistant: the caller usually isn't a
    # restaurant owner and has no embedded documents of their own to pick --
    # when true, skip the "select a document" requirement and widen the
    # Restaurant DB Source lookup below to every marketplace-visible
    # restaurant, not just ones the caller owns or is a workspace member of
    # (mirrors the `marketplace` query param on /api/restaurant/restaurants,
    # /menu/search, /menu/compare, /menu/recommend).
    marketplace: bool = False


@router.post("/stream")
async def chat_stream_endpoint(
    request: Request,
    req: ChatRequest,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    if not req.question.strip():
        raise HTTPException(400, "question must not be empty")
    if not req.document_ids and not req.marketplace:
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
    response_lang = req.response_language or primary_language([r["doc_language"] for r in rows])

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
                        limit=_FETCH_K * 3 if req.evidence_ranges else _FETCH_K,
                    )
                    candidates = _filter_candidates_by_evidence_ranges(candidates, req.evidence_ranges)
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
                restaurant_actions: dict = {}
                restaurant_action_candidates: list[dict] = []
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
                                    marketplace=req.marketplace,
                                )
                                restaurant_action_candidates = restaurant_meta.pop("action_candidates", []) or []
                        except Exception as exc:
                            log.warning("Restaurant DB context lookup failed; falling back to RAG only: %s", exc)
                            restaurant_context = ""
                            restaurant_meta = {"enabled": True, "error": str(exc), "fallback": "rag_only"}
                            restaurant_actions = {}
                            restaurant_action_candidates = []
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
                        if _is_restaurant_ordering_question(question_for_model):
                            context = restaurant_context
                        else:
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
                        "For restaurant menu, price comparison, carryout, pickup, or ordering questions, use only "
                        "[Restaurant DB Source] rows when they are present. Do not fill restaurant ordering rows from "
                        "regular [Source N] chunks because those chunks do not contain the authoritative restaurant_id "
                        "or menu_item_id. For price comparisons, use a table "
                        "with these columns exactly when available: Restaurant, Item, Price, Menu Item ID, Restaurant ID, "
                        "Email, Phone, Address. These fields allow the UI to render an Add button for the carryout cart. "
                        "Every menu row shown from a Restaurant DB Source row must include that same row's Menu Item ID "
                        "and Restaurant ID. If the item spelling in document text differs from the Restaurant DB row "
                        "for the same restaurant and price, prefer the Restaurant DB IDs and mention the spelling "
                        "difference briefly only if needed. "
                        "If the user asks to order an item, or asks to add an item to their cart/order (phrasings "
                        "like \"add biryani to my cart\", \"add it to cart\", \"put 2 of those in my order\"), treat this "
                        "as the same ordering intent. "
                        "First check whether the request names exactly one menu item unambiguously, meaning exactly one "
                        "Restaurant DB Source row matches it: a specific dish at a specific restaurant already named or "
                        "clearly established earlier in this conversation (do not require the user to repeat the "
                        "restaurant name once it was already settled -- e.g. once they picked \"Spice House\" for "
                        "biryani, \"add the naan too\" means naan at Spice House). "
                        "If it is unambiguous, provide an Order Details table with Field and Value rows for Restaurant, "
                        "Item, Price, Menu Item ID, Restaurant ID, Email, Phone, and Address, then tell the user it has "
                        "been added to their cart (not merely that they can click Add) -- the app adds it automatically "
                        "whenever exactly one item is identified this way. "
                        "If the same dish name matches more than one Restaurant DB Source row (different restaurants, or "
                        "different sizes/variants at the same restaurant) and nothing earlier in the conversation narrows "
                        "it down, do NOT guess or pick one for the user: ask a short clarifying question naming each "
                        "option (restaurant and price) and render them as a comparison table with the standard columns "
                        "(Restaurant, Item, Price, Menu Item ID, Restaurant ID, Email, Phone, Address) covering every "
                        "candidate, so the user can tap the one they want; say that tapping an option adds it to their "
                        "cart. Once the user's next message answers which one (by restaurant name, \"the first one\", "
                        "a size, etc.), resolve it the same unambiguous way above. "
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
                restaurant_actions = _restaurant_actions_from_candidates(
                    restaurant_action_candidates,
                    question_for_model,
                    "".join(output_tokens),
                    active_restaurant_ids=_active_restaurant_ids_from_history(req.history),
                )
                # "add to cart"/"order it" phrasing + exactly one resolved
                # item = unambiguous: the app adds it for the user right
                # away instead of waiting for a tap on a chip (see the
                # RESTAURANT ORDERING RULE prompt above, which tells the
                # model to say it was added, not just that the user can
                # click Add, in this same case). Two or more resolved
                # items is the ambiguous case -- the prompt asks the user
                # a clarifying question and the chips stay tap-to-add, no
                # auto_add flag.
                if (
                    restaurant_actions.get("restaurant_menu_items")
                    and len(restaurant_actions["restaurant_menu_items"]) == 1
                    and (
                        # The model rendered a single "Order Details"
                        # table, meaning it already judged this
                        # unambiguous and told the user it was added --
                        # that structural signal covers any phrasing
                        # ("Order 2 goat dum biryani from Royal Biryani
                        # House" has no "cart"/"add" in it at all), so it
                        # takes priority over the narrower wording-based
                        # regex, which stays as the fallback for the
                        # non-table conversational-answer path.
                        restaurant_actions.get("resolved_unambiguous")
                        or _detect_cart_add_intent(question_for_model)
                    )
                ):
                    restaurant_actions["auto_add"] = True
                    qty = restaurant_actions.get("resolved_quantity") or _extract_cart_add_quantity(question_for_model)
                    if qty:
                        restaurant_actions["auto_add_quantity"] = qty
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

        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=STALL_TIMEOUT_SECONDS)
                except asyncio.TimeoutError:
                    await finish_trace(trace_id, "error", "stream stalled -- no output for STALL_TIMEOUT_SECONDS")
                    yield f"data: {json.dumps({'type':'error','error':'This is taking longer than expected and may have stalled. Please try again.'})}\n\n"
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task
                    return
                if   item[0] == "token": yield f"data: {json.dumps({'type':'token','text':item[1]})}\n\n"
                elif item[0] == "done":
                    payload = item[1] if isinstance(item[1], dict) else {"sources": item[1]}
                    yield f"data: {json.dumps(_json_safe({'type':'done', **payload}))}\n\n"
                    break
                elif item[0] == "error": yield f"data: {json.dumps({'type':'error','error':item[1]})}\n\n"; break

            await task
        except asyncio.CancelledError:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            return

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
    marketplace: bool = False,
) -> tuple[str, dict]:
    safe_workspace_id = _uuid_or_none(workspace_id)
    terms = _restaurant_query_terms(question)
    rows = await _fetch_restaurant_menu_rows(db, user_id, safe_workspace_id, terms, limit=300, marketplace=marketplace)
    context_parts = _restaurant_context_parts(question, "", chunks or [])
    scored_rows = [
        (round(_restaurant_menu_match_score(row, context_parts), 3), row)
        for row in rows
    ]
    scored_rows.sort(key=lambda item: (-item[0], item[1]["restaurant_name"] or "", item[1]["item_name"] or ""))
    selected = _select_restaurant_db_context_rows(scored_rows, question)
    if not selected:
        return "", {"enabled": True, "matched_rows": 0, "available_rows": len(rows), "actions": {}}

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
    action_candidates = []
    _seen_candidate_ids: set[str] = set()
    # `selected` is exactly what the model sees and can render into its
    # table/chips. _select_restaurant_db_context_rows applies an
    # intent-bonus reorder (exact/strong dish-name match) that can rank a
    # row ahead of items with a higher raw match score, so capping this
    # pool to the top-N by raw score alone could silently exclude a row
    # the model just told the user was added to their cart -- breaking
    # auto_add and the "+Add" chip for that exact row. Guarantee every
    # `selected` row is present first (unconditionally), then fill any
    # remaining budget with the next highest-raw-score rows (used
    # elsewhere for broader fuzzy matching / extra "+Add" coverage).
    for row in selected:
        row_id = str(row["id"])
        if row_id in _seen_candidate_ids:
            continue
        _seen_candidate_ids.add(row_id)
        action_candidates.append({
            **_restaurant_menu_action_row(row),
            "menu_item_id": row_id,
            "source": "restaurant_db_context",
            "action_score": 999.0,
        })
    for score, row in scored_rows:
        if len(action_candidates) >= 80:
            break
        row_id = str(row["id"])
        if row_id in _seen_candidate_ids:
            continue
        _seen_candidate_ids.add(row_id)
        action_candidates.append({
            **_restaurant_menu_action_row(row),
            "menu_item_id": row_id,
            "source": "restaurant_db_context",
            "action_score": score,
        })
    return "\n".join(lines), {
        "enabled": True,
        "matched_rows": len(selected),
        "available_rows": len(rows),
        "restaurants": sorted({str(row["restaurant_name"]) for row in selected if row["restaurant_name"]}),
        "query_terms": terms,
        "action_candidates": action_candidates,
    }


async def _fetch_restaurant_menu_rows(
    db, user_id: str, workspace_id: str | None, terms: list[str], limit: int = 120, marketplace: bool = False
):
    return await db.fetch(
        f"""
        SELECT mi.id, mi.restaurant_id, mi.category, mi.item_name, mi.price,
               mi.currency, mi.quantity, mi.description, mi.availability,
               mi.dietary_tags, mi.spice_level,
               r.name AS restaurant_name, r.address, r.phone, r.email, r.cuisine_type
        FROM restaurant_menu_items mi
        JOIN restaurants r ON r.id=mi.restaurant_id
        WHERE (
            {_restaurant_access_sql("r")}
            OR ($5 AND r.marketplace_visible = TRUE)
          )
          AND ($5 OR $2::uuid IS NULL OR r.workspace_id=$2::uuid OR r.workspace_id IS NULL)
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
        marketplace,
    )


def _select_restaurant_db_context_rows(scored_rows: list[tuple[float, Any]], question: str, limit: int = 40) -> list[Any]:
    if not scored_rows:
        return []

    query_tokens = {
        token
        for token in _restaurant_query_terms(question)
        if token not in {"near", "nearby"}
    }
    exact_intent_rows: list[tuple[float, Any]] = []
    strong_intent_rows: list[tuple[float, Any]] = []
    fallback_rows: list[tuple[float, Any]] = []

    for score, row in scored_rows:
        item_tokens = set(_meaningful_restaurant_tokens(row["item_name"] or ""))
        overlap = len(query_tokens & item_tokens)
        if query_tokens and query_tokens.issubset(item_tokens):
            exact_intent_rows.append((score + 200, row))
        elif query_tokens and overlap >= max(2, len(query_tokens) - 1):
            strong_intent_rows.append((score + 80 + overlap, row))
        elif score > 0:
            fallback_rows.append((score, row))

    selected: list[Any] = []
    seen_rows: set[str] = set()

    def add_rows(candidates: list[tuple[float, Any]], max_rows: int | None = None) -> None:
        candidates.sort(key=lambda item: (-item[0], item[1]["restaurant_name"] or "", item[1]["item_name"] or ""))
        for _, row in candidates:
            row_id = str(row["id"])
            if row_id in seen_rows:
                continue
            seen_rows.add(row_id)
            selected.append(row)
            if len(selected) >= limit or (max_rows is not None and len(selected) >= max_rows):
                break

    add_rows(exact_intent_rows)
    if len(selected) < limit:
        add_rows(strong_intent_rows)
    if len(selected) < min(12, limit):
        add_rows(fallback_rows, max_rows=min(12, limit))
    return selected[:limit]


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


def _is_restaurant_ordering_question(question: str) -> bool:
    q = (question or "").lower()
    return any(
        term in q
        for term in (
            "menu",
            "menus",
            "price",
            "prices",
            "compare",
            "comparison",
            "order",
            "carryout",
            "pickup",
            "add",
            "cart",
            "restaurant id",
            "menu item id",
            "email",
            "phone",
            "address",
        )
    )


_CART_ADD_INTENT_RE = re.compile(
    r"\badd\b.{0,40}\b(cart|order|basket)\b"
    r"|\b(cart|order|basket)\b.{0,40}\badd\b"
    r"|\bput\b.{0,40}\b(cart|order|basket)\b"
    r"|\b(order|get me|i'?ll (take|have|get)|i want)\b.{0,40}\b(it|this|that|one|those|these)\b"
    r"|\border (it|this|that|one|those|these)\b"
    r"|\byes,? add\b|\bgo ahead and add\b",
    re.IGNORECASE,
)


def _detect_cart_add_intent(question: str) -> bool:
    """Narrow, imperative-phrasing detector for "please put this in my
    cart right now" -- distinct from the loose _is_restaurant_ordering_
    question keyword check above (which just flags the topic as
    ordering-related for prompt/context purposes). Only a question this
    function flags as True can trigger auto_add (see the done-event
    handler above): a casual "what's on the menu, I might order later"
    mentions "order" but isn't an instruction to act on right now, so it
    must NOT auto-add -- the user still taps a chip for that. This
    deliberately stays conservative (regex over a fixed phrase shape)
    rather than guessing from arbitrary wording, since a false positive
    here silently adds something nobody asked for."""
    q = (question or "").strip().lower()
    if not q:
        return False
    return bool(_CART_ADD_INTENT_RE.search(q))


_CART_ADD_QUANTITY_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "couple": 2, "a couple": 2,
    "three": 3, "a few": 3, "few": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


def _extract_cart_add_quantity(question: str) -> int | None:
    """Best-effort quantity for a cart-add follow-up like "add 2 more
    samosa to cart" or "add a couple more samosas" -- returns None (the
    caller then defaults to 1) when no quantity phrasing is found,
    rather than guessing. Digits win over number words when both somehow
    appear; this is intentionally conservative (digits, then a short
    fixed word list) rather than general number-word parsing, since an
    over-eager match here would silently add the wrong quantity."""
    q = (question or "").strip().lower()
    if not q:
        return None
    digit_match = re.search(r"\b(\d{1,2})\b", q)
    if digit_match:
        try:
            value = int(digit_match.group(1))
            if 1 <= value <= 50:
                return value
        except ValueError:
            pass
    for phrase, value in sorted(_CART_ADD_QUANTITY_WORDS.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{re.escape(phrase)}\b", q):
            return value
    return None


def _active_restaurant_ids_from_history(history: list[dict] | None) -> set[str]:
    """Finds the restaurant(s) the conversation already settled on, from
    the most recent assistant turn that rendered an ordering/comparison
    table (see _extract_restaurant_table_ids) -- used as a tie-breaker in
    _restaurant_actions_from_candidates so a context-free follow-up like
    "add 2 more samosa to cart" (no restaurant name of its own) resolves
    against the restaurant that prior "Order Details" table already
    named, rather than staying ambiguous across every restaurant that
    happens to sell the same dish (which is why the model could say
    "added" -- it can read the whole conversation -- while the app added
    nothing, since the candidate-scoring step only ever looked at the
    current message). Only the single most recent assistant message is
    considered: an older table is stale once a newer one (or a
    plain-text reply) has superseded it, and if that latest table still
    shows more than one restaurant (an unresolved comparison), no single
    restaurant is "active" yet, so this returns an empty set and the
    normal ambiguous-match handling applies."""
    for turn in reversed(history or []):
        if (turn.get("role") or "") != "assistant":
            continue
        pairs = _extract_restaurant_table_ids(str(turn.get("content") or ""))
        if not pairs:
            return set()
        return {restaurant_id.lower() for restaurant_id, _ in pairs}
    return set()


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


def _strip_md_inline(text: str) -> str:
    """Strip inline markdown formatting (`code`, **bold**, __bold__,
    *italic*, _italic_) the model commonly wraps a table cell's value in
    -- e.g. rendering a Menu Item ID as `0737c2f5-...` or a restaurant
    name as **Royal Biryani House Bothell**. The ids extracted from the
    rendered table have to match the plain (unwrapped) ids on the
    candidates list character-for-character, so any such wrapping must
    be removed before comparing -- otherwise `` `0737c2f5-...` `` !=
    "0737c2f5-..." and the lookup silently fails every time, even though
    the table shows the correct, real id.
    """
    text = text.strip()
    for _ in range(3):
        new_text = re.sub(r"^(\*{1,3}|_{1,3}|`+)", "", text)
        new_text = re.sub(r"(\*{1,3}|_{1,3}|`+)$", "", new_text)
        new_text = new_text.strip()
        if new_text == text:
            break
        text = new_text
    return text


def _extract_restaurant_table_ids(answer_text: str) -> list[tuple[str, str]]:
    """Deterministically reads every row of the ordering/comparison
    table the model is instructed to render (the RESTAURANT ORDERING
    RULE prompt) and returns the (restaurant_id, menu_item_id) pair for
    EVERY item/option shown -- so when the assistant actually displays
    food item details as a table, "+Add" coverage for each row never
    depends on the fuzzy relevance scoring in
    _restaurant_actions_from_candidates below (which was only ever meant
    to guess relevance for a plain conversational answer that doesn't
    render a table, and caps/thresholds results accordingly).

    Handles both table shapes the prompt asks for:
      - a per-item comparison table (one row per restaurant/item, with
        "Menu Item ID" and "Restaurant ID" columns), and
      - a single "Order Details" Field/Value table (one row per field).
    Returns an empty list if no recognizable table is found.
    """
    lines = [ln.strip() for ln in (answer_text or "").splitlines()]
    table_blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.startswith("|") and line.endswith("|") and line.count("|") >= 2:
            current.append(line)
        else:
            if current:
                table_blocks.append(current)
                current = []
    if current:
        table_blocks.append(current)

    def split_row(line: str) -> list[str]:
        return [cell.strip() for cell in line.strip("|").split("|")]

    def is_separator(cells: list[str]) -> bool:
        return bool(cells) and all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells if cell)

    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for block in table_blocks:
        if len(block) < 2:
            continue
        header = [c.lower() for c in split_row(block[0])]
        data_rows = [split_row(r) for r in block[1:] if not is_separator(split_row(r))]
        if "menu item id" in header and "restaurant id" in header:
            mi_idx = header.index("menu item id")
            r_idx = header.index("restaurant id")
            for row in data_rows:
                if len(row) <= max(mi_idx, r_idx):
                    continue
                menu_item_id = _strip_md_inline(row[mi_idx])
                restaurant_id = _strip_md_inline(row[r_idx])
                key = (restaurant_id.lower(), menu_item_id.lower())
                if menu_item_id and restaurant_id and key not in seen:
                    seen.add(key)
                    pairs.append((restaurant_id, menu_item_id))
        elif len(header) == 2 and "field" in header and "value" in header:
            field_idx = header.index("field")
            value_idx = header.index("value")
            field_map: dict[str, str] = {}
            for row in data_rows:
                if len(row) <= max(field_idx, value_idx):
                    continue
                field_map[row[field_idx].strip().lower()] = row[value_idx].strip()
            menu_item_id = _strip_md_inline(field_map.get("menu item id", ""))
            restaurant_id = _strip_md_inline(field_map.get("restaurant id", ""))
            key = (restaurant_id.lower(), menu_item_id.lower())
            if menu_item_id and restaurant_id and key not in seen:
                seen.add(key)
                pairs.append((restaurant_id, menu_item_id))
    return pairs


def _extract_order_details_fields(answer_text: str) -> tuple[str, str, int | None] | None:
    """Recognizes ONLY the single "Order Details" Field/Value table shape
    (Restaurant/Item/Price/.../Menu Item ID/Restaurant ID rows) that the
    RESTAURANT ORDERING RULE prompt renders specifically when it already
    judged the request unambiguous -- never the multi-row comparison
    table it renders instead to ask a clarifying question. Finding this
    shape is a far more reliable "the app should actually add this now"
    signal than trying to pattern-match every way a user might phrase an
    order (see _detect_cart_add_intent, kept as a fallback for the
    non-table conversational-answer path below): a user can ask to order
    something by directly naming the dish and quantity ("Order 2 goat
    dum biryani from Royal Biryani House") with no "cart"/"add" and no
    pronoun in sight, which that regex alone does not catch, even though
    the model already committed to a single resolved item and told the
    user it was added. Returns (restaurant_id, menu_item_id, quantity)
    -- quantity is None when the table has no Quantity row, so the
    caller falls back to parsing the user's own wording for it."""
    lines = [ln.strip() for ln in (answer_text or "").splitlines()]
    table_blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.startswith("|") and line.endswith("|") and line.count("|") >= 2:
            current.append(line)
        else:
            if current:
                table_blocks.append(current)
                current = []
    if current:
        table_blocks.append(current)

    def split_row(line: str) -> list[str]:
        return [cell.strip() for cell in line.strip("|").split("|")]

    def is_separator(cells: list[str]) -> bool:
        return bool(cells) and all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells if cell)

    for block in table_blocks:
        if len(block) < 2:
            continue
        header = [c.lower() for c in split_row(block[0])]
        if len(header) != 2 or "field" not in header or "value" not in header:
            continue
        field_idx = header.index("field")
        value_idx = header.index("value")
        field_map: dict[str, str] = {}
        for raw_row in block[1:]:
            row = split_row(raw_row)
            if is_separator(row) or len(row) <= max(field_idx, value_idx):
                continue
            field_map[row[field_idx].strip().lower()] = row[value_idx].strip()
        menu_item_id = _strip_md_inline(field_map.get("menu item id", ""))
        restaurant_id = _strip_md_inline(field_map.get("restaurant id", ""))
        if not (menu_item_id and restaurant_id):
            continue
        quantity: int | None = None
        qty_digits = re.search(r"\d+", field_map.get("quantity", ""))
        if qty_digits:
            try:
                quantity = int(qty_digits.group(0))
            except ValueError:
                quantity = None
        return (restaurant_id, menu_item_id, quantity)
    return None


def _restaurant_actions_from_candidates(
    candidates: list[dict],
    question: str,
    answer_text: str,
    active_restaurant_ids: set[str] | None = None,
) -> dict:
    if not candidates:
        return {}

    order_details = _extract_order_details_fields(answer_text)
    table_pairs = _extract_restaurant_table_ids(answer_text)
    if table_pairs:
        by_id = {
            (
                str(item.get("restaurant_id") or "").lower(),
                str(item.get("menu_item_id") or item.get("id") or "").lower(),
            ): item
            for item in candidates
        }
        table_items: list[dict] = []
        for restaurant_id, menu_item_id in table_pairs:
            match = by_id.get((restaurant_id.lower(), menu_item_id.lower()))
            if match:
                table_items.append({**match, "action_score": 999.0})
        if table_items:
            resolved_unambiguous = (
                len(table_items) == 1
                and order_details is not None
                and str(table_items[0].get("restaurant_id") or "").lower() == order_details[0].lower()
                and str(table_items[0].get("menu_item_id") or table_items[0].get("id") or "").lower() == order_details[1].lower()
            )
            result = {
                "type": "restaurant_menu_actions",
                "source": "restaurant_table_rows",
                "restaurant_menu_items": table_items[:30],
                "resolved_unambiguous": resolved_unambiguous,
            }
            if resolved_unambiguous and order_details[2]:
                result["resolved_quantity"] = order_details[2]
            return result
        # A table WAS rendered but none of its (restaurant_id,
        # menu_item_id) pairs matched a known candidate row (e.g. the
        # model mistyped or invented an id) -- fall through to the fuzzy
        # scoring below rather than returning nothing.

    question_norm = _normalise_restaurant_text(question)
    answer_norm = _normalise_restaurant_text(answer_text)
    answer_raw = (answer_text or "").lower()
    combined = f"{question_norm} {answer_norm}".strip()
    scored: list[tuple[float, dict]] = []
    for item in candidates:
        item_name = _normalise_restaurant_text(str(item.get("item_name") or ""))
        restaurant_name = _normalise_restaurant_text(str(item.get("restaurant_name") or ""))
        restaurant_id = str(item.get("restaurant_id") or "")
        menu_item_id = str(item.get("menu_item_id") or item.get("id") or "")
        if not item_name or not restaurant_id or not menu_item_id:
            continue
        item_tokens = set(_meaningful_restaurant_tokens(item_name))
        question_tokens = set(_meaningful_restaurant_tokens(question_norm))
        answer_tokens = set(_meaningful_restaurant_tokens(answer_norm))
        item_question_overlap = len(item_tokens & question_tokens) / max(len(item_tokens), 1)
        item_answer_overlap = len(item_tokens & answer_tokens) / max(len(item_tokens), 1)
        item_name_in_answer = bool(item_name and item_name in answer_norm)
        item_name_in_question = bool(item_name and item_name in question_norm)
        item_relevant = (
            item_name_in_answer
            or item_name_in_question
            or item_answer_overlap >= 0.5
            or item_question_overlap >= 0.5
        )
        if not item_relevant:
            continue
        score = float(item.get("action_score") or 0)
        if item_name_in_answer:
            score += 80
        elif item_answer_overlap >= 0.5:
            score += 45
        if restaurant_name and restaurant_name in answer_norm:
            score += 55
        elif restaurant_name and restaurant_name in combined:
            score += 25
        if item_name_in_question:
            score += 45
        elif item_question_overlap >= 0.5:
            score += 35
        price = item.get("price")
        if price is not None:
            price_text = f"{float(price):.2f}".rstrip("0").rstrip(".")
            if price_text and price_text in answer_norm:
                score += 10
        # The model is told (RESTAURANT ORDERING RULE prompt) to echo the
        # raw Menu Item ID / Restaurant ID when it renders an ordering
        # table -- when it does, that's a strong extra signal this exact
        # row is the one being discussed, so it's rewarded as a BONUS
        # rather than required. Requiring it outright (as this used to)
        # meant "+Add" chips only ever appeared for that narrow
        # table-formatted answer, never for an ordinary conversational
        # reply like "Chicken Biryani is $12.99 at Spice House" -- which
        # is most of what diners actually ask, and was reported as
        # "no item to add to cart from chat".
        if menu_item_id and menu_item_id.lower() in answer_raw:
            score += 60
        if restaurant_id and restaurant_id.lower() in answer_raw:
            score += 30
        if score >= 70:
            scored.append((score, item))
    if not scored:
        return {}
    scored.sort(key=lambda pair: (-pair[0], str(pair[1].get("restaurant_name") or ""), str(pair[1].get("item_name") or "")))
    deduped: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for score, item in scored:
        key = (str(item.get("restaurant_id") or ""), _normalise_restaurant_text(str(item.get("item_name") or "")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append({**item, "action_score": round(score, 3)})
        if len(deduped) >= 8:
            break
    if len(deduped) != 1 and active_restaurant_ids and len(active_restaurant_ids) == 1:
        # Disambiguate using the restaurant the PRIOR turn already
        # settled on (see _active_restaurant_ids_from_history): narrow
        # the already-scored candidates down to that one restaurant and
        # see if that alone resolves the match to exactly one item.
        pinned_id = next(iter(active_restaurant_ids))
        pinned_seen: set[tuple[str, str]] = set()
        pinned_deduped: list[dict] = []
        for score, item in scored:
            if str(item.get("restaurant_id") or "").lower() != pinned_id:
                continue
            key = (str(item.get("restaurant_id") or ""), _normalise_restaurant_text(str(item.get("item_name") or "")))
            if key in pinned_seen:
                continue
            pinned_seen.add(key)
            pinned_deduped.append({**item, "action_score": round(score, 3)})
        if len(pinned_deduped) == 1:
            deduped = pinned_deduped
    return {
        "type": "restaurant_menu_actions",
        "source": "restaurant_db_answer_filter",
        "restaurant_menu_items": deduped,
    }


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
    aliases = {
        "goad": "goat",
        "good": "goat",
        "goatdum": "goat",
        "goadum": "goat",
        "mutton": "goat",
        "vejawada": "bezawada",
        "bezawara": "bezawada",
        "biriyani": "biryani",
    }
    for raw in raw_terms:
        term = raw.lower().strip("-'")
        term = aliases.get(term, term)
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
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
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


def _json_safe(value):
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
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
            **_video_source_fields(c),
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
        video_label = _video_source_label(c)
        video_suffix = f" | {video_label}" if video_label else ""
        parts.append(
            f"[Source {i+1}: \"{c.get('doc_name','')}\" | "
            f"chunk {(c.get('chunk_index') or 0)+1}/{c.get('chunk_total','?')} | "
            f"{icon} {match_type} | {score}{video_suffix}]\n{content}"
        )
    return "\n\n---\n\n".join(parts)


def _sanitise(chunks: list[dict], redact_pii: bool = False) -> list[dict]:
    return [
        {
            "source_number": index + 1,
            "document_id":  str(c.get("document_id") or ""),
            "doc_name":     c.get("doc_name"),
            "original_name": c.get("doc_name") or c.get("original_name"),
            "chunk_index":  c.get("chunk_index"),
            "chunk_total":  c.get("chunk_total"),
            **_video_source_fields(c),
            "similarity":   round(float(c.get("similarity") or 0), 4),
            "rerank_score": round(float(c.get("rerank_score") or 0), 4)
                            if c.get("rerank_score") is not None else None,
            "match_type":   c.get("match_type", "vector"),
            "preview":      redact_text((c.get("content") or "")[:500], redact_pii).text,
            "excerpt":      redact_text((c.get("content") or "")[:500], redact_pii).text,
        }
        for index, c in enumerate(chunks)
    ]


def _chunk_meta(chunk: dict) -> dict:
    meta = chunk.get("chunk_metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except json.JSONDecodeError:
            meta = {}
    return meta if isinstance(meta, dict) else {}


def _filter_candidates_by_evidence_ranges(
    candidates: list[dict], evidence_ranges: list[DocumentEvidenceRange],
) -> list[dict]:
    if not evidence_ranges:
        return candidates
    configured = {item.document_id: item.ranges for item in evidence_ranges}
    filtered = []
    for candidate in candidates:
        windows = configured.get(str(candidate.get("document_id")))
        if not windows:
            filtered.append(candidate)
            continue
        metadata = _chunk_meta(candidate)
        start = metadata.get("start_seconds")
        end = metadata.get("end_seconds")
        if start is None or end is None:
            continue
        if any(float(start) <= window.end_seconds and float(end) >= window.start_seconds for window in windows):
            filtered.append(candidate)
    return filtered


def _video_source_fields(chunk: dict) -> dict:
    meta = _chunk_meta(chunk)
    if meta.get("file_type") != "video" and not str(meta.get("chunk_type") or "").startswith("video_"):
        return {}
    return {
        "file_type": "video",
        "chunk_type": meta.get("chunk_type"),
        "start_seconds": meta.get("start_seconds"),
        "end_seconds": meta.get("end_seconds"),
        "start_time": meta.get("start_time"),
        "end_time": meta.get("end_time"),
        "thumbnail_path": meta.get("thumbnail_path") or meta.get("frame_path"),
    }


def _video_source_label(chunk: dict) -> str:
    fields = _video_source_fields(chunk)
    if not fields:
        return ""
    start = fields.get("start_time") or _fmt_seconds(fields.get("start_seconds"))
    end = fields.get("end_time") or _fmt_seconds(fields.get("end_seconds"))
    if start and end and start != end:
        return f"video {start}-{end}"
    if start:
        return f"video @ {start}"
    return "video"


def _fmt_seconds(value) -> str:
    try:
        total = int(float(value))
    except (TypeError, ValueError):
        return ""
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
