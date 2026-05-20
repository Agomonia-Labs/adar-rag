# routes/summarize.py
from __future__ import annotations
import json, asyncio
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from auth.dependencies import CurrentUser
from database.connection import get_db
import services.storage as gcs
from services.llm import chat_stream, summarize_system, mini_summarize_system

router = APIRouter()

SUMMARY_PROMPTS = {
    "executive": "Write a 3-5 sentence executive summary focusing on main purpose, key findings, and conclusions.",
    "detailed":  "Write a comprehensive detailed summary covering all major topics, findings, data points, numbers, dates, and conclusions.",
    "bullets":   "Create a structured bullet-point summary grouped under bold headings. Each bullet should be a complete informative sentence.",
    "sections":  "Identify each major section or topic. Write a heading and 3-5 sentence summary for each section.",
    "custom":    "",
}

DIRECT_CHAR_LIMIT = 50_000
BATCH_SIZE        = 6


class SummarizeRequest(BaseModel):
    summary_type:  str       = "executive"
    custom_prompt: str       = ""
    chunk_indices: list[int] = []


class MultiSummarizeRequest(BaseModel):
    document_ids:  list[str]
    summary_type:  str = "executive"
    custom_prompt: str = ""


# ── Single document ───────────────────────────────────────────────────────────
@router.post("/document/{doc_id}/stream")
async def summarize_document(
    doc_id: str,
    body:   SummarizeRequest,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    row     = await _get_owned_chunked(doc_id, str(current_user["id"]), db)
    user_id = str(current_user["id"])

    async def generate():
        try:
            meta   = await gcs.download_json(gcs.metadata_path(user_id, doc_id))
            chunks = meta["chunks"]
            if body.chunk_indices:
                chunks = [c for c in chunks if c["index"] in body.chunk_indices]
            if not chunks:
                yield _sse_error("No chunks found"); return

            texts = await _load_chunk_texts(chunks)
            async for event in _stream_summary(
                texts, body.summary_type, body.custom_prompt, row["original_name"]
            ):
                yield event
        except Exception as e:
            yield _sse_error(str(e))

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Multiple documents ────────────────────────────────────────────────────────
@router.post("/documents/stream")
async def summarize_multiple_documents(
    body: MultiSummarizeRequest,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    if not body.document_ids:
        raise HTTPException(400, "Provide at least one document_id")

    user_id = str(current_user["id"])
    rows = await db.fetch(
        """SELECT id, original_name, status FROM documents
           WHERE id = ANY($1::uuid[]) AND user_id = $2 AND status != 'deleted'""",
        body.document_ids, user_id,
    )
    found       = {str(r["id"]): r for r in rows}
    missing     = set(body.document_ids) - set(found)
    not_chunked = [str(r["id"]) for r in rows if r["status"] not in ("chunked","embedding","embedded")]

    if missing:
        raise HTTPException(403, f"Documents not found: {missing}")
    if not_chunked:
        raise HTTPException(400, f"Documents not yet chunked: {not_chunked}")

    async def generate():
        try:
            all_texts: list[str] = []
            for doc_id in body.document_ids:
                row   = found[doc_id]
                meta  = await gcs.download_json(gcs.metadata_path(user_id, doc_id))
                texts = await _load_chunk_texts(meta["chunks"])
                all_texts.append(f"=== {row['original_name']} ===\n" + "\n\n".join(texts))

            label = f"{len(body.document_ids)} documents"
            async for event in _stream_summary(all_texts, body.summary_type, body.custom_prompt, label):
                yield event
        except Exception as e:
            yield _sse_error(str(e))

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Core streaming summary ────────────────────────────────────────────────────
async def _stream_summary(
    texts:         list[str],
    summary_type:  str,
    custom_prompt: str,
    label:         str,
):
    full_text = "\n\n".join(texts)

    if summary_type == "custom" and not custom_prompt.strip():
        yield _sse_error("Provide a custom_prompt for custom summary type")
        return

    base_prompt = (
        custom_prompt.strip() if summary_type == "custom"
        else SUMMARY_PROMPTS.get(summary_type, SUMMARY_PROMPTS["executive"])
    )

    # ── Direct path ───────────────────────────────────────────────────────────
    if len(full_text) <= DIRECT_CHAR_LIMIT:
        system   = summarize_system(summary_type, label, base_prompt)
        # For sections, give the model a two-step instruction in the user message
        if summary_type == "sections":
            user_msg = (
                f"Here is the complete content of \"{label}\".\n"
                "Carefully read it, identify all sections/topics, then produce a detailed section-by-section summary:\n\n"
                f"{full_text}"
            )
        else:
            user_msg = f"Please summarise the following content of \"{label}\":\n\n{full_text}"
        messages = [{"role": "user", "content": user_msg}]

        async for event in _run_chat(messages, system):
            yield event
        return

    # ── Map-reduce path ───────────────────────────────────────────────────────
    total_batches = (len(texts) + BATCH_SIZE - 1) // BATCH_SIZE
    yield _sse_meta({"stage": "map", "total_batches": total_batches})

    batch_summaries: list[str] = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch     = texts[i : i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        yield _sse_meta({"stage": "map", "batch": batch_num, "of": total_batches})

        batch_text = "\n\n".join(batch)
        system     = mini_summarize_system(label)
        messages   = [{"role": "user", "content": f"Summarise this section thoroughly:\n\n{batch_text}"}]

        tokens: list[str] = []
        async def collect(t: str, _toks=tokens): _toks.append(t)
        await _await_chat(messages, system, collect)
        batch_summaries.append("".join(tokens))

    # Reduce
    yield _sse_meta({"stage": "reduce"})
    combined = "\n\n---\n\n".join(
        f"[Section {i+1}]\n{s}" for i, s in enumerate(batch_summaries)
    )
    system   = summarize_system(summary_type, label, base_prompt)
    messages = [{"role": "user", "content": (
        f"Using these section summaries, produce a single high-quality {summary_type} summary:\n\n{combined}"
    )}]

    async for event in _run_chat(messages, system):
        yield event


# ── Streaming helper (yields SSE events) ─────────────────────────────────────
async def _run_chat(messages: list[dict], system: str):
    """Run chat_stream and yield SSE token/done/error events."""
    q: asyncio.Queue = asyncio.Queue()

    async def on_token(t: str): await q.put(t)

    async def run():
        try:
            await chat_stream(messages, system, on_token)
        except Exception as e:
            await q.put(Exception(e))
        finally:
            await q.put(None)   # sentinel

    task = asyncio.create_task(run())
    while True:
        try:
            item = await asyncio.wait_for(q.get(), timeout=90)
        except asyncio.TimeoutError:
            yield _sse_error("Summary timed out — document may be too large")
            task.cancel()
            return

        if item is None:
            break
        if isinstance(item, Exception):
            yield _sse_error(str(item))
            return
        yield _sse_token(item)

    await task
    yield _sse_done()


# ── Non-streaming helper (collects all tokens) ────────────────────────────────
async def _await_chat(messages: list[dict], system: str, on_token: Callable):
    """Run chat_stream to completion without streaming (used in map phase)."""
    q: asyncio.Queue = asyncio.Queue()

    async def collect(t: str): await q.put(t)

    async def run():
        try:
            await chat_stream(messages, system, collect)
        finally:
            await q.put(None)

    task = asyncio.create_task(run())
    while True:
        try:
            item = await asyncio.wait_for(q.get(), timeout=90)
        except asyncio.TimeoutError:
            task.cancel()
            return
        if item is None:
            break
        await on_token(item)
    await task


# Fix missing import
from typing import Callable

# ── Helpers ───────────────────────────────────────────────────────────────────
async def _load_chunk_texts(chunks: list[dict]) -> list[str]:
    return [await gcs.download_text(c["gcs_path"]) for c in chunks]


async def _get_owned_chunked(doc_id: str, user_id: str, db) -> dict:
    row = await db.fetchrow(
        "SELECT * FROM documents WHERE id=$1 AND user_id=$2 AND status != 'deleted'",
        doc_id, user_id,
    )
    if not row:
        raise HTTPException(404, "Document not found")
    if row["status"] not in ("chunked","embedding","embedded"):
        raise HTTPException(400, f"Document must be chunked first (status: {row['status']})")
    return dict(row)


def _sse_token(text: str) -> str:
    return f"data: {json.dumps({'type':'token','text':text})}\n\n"

def _sse_done() -> str:
    return f"data: {json.dumps({'type':'done'})}\n\n"

def _sse_error(msg: str) -> str:
    return f"data: {json.dumps({'type':'error','error':msg})}\n\n"

def _sse_meta(data: dict) -> str:
    return f"data: {json.dumps({'type':'meta',**data})}\n\n"