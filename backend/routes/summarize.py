# routes/summarize.py
from __future__ import annotations
import json, asyncio, hashlib, os
from typing import Callable
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from auth.dependencies import CurrentUser
from database.connection import get_db, get_pool
from services.usage import check_and_log_daily_event
import services.storage as gcs
from services.llm import chat_stream, summarize_system, mini_summarize_system
from services.language import primary_language, response_language_instruction
from services.pii import redact_text

from fastapi import Request

router = APIRouter()

SUMMARY_PROMPTS = {
    "executive": "Write a 3-5 sentence executive summary focusing on main purpose, key findings, and conclusions.",
    "detailed":  "Write a comprehensive detailed summary covering all major topics, findings, data points, numbers, dates, and conclusions.",
    "bullets":   "Create a structured bullet-point summary grouped under bold headings. Each bullet should be a complete informative sentence.",
    "sections":  "Identify each major section or topic. Write a heading and 3-5 sentence summary for each section.",
    "custom":    "",
}

DIRECT_CHAR_LIMIT = 50_000
MAP_CHAR_LIMIT = max(8_000, int(os.getenv("SUMMARY_MAP_CHAR_LIMIT", "40000")))
SUMMARY_CONCURRENCY = max(1, int(os.getenv("SUMMARY_CONCURRENCY", "4")))
SUMMARY_RETRIES = max(1, int(os.getenv("SUMMARY_RETRIES", "3")))
SUMMARY_RETRY_BASE_SECONDS = max(0.0, float(os.getenv("SUMMARY_RETRY_BASE_SECONDS", "1.5")))
SUMMARY_MAX_LEVELS = max(2, int(os.getenv("SUMMARY_MAX_LEVELS", "8")))
SUMMARY_HEARTBEAT_SECONDS = max(5, int(os.getenv("SUMMARY_HEARTBEAT_SECONDS", "15")))


class SummarizeRequest(BaseModel):
    summary_type:  str       = "executive"
    custom_prompt: str       = ""
    chunk_indices: list[int] = []
    redact_pii:    bool      = False
    response_language: str | None = None


class MultiSummarizeRequest(BaseModel):
    document_ids:  list[str]
    summary_type:  str = "executive"
    custom_prompt: str = ""
    redact_pii:    bool = False
    response_language: str | None = None


# ── Single document ───────────────────────────────────────────────────────────
@router.post("/document/{doc_id}/stream")
async def summarize_document(
    request: Request,
    doc_id: str,
    body:   SummarizeRequest,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    row     = await _get_accessible_chunked(doc_id, str(current_user["id"]), db)
    user_id = str(current_user["id"])
    await check_and_log_daily_event(
        db,
        user_id,
        "summarize",
        "max_summaries_day",
        metadata={"doc_id": doc_id, "summary_type": body.summary_type},
    )

    async def generate():
        try:
            owner_id = str(row["user_id"])
            meta   = await gcs.download_json(gcs.metadata_path(owner_id, doc_id))
            chunks = meta["chunks"]
            if body.chunk_indices:
                chunks = [c for c in chunks if c["index"] in body.chunk_indices]
            if not chunks:
                yield _sse_error("No chunks found"); return

            texts = await _load_chunk_texts(chunks)
            cache_prefix = _summary_cache_prefix(
                owner_id=owner_id,
                doc_id=doc_id,
                document_version=row.get("updated_at"),
                summary_type=body.summary_type,
                custom_prompt=body.custom_prompt,
                response_language=body.response_language or row.get("doc_language") or "en",
                redact_pii=body.redact_pii,
                chunk_indices=body.chunk_indices,
            )
            async for event in _stream_summary(
                texts,
                body.summary_type,
                body.custom_prompt,
                row["original_name"],
                body.response_language or row.get("doc_language") or "en",
                body.redact_pii,
                cache_prefix=cache_prefix,
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
        """SELECT d.id, d.user_id, d.original_name, d.status, d.doc_language, d.updated_at
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
        body.document_ids, user_id,
    )
    found       = {str(r["id"]): r for r in rows}
    missing     = set(body.document_ids) - set(found)
    not_chunked = [str(r["id"]) for r in rows if r["status"] not in ("chunked","embedding","embedded")]

    if missing:
        raise HTTPException(403, f"Documents not found: {missing}")
    if not_chunked:
        raise HTTPException(400, f"Documents not yet chunked: {not_chunked}")
    await check_and_log_daily_event(
        db,
        user_id,
        "summarize",
        "max_summaries_day",
        quantity=len(body.document_ids),
        metadata={"doc_ids": body.document_ids},
    )

    async def generate():
        try:
            all_texts: list[str] = []
            for doc_id in body.document_ids:
                row   = found[doc_id]
                owner_id = str(row["user_id"])
                meta  = await gcs.download_json(gcs.metadata_path(owner_id, doc_id))
                texts = await _load_chunk_texts(meta["chunks"])
                all_texts.append(f"=== {row['original_name']} ===")
                all_texts.extend(texts)

            label = f"{len(body.document_ids)} documents"
            lang = body.response_language or primary_language([r["doc_language"] for r in rows])
            async for event in _stream_summary(all_texts, body.summary_type, body.custom_prompt, label, lang, body.redact_pii):
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
    response_lang: str = "en",
    redact_pii:    bool = False,
    cache_prefix: str | None = None,
):
    if summary_type == "custom" and not custom_prompt.strip():
        yield _sse_error("Provide a custom_prompt for custom summary type")
        return

    texts = [str(text).strip() for text in texts if str(text or "").strip()]
    if not texts:
        yield _sse_error("No text content found to summarize")
        return

    base_prompt = (
        redact_text(custom_prompt.strip(), redact_pii).text if summary_type == "custom"
        else SUMMARY_PROMPTS.get(summary_type, SUMMARY_PROMPTS["executive"])
    )

    total_chars = sum(len(text) + 2 for text in texts)
    # ── Direct path ───────────────────────────────────────────────────────────
    if total_chars <= DIRECT_CHAR_LIMIT:
        full_text = redact_text("\n\n".join(texts), redact_pii).text
        language_instruction = response_language_instruction(response_lang)
        system   = summarize_system(summary_type, label, base_prompt, language_instruction)
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

    # ── Hierarchical map-reduce path ─────────────────────────────────────────
    current = _group_texts_by_chars(texts, MAP_CHAR_LIMIT)
    level = 0
    while sum(len(item) + 2 for item in current) > DIRECT_CHAR_LIMIT:
        if level >= SUMMARY_MAX_LEVELS:
            raise RuntimeError("Summary hierarchy exceeded its configured depth")
        batches = _group_texts_by_chars(current, MAP_CHAR_LIMIT)
        total_batches = len(batches)
        yield _sse_meta({
            "stage": "map" if level == 0 else "reduce",
            "level": level + 1,
            "batch": 0,
            "of": total_batches,
            "total_characters": total_chars,
        })
        current = []
        async for progress, summaries in _summarize_level(
            batches=batches,
            level=level,
            label=label,
            response_lang=response_lang,
            redact_pii=redact_pii,
            cache_prefix=cache_prefix,
        ):
            current = summaries
            yield _sse_meta({
                "stage": "map" if level == 0 else "reduce",
                "level": level + 1,
                "batch": progress,
                "of": total_batches,
            })
        level += 1

    yield _sse_meta({"stage": "final", "level": level + 1, "batch": 0, "of": 1})
    combined = "\n\n---\n\n".join(
        f"[Section {i+1}]\n{summary}" for i, summary in enumerate(current)
    )
    language_instruction = response_language_instruction(response_lang)
    system   = summarize_system(summary_type, label, base_prompt, language_instruction)
    messages = [{"role": "user", "content": (
        f"Using these section summaries, produce a single high-quality {summary_type} summary:\n\n{combined}"
    )}]

    final_cache_path = f"{cache_prefix}/final.txt" if cache_prefix else None
    final = await _cached_or_generate(cache_path=final_cache_path, messages=messages, system=system)
    yield _sse_meta({"stage": "final", "level": level + 1, "batch": 1, "of": 1})
    for offset in range(0, len(final), 800):
        yield _sse_token(final[offset:offset + 800])
    yield _sse_done()


def _group_texts_by_chars(texts: list[str], max_chars: int) -> list[str]:
    """Pack text in order into bounded prompts, splitting oversized items."""
    groups: list[str] = []
    current: list[str] = []
    current_chars = 0
    for raw in texts:
        text = str(raw or "").strip()
        if not text:
            continue
        pieces = [text[offset:offset + max_chars] for offset in range(0, len(text), max_chars)]
        for piece in pieces:
            added = len(piece) + (2 if current else 0)
            if current and current_chars + added > max_chars:
                groups.append("\n\n".join(current))
                current = []
                current_chars = 0
            current.append(piece)
            current_chars += len(piece) + (2 if len(current) > 1 else 0)
    if current:
        groups.append("\n\n".join(current))
    return groups


async def _summarize_level(
    *, batches: list[str], level: int, label: str, response_lang: str,
    redact_pii: bool, cache_prefix: str | None,
):
    """Summarize one hierarchy level concurrently and yield progress snapshots."""
    semaphore = asyncio.Semaphore(SUMMARY_CONCURRENCY)
    summaries = [""] * len(batches)
    language_instruction = response_language_instruction(response_lang)
    system = mini_summarize_system(label, language_instruction)

    async def process(index: int, batch_text: str) -> tuple[int, str]:
        prompt = (
            "Summarise this source section thoroughly. Preserve key facts, figures, names, dates, "
            "conclusions, and source section boundaries:\n\n"
            if level == 0 else
            "Consolidate these partial summaries without losing facts, figures, names, dates, "
            "conclusions, or conflicting findings:\n\n"
        )
        messages = [{"role": "user", "content": prompt + redact_text(batch_text, redact_pii).text}]
        cache_path = f"{cache_prefix}/level-{level}/batch-{index:06d}.txt" if cache_prefix else None
        async with semaphore:
            result = await _cached_or_generate(cache_path=cache_path, messages=messages, system=system)
        return index, result

    pending = {asyncio.create_task(process(index, batch)) for index, batch in enumerate(batches)}
    completed = 0
    try:
        while pending:
            done, pending = await asyncio.wait(
                pending,
                timeout=SUMMARY_HEARTBEAT_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                yield completed, list(summaries)
                continue
            for task in done:
                index, result = await task
                summaries[index] = result
                completed += 1
            yield completed, list(summaries)
    except Exception:
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        raise


async def _cached_or_generate(*, cache_path: str | None, messages: list[dict], system: str) -> str:
    if cache_path:
        try:
            if await gcs.blob_metadata(cache_path):
                cached = (await gcs.download_text(cache_path)).strip()
                if cached:
                    return cached
        except Exception:
            # Cache availability must never prevent summarization.
            pass
    result = await _generate_text_with_retries(messages, system)
    if cache_path:
        try:
            await gcs.upload_text(cache_path, result)
        except Exception:
            pass
    return result


async def _generate_text_with_retries(messages: list[dict], system: str) -> str:
    last_error: Exception | None = None
    for attempt in range(1, SUMMARY_RETRIES + 1):
        try:
            result = (await _generate_text_once(messages, system)).strip()
            if not result:
                raise RuntimeError("The model returned an empty summary")
            return result
        except Exception as exc:
            last_error = exc
            if attempt < SUMMARY_RETRIES:
                await asyncio.sleep(SUMMARY_RETRY_BASE_SECONDS * (2 ** (attempt - 1)))
    raise RuntimeError(f"Summary generation failed after {SUMMARY_RETRIES} attempts: {last_error}") from last_error


async def _generate_text_once(messages: list[dict], system: str) -> str:
    q: asyncio.Queue = asyncio.Queue()

    async def collect(token: str):
        await q.put(token)

    async def run():
        try:
            await chat_stream(messages, system, collect)
        except Exception as exc:
            await q.put(exc)
        finally:
            await q.put(None)

    task = asyncio.create_task(run())
    tokens: list[str] = []
    try:
        while True:
            try:
                item = await asyncio.wait_for(q.get(), timeout=STALL_TIMEOUT_SECONDS)
            except asyncio.TimeoutError as exc:
                task.cancel()
                raise TimeoutError("Summary model call stalled") from exc
            if item is None:
                break
            if isinstance(item, Exception):
                raise item
            tokens.append(str(item))
        await task
        return "".join(tokens)
    finally:
        if not task.done():
            task.cancel()


def _summary_cache_prefix(
    *, owner_id: str, doc_id: str, document_version, summary_type: str,
    custom_prompt: str, response_language: str, redact_pii: bool,
    chunk_indices: list[int],
) -> str:
    signature = hashlib.sha256(json.dumps({
        "document_version": str(document_version or ""),
        "summary_type": summary_type,
        "custom_prompt": custom_prompt,
        "response_language": response_language,
        "redact_pii": redact_pii,
        "chunk_indices": chunk_indices,
        "pipeline": 2,
    }, sort_keys=True).encode()).hexdigest()[:24]
    return f"users/{owner_id}/documents/{doc_id}/summaries/{signature}"


# ── Streaming helper (yields SSE events) ─────────────────────────────────────
# Bounds the gap between model tokens rather than the complete hierarchy.
# Every completed map batch is cached, so a later retry resumes completed work.
STALL_TIMEOUT_SECONDS = 240


async def _run_chat(messages: list[dict], system: str):
    """Generate with retries, then emit bounded SSE token events."""
    try:
        result = await _generate_text_with_retries(messages, system)
    except Exception as exc:
        yield _sse_error(str(exc))
        return
    for offset in range(0, len(result), 800):
        yield _sse_token(result[offset:offset + 800])
    yield _sse_done()


# ── Non-streaming helper (collects all tokens) ────────────────────────────────
async def _await_chat(messages: list[dict], system: str, on_token: Callable):
    """Compatibility helper for batch operations and older callers."""
    await on_token(await _generate_text_with_retries(messages, system))

# ── Helpers ───────────────────────────────────────────────────────────────────
async def _load_chunk_texts(chunks: list[dict]) -> list[str]:
    concurrency = max(1, int(os.getenv("SUMMARY_CHUNK_DOWNLOAD_CONCURRENCY", "16")))
    semaphore = asyncio.Semaphore(concurrency)

    async def load(index: int, chunk: dict) -> tuple[int, str]:
        async with semaphore:
            return index, await gcs.download_text(chunk["gcs_path"])

    loaded = await asyncio.gather(*(load(index, chunk) for index, chunk in enumerate(chunks)))
    loaded.sort(key=lambda item: item[0])
    return [text for _index, text in loaded]


async def _get_accessible_chunked(doc_id: str, user_id: str, db) -> dict:
    row = await db.fetchrow(
        """SELECT d.* FROM documents d
           WHERE d.id=$1
             AND d.status != 'deleted'
             AND (
               d.user_id=$2
               OR EXISTS (
                 SELECT 1 FROM workspace_members wm
                 WHERE wm.workspace_id=d.workspace_id
                   AND wm.user_id=$2
               )
             )""",
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
