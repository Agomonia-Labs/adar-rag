# routes/compare.py
from __future__ import annotations
import os, re, logging
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
import json, httpx

from auth.dependencies import CurrentUser
from database.connection import get_db, get_pool

router = APIRouter()
log = logging.getLogger("docintel.compare")

GOOGLE_AI_KEY     = os.getenv("GOOGLE_AI_KEY", "")
GEMINI_CHAT_MODEL = os.getenv("GEMINI_CHAT_MODEL", "gemini-1.5-flash")
GEMINI_BASE       = "https://generativelanguage.googleapis.com/v1beta/models"
MAX_CHARS_PER_DOC = int(os.getenv("COMPARE_MAX_CHARS", "30000"))


class CompareRequest(BaseModel):
    document_id_1: str
    document_id_2: str


@router.post("/stream")
async def compare_documents(
    body: CompareRequest,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    user_id = str(current_user["id"])

    rows = await db.fetch(
        """SELECT id, original_name, status FROM documents
           WHERE id = ANY($1::uuid[]) AND user_id = $2 AND status != 'deleted'""",
        [body.document_id_1, body.document_id_2], user_id,
    )
    if len(rows) < 2:
        raise HTTPException(404, "One or both documents not found")

    docs = {str(r["id"]): dict(r) for r in rows}
    for did in [body.document_id_1, body.document_id_2]:
        if did not in docs:
            raise HTTPException(404, f"Document {did} not found")

    doc1 = docs[body.document_id_1]
    doc2 = docs[body.document_id_2]
    doc_id_1, doc_id_2 = body.document_id_1, body.document_id_2

    async def generate():
        pool = get_pool()
        try:
            yield _sse({"type": "status", "message": f"Loading {doc1['original_name']}..."})
            async with pool.acquire() as conn:
                text1 = await _load_from_db(conn, doc_id_1, user_id)
            if not text1:
                yield _sse({"type": "error", "error": f"No text found for {doc1['original_name']}. Ensure it is chunked."}); return

            yield _sse({"type": "status", "message": f"Loading {doc2['original_name']}..."})
            async with pool.acquire() as conn:
                text2 = await _load_from_db(conn, doc_id_2, user_id)
            if not text2:
                yield _sse({"type": "error", "error": f"No text found for {doc2['original_name']}. Ensure it is chunked."}); return

            yield _sse({"type": "status", "message": "Gemini is analysing differences..."})
            result, err = await _gemini_compare(doc1["original_name"], text1, doc2["original_name"], text2)

            if result:
                yield _sse({"type": "result", "data": result})
            else:
                yield _sse({"type": "error", "error": err or "Comparison failed"})
        except Exception as e:
            log.exception("Compare stream error")
            yield _sse({"type": "error", "error": str(e)})

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def _load_from_db(conn, document_id: str, user_id: str) -> str:
    rows = await conn.fetch(
        "SELECT content FROM document_chunks WHERE document_id=$1 AND user_id=$2 ORDER BY chunk_index",
        document_id, user_id,
    )
    parts, total = [], 0
    for r in rows:
        text = r["content"] or ""
        if total + len(text) > MAX_CHARS_PER_DOC:
            rem = MAX_CHARS_PER_DOC - total
            if rem > 100: parts.append(text[:rem] + "\n[truncated]")
            break
        parts.append(text); total += len(text)
    return "\n\n".join(parts)


async def _gemini_compare(doc1_name, doc1_text, doc2_name, doc2_text):
    """
    Uses a structured TEXT format with markers instead of JSON.
    Legal documents contain quotes/newlines that break JSON parsing.
    Markers like ===SECTION=== are unambiguous and always parseable.
    """
    prompt = f"""You are a legal and financial document comparison expert.

Compare Document 1 and Document 2 thoroughly, then output your analysis using
EXACTLY the marker format shown below. Do not use JSON. Do not deviate from the format.

DOCUMENT 1: {doc1_name}
{doc1_text}

DOCUMENT 2: {doc2_name}
{doc2_text}

Output format (use these exact markers):

===SUMMARY===
Write 2-3 sentences summarising the most important differences.

===SIMILARITY===
Write only a number between 0.0 and 1.0

===DOC1_UNIQUE===
- Each bullet is one point that only appears in Document 1
- Add as many bullets as needed

===DOC2_UNIQUE===
- Each bullet is one point that only appears in Document 2

===SECTIONS===
For each major topic in either document, write one block:

>>>TOPIC<<<
Short topic name (e.g. Payment Terms, Lease Duration, Security Deposit)
>>>DOC1<<<
What Document 1 says about this topic (or: Not present)
>>>DOC2<<<
What Document 2 says about this topic (or: Not present)
>>>DIFF<<<
One sentence describing exactly how they differ
>>>TYPE<<<
same OR modified OR added OR removed
>>>END<<<

Repeat the block above for every major topic. Begin output now:"""

    url = f"{GEMINI_BASE}/{GEMINI_CHAT_MODEL}:generateContent"
    payload = {
        "contents":         [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 8192, "temperature": 0.1},
    }

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(url, params={"key": GOOGLE_AI_KEY}, json=payload)
        if not resp.is_success:
            return None, f"Gemini error {resp.status_code}: {resp.text[:200]}"

        candidate = resp.json().get("candidates", [{}])[0]
        if candidate.get("finishReason") == "SAFETY":
            return None, "Gemini blocked for safety reasons"

        text = (candidate.get("content", {}).get("parts", [{}])[0].get("text", "").strip())
        if not text:
            return None, "Gemini returned empty response"

        log.info(f"Gemini raw (first 400): {text[:400]}")
        result = _parse_marker_format(text, doc1_name, doc2_name)
        log.info(f"Parsed: {len(result.get('sections',[]))} sections, sim={result.get('similarity_score')}")
        return result, None

    except Exception as e:
        log.exception("Gemini compare exception")
        return None, str(e)


def _parse_marker_format(text: str, doc1_name: str, doc2_name: str) -> dict:
    """Parse the marker-based format — fully robust, no JSON needed."""

    def between(marker_start, marker_end, src=text):
        s = src.find(marker_start)
        if s == -1: return ""
        s += len(marker_start)
        e = src.find(marker_end, s)
        return src[s:e].strip() if e != -1 else src[s:].strip()

    # Summary
    summary = between("===SUMMARY===", "===SIMILARITY===") or between("===SUMMARY===", "===DOC1_UNIQUE===")

    # Similarity score
    sim_raw = between("===SIMILARITY===", "===DOC1_UNIQUE===")
    try:
        similarity = float(re.search(r"[\d.]+", sim_raw).group())
        similarity = max(0.0, min(1.0, similarity))
    except Exception:
        similarity = 0.5

    # Doc unique points
    def parse_bullets(raw: str) -> list[str]:
        lines = [l.strip().lstrip("-").lstrip("*").strip() for l in raw.splitlines()]
        return [l for l in lines if l]

    doc1_raw    = between("===DOC1_UNIQUE===", "===DOC2_UNIQUE===")
    doc2_raw    = between("===DOC2_UNIQUE===", "===SECTIONS===")
    doc1_unique = parse_bullets(doc1_raw)
    doc2_unique = parse_bullets(doc2_raw)

    # Sections
    sections_raw = between("===SECTIONS===", "\x00")  # read to end
    if not sections_raw:
        sections_raw = text[text.find("===SECTIONS===") + len("===SECTIONS==="):]

    sections = []
    blocks = re.split(r">>>TOPIC<<<", sections_raw)
    for block in blocks:
        if not block.strip(): continue

        def field(tag, src=block):
            s = src.find(f">>>{tag}<<<")
            if s == -1: return ""
            s += len(f">>>{tag}<<<")
            # Find the next >>> marker or >>>END<<<
            e = src.find(">>>", s)
            return src[s:e].strip() if e != -1 else src[s:].strip()

        topic     = block.split(">>>")[0].strip()
        doc1_text = field("DOC1")
        doc2_text = field("DOC2")
        diff      = field("DIFF")
        type_raw  = field("TYPE").lower().strip()
        sec_type  = type_raw if type_raw in ("same","modified","added","removed") else "modified"

        if topic:
            sections.append({
                "topic":      topic,
                "doc1_text":  doc1_text or "Not present",
                "doc2_text":  doc2_text or "Not present",
                "difference": diff,
                "type":       sec_type,
            })

    return {
        "summary":          summary or f"Comparison of {doc1_name} and {doc2_name}.",
        "similarity_score": similarity,
        "doc1_unique":      doc1_unique,
        "doc2_unique":      doc2_unique,
        "sections":         sections,
    }


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"