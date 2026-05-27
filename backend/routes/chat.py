# routes/chat.py — 3-stage RAG: Hybrid Retrieval → Gemini Re-rank → Generate
from __future__ import annotations
import json, asyncio
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import StreamingResponse

from auth.dependencies import CurrentUser
from database.connection import get_db, get_pool
from services.llm import embed_query, chat_stream, rag_system
from services.vectordb import find_similar, TOP_K, RERANK_FETCH_K
from services.usage import check_daily_limit, log_event
from services.reranker import rerank, RERANK_ENABLED
from services.language import primary_language, response_language_instruction
from services.pii import redact_text
from services.tracing import start_trace, finish_trace, span, record_llm_event

router = APIRouter()

# Fetch more candidates when re-ranking so the re-ranker has enough to work with
_FETCH_K = RERANK_FETCH_K if RERANK_ENABLED else TOP_K


class ChatRequest(BaseModel):
    question:     str
    document_ids: list[str]
    history:      list[dict] = []
    workspace_id: str | None = None
    redact_pii:   bool = False


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
        """SELECT d.id, d.status, d.doc_language FROM documents d
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
                # ── Check daily query limit (fresh pool conn — db released by now) ──
                async with span("usage_limit", trace_id=trace_id, metadata={"event_type": "query"}):
                    async with get_pool().acquire() as _conn:
                        await check_daily_limit(_conn, user_id, "query", "max_queries_day")

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

                # ── Stage 5: Stream LLM answer ────────────────────────────
                messages = [
                    {"role": m["role"], "content": redact_text(m["content"], req.redact_pii).text}
                    for m in req.history[-12:]
                    if m.get("role") and m.get("content")
                ] + [{"role": "user", "content": question_for_model}]

                language_instruction = response_language_instruction(response_lang)
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
                async with span("save_usage", trace_id=trace_id, metadata={"doc_count": len(req.document_ids), "chunks_used": len(chunks)}):
                    async with get_pool().acquire() as _conn:
                        await log_event(_conn, user_id, "query", metadata={"doc_count": len(req.document_ids), "chunks_used": len(chunks)})
                await finish_trace(trace_id, "success")
                await queue.put(("done", _sanitise(chunks, redact_pii=req.redact_pii)))

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
            elif item[0] == "done":  yield f"data: {json.dumps({'type':'done','sources':item[1]})}\n\n"; break
            elif item[0] == "error": yield f"data: {json.dumps({'type':'error','error':item[1]})}\n\n"; break

        await task

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Trace-Id": trace_id},
    )


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
