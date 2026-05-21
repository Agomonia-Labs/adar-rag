# services/reranker.py — Gemini cross-encoder re-ranking
# Re-orders retrieved chunks by true query-chunk relevance before sending to LLM.
from __future__ import annotations
import os, json, re, logging
import httpx

log = logging.getLogger("docintel.reranker")

GOOGLE_AI_KEY     = os.getenv("GOOGLE_AI_KEY", "")
RERANK_ENABLED    = os.getenv("RERANK_ENABLED", "true").lower() != "false"
GEMINI_CHAT_MODEL = os.getenv("GEMINI_CHAT_MODEL", "gemini-2.5-flash")
GEMINI_BASE       = "https://generativelanguage.googleapis.com/v1beta/models"


async def rerank(
    query:  str,
    chunks: list[dict],
    top_k:  int = 6,
) -> list[dict]:
    """
    Re-rank retrieved chunks using Gemini as a cross-encoder scorer.

    Unlike bi-encoders (which score query and chunk independently),
    this approach scores each (query, chunk) pair together — giving
    Gemini full context to judge true relevance.

    Pipeline:
      Hybrid retrieval → 20 candidates → Gemini scores each → top 6 returned

    Falls back to original order if Gemini call fails.
    """
    if not RERANK_ENABLED or not chunks:
        return chunks[:top_k]

    if not GOOGLE_AI_KEY:
        log.warning("GOOGLE_AI_KEY not set — skipping re-rank, using retrieval order")
        return chunks[:top_k]

    result = await _gemini_rerank(query, chunks, top_k)
    if result:
        log.info(f"Re-ranked {len(chunks)} → {len(result)} chunks via Gemini")
        return result

    log.warning("Gemini re-rank failed — falling back to retrieval order")
    return chunks[:top_k]


async def _gemini_rerank(
    query:  str,
    chunks: list[dict],
    top_k:  int,
) -> list[dict]:
    """
    Ask Gemini to score each (query, chunk) pair for relevance.
    Returns chunks sorted by score descending, limited to top_k.
    """
    n = len(chunks)
    passages = "\n\n".join(
        f"[{i + 1}] {c.get('content', '')[:500]}"
        for i, c in enumerate(chunks)
    )

    # Explicit single-line format discourages markdown fences and newlines
    prompt = (
        f"You are a relevance scoring system.\n\n"
        f"QUERY: {query}\n\n"
        f"PASSAGES:\n{passages}\n\n"
        f"Score each passage 0.0 (irrelevant) to 1.0 (perfect answer).\n"
        f"Rules:\n"
        f"- Return ONLY a single-line JSON array of exactly {n} numbers\n"
        f"- No markdown, no code fences, no explanation\n"
        f"- Example for {n} items: [{', '.join(['0.9' if i == 0 else '0.3' for i in range(n)])}]\n\n"
        f"Scores:"
    )

    url     = f"{GEMINI_BASE}/{GEMINI_CHAT_MODEL}:generateContent"
    payload = {
        "contents":         [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": 1024,   # enough for 20+ scores even with newlines
            "temperature":     0.0,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=25) as client:
            resp = await client.post(url, params={"key": GOOGLE_AI_KEY}, json=payload)
            if not resp.is_success:
                log.error(f"Gemini rerank {resp.status_code}: {resp.text[:200]}")
                return []

            text = (
                resp.json()
                    .get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [{}])[0]
                    .get("text", "")
                    .strip()
            )

        # Strip markdown code fences if Gemini ignores the instruction
        clean = re.sub(r"```[a-z]*\n?", "", text).strip()

        # Attempt 1: parse complete JSON array
        scores = None
        m = re.search(r"\[([\d\s.,\n]+)\]", clean, re.DOTALL)
        if m:
            try:
                scores = json.loads("[" + m.group(1) + "]")
            except json.JSONDecodeError:
                pass

        # Attempt 2: extract all valid floats (handles truncated output)
        if scores is None:
            nums = re.findall(r"\d+\.\d+|(?<![\d.])\d(?![\d.])", clean)
            candidates = [float(x) for x in nums if 0.0 <= float(x) <= 1.0]
            if candidates:
                scores = candidates
                log.warning(
                    f"Gemini rerank used fallback number extraction "
                    f"({len(scores)} scores from truncated output)"
                )

        if not scores:
            log.error(f"Gemini rerank: cannot parse scores from: {text[:300]}")
            return []

        # Pad with 0.0 if truncated, trim if too many
        if len(scores) < n:
            log.warning(f"Got {len(scores)} scores for {n} chunks — padding remainder with 0.0")
            scores = scores + [0.0] * (n - len(scores))
        scores = scores[:n]

        # Sort by score descending, return top_k
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        return [
            {
                **chunks[idx],
                "rerank_score": round(float(score), 4),
                "similarity":   round(float(score), 4),
            }
            for idx, score in ranked[:top_k]
        ]

    except Exception as exc:
        log.error(f"Gemini rerank exception: {exc}")
        return []