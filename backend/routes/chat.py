# routes/chat.py — 3-stage RAG: Hybrid Retrieval → Gemini Re-rank → Generate
from __future__ import annotations
import json, asyncio
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse

from auth.dependencies import CurrentUser
from database.connection import get_db, get_pool
from services.llm import embed_query, chat_stream, rag_system
from services.vectordb import find_similar, TOP_K, RERANK_FETCH_K
from services.usage import check_daily_limit, log_event
from services.reranker import rerank, RERANK_ENABLED
from services.language import primary_language, response_language_instruction

router = APIRouter()

# Fetch more candidates when re-ranking so the re-ranker has enough to work with
_FETCH_K = RERANK_FETCH_K if RERANK_ENABLED else TOP_K


class ChatRequest(BaseModel):
    question:     str
    document_ids: list[str]
    history:      list[dict] = []
    workspace_id: str | None = None


@router.post("/stream")
async def chat_stream_endpoint(
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

    async def generate():
        queue: asyncio.Queue = asyncio.Queue()

        async def on_token(t: str):
            await queue.put(("token", t))

        async def run():
            try:
                # ── Check daily query limit (fresh pool conn — db released by now) ──
                async with get_pool().acquire() as _conn:
                    await check_daily_limit(_conn, user_id, "query", "max_queries_day")

                # ── Stage 1: Embed query ──────────────────────────────────
                query_vec = await embed_query(req.question)

                # ── Stage 2: Hybrid retrieval (vector + BM25 + RRF) ──────
                #    Fetch RERANK_FETCH_K candidates — more than final TOP_K
                candidates = await find_similar(
                    query_embedding=query_vec,
                    query_text=req.question,   # enables BM25 + RRF fusion
                    user_id=user_id,
                    document_ids=req.document_ids,
                    limit=_FETCH_K,
                )

                # ── Stage 3: Gemini cross-encoder re-ranking ──────────────
                #    Gemini scores each (query, chunk) pair together —
                #    far more accurate than independent bi-encoder scores
                chunks = await rerank(
                    query=req.question,
                    chunks=candidates,
                    top_k=TOP_K,
                )

                # ── Stage 4: Build grounded context ──────────────────────
                context = _build_context(chunks)

                # ── Stage 5: Stream LLM answer ────────────────────────────
                messages = [
                    {"role": m["role"], "content": m["content"]}
                    for m in req.history[-12:]
                    if m.get("role") and m.get("content")
                ] + [{"role": "user", "content": req.question}]

                language_instruction = response_language_instruction(response_lang)
                await chat_stream(messages, rag_system(context, language_instruction), on_token)
                async with get_pool().acquire() as _conn:
                    await log_event(_conn, user_id, "query", metadata={"doc_count": len(req.document_ids), "chunks_used": len(chunks)})
                await queue.put(("done", _sanitise(chunks)))

            except HTTPException as exc:
                await queue.put(("error", exc.detail))
            except Exception as exc:
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
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _build_context(chunks: list[dict]) -> str:
    if not chunks:
        return "No relevant chunks found."
    parts = []
    for i, c in enumerate(chunks):
        match_type   = c.get("match_type", "vector")
        rerank_score = c.get("rerank_score")
        icon  = {"hybrid": "⚡", "keyword": "🔤", "vector": "🔍"}.get(match_type, "🔍")
        score = (f"rerank {rerank_score*100:.1f}%" if rerank_score is not None
                 else f"relevance {c.get('similarity', 0)*100:.1f}%")
        parts.append(
            f"[Source {i+1}: \"{c.get('doc_name','')}\" | "
            f"chunk {(c.get('chunk_index') or 0)+1}/{c.get('chunk_total','?')} | "
            f"{icon} {match_type} | {score}]\n{c.get('content','')}"
        )
    return "\n\n---\n\n".join(parts)


def _sanitise(chunks: list[dict]) -> list[dict]:
    return [
        {
            "doc_name":     c.get("doc_name"),
            "chunk_index":  c.get("chunk_index"),
            "chunk_total":  c.get("chunk_total"),
            "similarity":   round(float(c.get("similarity") or 0), 4),
            "rerank_score": round(float(c.get("rerank_score") or 0), 4)
                            if c.get("rerank_score") is not None else None,
            "match_type":   c.get("match_type", "vector"),
            "preview":      (c.get("content") or "")[:300],
        }
        for c in chunks
    ]
