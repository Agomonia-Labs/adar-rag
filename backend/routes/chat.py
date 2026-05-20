# routes/chat.py
from __future__ import annotations
import json, asyncio
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse

from auth.dependencies import CurrentUser, get_current_user
from database.connection import get_db
from services.llm import embed_query, chat_stream
from services.vectordb import find_similar

router = APIRouter()


class ChatRequest(BaseModel):
    question:     str
    document_ids: list[str]   # user must select which embedded docs to search
    history:      list[dict] = []


@router.post("/stream")
async def chat_stream_endpoint(req: ChatRequest, current_user: CurrentUser, db=Depends(get_db)):
    if not req.question.strip():
        raise HTTPException(400, "question must not be empty")
    if not req.document_ids:
        raise HTTPException(400, "Select at least one embedded document to query")

    # Verify the user owns all requested documents and they are embedded
    user_id = str(current_user["id"])
    rows = await db.fetch(
        """
        SELECT id, status FROM documents
        WHERE id = ANY($1::uuid[]) AND user_id = $2 AND status != 'deleted'
        """,
        req.document_ids, user_id,
    )

    found_ids    = {str(r["id"]) for r in rows}
    not_found    = set(req.document_ids) - found_ids
    not_embedded = {str(r["id"]) for r in rows if r["status"] != "embedded"}

    if not_found:
        raise HTTPException(403, f"Documents not found or not yours: {not_found}")
    if not_embedded:
        raise HTTPException(400, f"Documents not yet embedded: {not_embedded}")

    async def generate():
        queue: asyncio.Queue = asyncio.Queue()

        async def on_token(text: str):
            await queue.put(("token", text))

        async def run():
            try:
                # 1. Embed the question
                query_vec = await embed_query(req.question)

                # 2. User-scoped vector search
                chunks = await find_similar(
                    query_embedding=query_vec,
                    user_id=user_id,
                    document_ids=req.document_ids,
                )

                # 3. Build grounded context
                context = _build_context(chunks)

                # 4. Stream answer
                messages = [
                    {"role": m["role"], "content": m["content"]}
                    for m in req.history[-12:] if m.get("role") and m.get("content")
                ] + [{"role": "user", "content": req.question}]

                await chat_stream(messages, context, on_token)

                sources = _sanitise(chunks)
                await queue.put(("done", sources))
            except Exception as exc:
                await queue.put(("error", str(exc)))

        task = asyncio.create_task(run())

        while True:
            item = await queue.get()
            if item[0] == "token":
                yield f"data: {json.dumps({'type':'token','text':item[1]})}\n\n"
            elif item[0] == "done":
                yield f"data: {json.dumps({'type':'done','sources':item[1]})}\n\n"
                break
            elif item[0] == "error":
                yield f"data: {json.dumps({'type':'error','error':item[1]})}\n\n"
                break

        await task

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _build_context(chunks: list[dict]) -> str:
    if not chunks:
        return "No relevant chunks found."
    return "\n\n---\n\n".join(
        f"[Source {i+1}: \"{c.get('doc_name','')}\" | "
        f"chunk {(c.get('chunk_index') or 0)+1}/{c.get('chunk_total','?')} | "
        f"similarity {c.get('similarity',0)*100:.1f}%]\n{c.get('content','')}"
        for i, c in enumerate(chunks)
    )


def _sanitise(chunks: list[dict]) -> list[dict]:
    return [
        {
            "doc_name":    c.get("doc_name"),
            "chunk_index": c.get("chunk_index"),
            "chunk_total": c.get("chunk_total"),
            "similarity":  round(float(c.get("similarity") or 0), 4),
            "preview":     (c.get("content") or "")[:300],
        }
        for c in chunks
    ]
