# services/llm.py  — OpenAI / Gemini via REST API
from __future__ import annotations
import os, asyncio, base64, json
from typing import Callable, Awaitable

import httpx

LLM_PROVIDER  = os.getenv("LLM_PROVIDER", "openai").lower()
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1536"))

_EXTRACT_PROMPT = (
    "Extract ALL content from this document with complete fidelity. "
    "Include every piece of text (printed AND handwritten), all tables with values, "
    "charts, diagrams, captions. Do NOT summarise — transcribe completely."
)


# ══════════════════════════════════════════════════════════════════
#  OpenAI
# ══════════════════════════════════════════════════════════════════
if LLM_PROVIDER == "openai":
    from openai import AsyncOpenAI
    _c           = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    _EMBED_MODEL = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
    _CHAT_MODEL  = os.getenv("OPENAI_CHAT_MODEL",  "gpt-4o-mini")

    async def embed(text: str) -> list[float]:
        r = await _c.embeddings.create(model=_EMBED_MODEL, input=text[:8000])
        return r.data[0].embedding

    async def embed_query(text: str) -> list[float]:
        return await embed(text)

    async def vision_extract(file_path: str, media_type: str) -> str:
        with open(file_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        r = await _c.chat.completions.create(
            model="gpt-4o-mini", max_tokens=4000,
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{b64}"}},
                {"type": "text",      "text": _EXTRACT_PROMPT},
            ]}],
        )
        return r.choices[0].message.content or ""

    async def chat_stream(
        messages:      list[dict],
        system_prompt: str,
        on_token:      Callable[[str], Awaitable[None]],
    ) -> None:
        """Stream a chat response. system_prompt is passed directly as the system message."""
        stream = await _c.chat.completions.create(
            model=_CHAT_MODEL, max_tokens=4000, stream=True,
            messages=[{"role": "system", "content": system_prompt}, *messages],
        )
        async for chunk in stream:
            d = chunk.choices[0].delta.content
            if d:
                await on_token(d)


# ══════════════════════════════════════════════════════════════════
#  Gemini — pure REST API
# ══════════════════════════════════════════════════════════════════
elif LLM_PROVIDER == "gemini":

    _GOOGLE_KEY      = os.getenv("GOOGLE_AI_KEY", "")
    _EMBED_MODEL     = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-2")
    _CHAT_MODEL      = os.getenv("GEMINI_CHAT_MODEL",  "gemini-2.5-flash")
    _EMBED_MODEL_ID  = _EMBED_MODEL.removeprefix("models/")
    _CHAT_MODEL_ID   = _CHAT_MODEL.removeprefix("models/")
    # v1 for embeddings (stable), v1beta for chat (supports systemInstruction)
    _GEMINI_EMBED_BASE = "https://generativelanguage.googleapis.com/v1/models"
    _GEMINI_CHAT_BASE  = "https://generativelanguage.googleapis.com/v1beta/models"

    async def _embed_rest(text: str, task_type: str) -> list[float]:
        url = f"{_GEMINI_EMBED_BASE}/{_EMBED_MODEL_ID}:embedContent"
        payload = {
            "model":                f"models/{_EMBED_MODEL_ID}",
            "content":              {"parts": [{"text": text[:8000]}]},
            "taskType":             task_type,
            "outputDimensionality": EMBEDDING_DIM,
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, params={"key": _GOOGLE_KEY}, json=payload)
            if not resp.is_success:
                raise RuntimeError(f"Gemini embed error {resp.status_code}: {resp.text}")
            return resp.json()["embedding"]["values"]

    async def embed(text: str) -> list[float]:
        return await _embed_rest(text, "RETRIEVAL_DOCUMENT")

    async def embed_query(text: str) -> list[float]:
        return await _embed_rest(text, "RETRIEVAL_QUERY")

    async def vision_extract(file_path: str, media_type: str) -> str:
        with open(file_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        url = f"{_GEMINI_CHAT_BASE}/{_CHAT_MODEL_ID}:generateContent"
        payload = {
            "contents": [{"parts": [
                {"inline_data": {"mime_type": media_type, "data": b64}},
                {"text": _EXTRACT_PROMPT},
            ]}]
        }
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, params={"key": _GOOGLE_KEY}, json=payload)
            if not resp.is_success:
                raise RuntimeError(f"Gemini vision error {resp.status_code}: {resp.text}")
            return resp.json()["candidates"][0]["content"]["parts"][0].get("text", "")

    async def chat_stream(
        messages:      list[dict],
        system_prompt: str,
        on_token:      Callable[[str], Awaitable[None]],
    ) -> None:
        """
        Stream a chat response via Gemini REST API.
        system_prompt is passed directly as systemInstruction — NOT wrapped in _rag_system().
        This means callers (chat.py, summarize.py) are responsible for building the right prompt.
        """
        url = f"{_GEMINI_CHAT_BASE}/{_CHAT_MODEL_ID}:streamGenerateContent"

        contents = []
        for m in messages:
            role = "user" if m["role"] == "user" else "model"
            contents.append({"role": role, "parts": [{"text": m["content"]}]})

        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents":          contents,
            "generationConfig":  {
                "maxOutputTokens": 4000,
                "temperature":     0.2,
            },
        }

        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream(
                "POST", url,
                params={"key": _GOOGLE_KEY, "alt": "sse"},
                json=payload,
                headers={"Content-Type": "application/json"},
            ) as response:
                if not response.is_success:
                    body = await response.aread()
                    raise RuntimeError(
                        f"Gemini chat error {response.status_code}: {body.decode()[:300]}"
                    )
                buffer = ""
                async for raw in response.aiter_text():
                    buffer += raw
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        data_str = line[5:].strip()
                        if not data_str or data_str == "[DONE]":
                            continue
                        try:
                            chunk = json.loads(data_str)
                            for cand in chunk.get("candidates", []):
                                for part in cand.get("content", {}).get("parts", []):
                                    text = part.get("text", "")
                                    if text:
                                        await on_token(text)
                        except (json.JSONDecodeError, KeyError):
                            continue

else:
    raise ValueError(f"Unknown LLM_PROVIDER={LLM_PROVIDER!r}. Set openai or gemini in .env")


# ── System prompts ────────────────────────────────────────────────────────────

def rag_system(context: str, language_instruction: str = "") -> str:
    """System prompt for RAG chat — grounds answers in retrieved chunks."""
    language_rule = (
        f"LANGUAGE RULE:\n{language_instruction}\n\n"
        if language_instruction else ""
    )
    return (
        "You are DocIntel, a precise document intelligence assistant.\n\n"
        f"{language_rule}"
        "ANSWER RULES:\n"
        "1. Answer ONLY from the retrieved chunks below.\n"
        "2. Cite sources inline with [Source N].\n"
        "3. If the answer is absent say: \"The provided documents don't contain this information.\"\n"
        "4. Never invent or extrapolate.\n\n"
        "FORMATTING RULES — apply these in EVERY response:\n"
        "5. IMPORTANCE COLOURS — use these to guide the reader's eye:\n"
        "   **bold text** = highest importance: key findings, critical numbers, main conclusions, decisive facts\n"
        "   *italic text* = context or qualification: background info, caveats, supporting details\n"
        "   Regular text = standard information\n"
        "   > blockquote  = a single key takeaway, warning, or notable insight\n"
        "6. TABLES — use markdown tables for any data with rows and columns:\n"
        "   | Col | Col |\n   |-----|-----|\n   | val | val |\n"
        "7. STRUCTURE — use ## headings for multi-section answers, - bullets for lists.\n"
        "8. NUMBERS — always write metric values explicitly: $2.4M, 17%, Q3 2024.\n"
        "   These are auto-highlighted green in the UI — use them precisely.\n"
        "9. Inline code (`value`) for IDs, codes, or technical strings.\n\n"
        "EXAMPLE of good formatting:\n"
        "Revenue grew to **$2.8M** in Q3, a **17% increase** year-over-year [Source 1].\n"
        "*This was driven primarily by enterprise account expansion.*\n"
        "> Key takeaway: margins improved to **28%**, the highest in four quarters.\n\n"
        f"RETRIEVED CHUNKS:\n{context}"
    )


def summarize_system(
    summary_type: str,
    label: str,
    base_prompt: str,
    language_instruction: str = "",
) -> str:
    """System prompt for summarization — focused on quality output."""

    common_rules = (
        "RULES:\n"
        "1. Use ONLY the content provided — never add external knowledge.\n"
        "2. Preserve important specifics: numbers, dates, names, percentages, amounts.\n"
        "3. Write clearly and professionally.\n"
        "4. Be thorough — do not skip important information.\n"
        "5. Do NOT say 'the document says' or 'according to the text' — state content directly."
    )

    if summary_type == "executive":
        task = (
            "Write a concise executive summary of 3-5 sentences.\n"
            "Structure: (1) What this document is about, (2) Key findings or main points, "
            "(3) Most important conclusions or recommendations.\n"
            "Use flowing prose — no bullet points."
        )

    elif summary_type == "detailed":
        task = (
            "Write a comprehensive, detailed summary that covers everything important.\n"
            "- Use ## headings for each major topic or theme\n"
            "- Under each heading write detailed paragraphs covering all key content\n"
            "- Include every significant fact, figure, date, name, and conclusion\n"
            "- Be thorough: this summary should allow someone to fully understand the document without reading it\n"
            "- Minimum 500 words"
        )

    elif summary_type == "bullets":
        task = (
            "Create a structured bullet-point summary.\n"
            "Format:\n"
            "**[Topic Heading]**\n"
            "• [Complete sentence with key information]\n"
            "• [Complete sentence with key information]\n\n"
            "**[Next Topic Heading]**\n"
            "• ...\n\n"
            "Rules:\n"
            "- Create a heading for every major topic\n"
            "- Each bullet must be a complete, standalone informative sentence\n"
            "- Include all key facts, figures, dates, and conclusions\n"
            "- Do not use vague bullets like 'discusses X' — state the actual content"
        )

    elif summary_type == "sections":
        task = (
            "Create a detailed section-by-section breakdown of this document.\n\n"
            "STEP 1: Carefully read the entire content and identify every distinct section, "
            "topic, or theme. Look for:\n"
            "  - Explicit headings or titles in the text\n"
            "  - Numbered sections (1., 2., Section A, etc.)\n"
            "  - Clear topic shifts or new subjects\n\n"
            "STEP 2: For EACH section you identify, output:\n\n"
            "## [Exact section title from document, OR a descriptive topic name]\n"
            "[Write 4-6 sentences covering: what this section is about, "
            "the key information presented, important data/figures/dates mentioned, "
            "and the main conclusions or outcomes of this section]\n\n"
            "CRITICAL RULES:\n"
            "- Every section of the document MUST appear — do not skip anything\n"
            "- Use the document's own headings wherever they exist\n"
            "- Each section summary must be specific and informative — not vague\n"
            "- Include actual numbers, names, dates from each section\n"
            "- If the document has no clear sections, create logical topic-based headings\n"
            "- Minimum 6 sections for a substantial document"
        )

    elif summary_type == "custom":
        task = base_prompt

    else:
        task = base_prompt

    return (
        f"You are an expert document analyst. You are summarising: \"{label}\"\n\n"
        + (f"LANGUAGE RULE:\n{language_instruction}\n\n" if language_instruction else "")
        + f"YOUR TASK:\n{task}\n\n"
        f"{common_rules}"
    )


def mini_summarize_system(label: str, language_instruction: str = "") -> str:
    """System prompt for map-reduce batch summarisation."""
    return (
        f"You are summarising a section of: \"{label}\"\n\n"
        + (f"LANGUAGE RULE:\n{language_instruction}\n\n" if language_instruction else "")
        + "Write a thorough summary of this section. "
        "Preserve all key facts, figures, names, dates, and conclusions. "
        "Be complete — nothing important should be omitted."
    )
