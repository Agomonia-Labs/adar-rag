# services/llm.py  — OpenAI / Gemini behind one interface
from __future__ import annotations
import os, asyncio, base64
from typing import Callable, Awaitable

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai").lower()

_EXTRACT_PROMPT = (
    "Extract ALL content from this document with complete fidelity. "
    "Include every piece of text (printed AND handwritten), all tables with values, "
    "charts, diagrams, captions. Do NOT summarise — transcribe completely."
)

# ══════════════════════════════════════════════════════
#  OpenAI
# ══════════════════════════════════════════════════════
if LLM_PROVIDER == "openai":
    from openai import AsyncOpenAI
    _c          = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    _EMBED      = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
    _CHAT       = os.getenv("OPENAI_CHAT_MODEL",  "gpt-4o-mini")

    async def embed(text: str) -> list[float]:
        r = await _c.embeddings.create(model=_EMBED, input=text[:8000])
        return r.data[0].embedding

    async def embed_query(text: str) -> list[float]:
        return await embed(text)

    async def vision_extract(file_path: str, media_type: str) -> str:
        with open(file_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        r = await _c.chat.completions.create(
            model="gpt-4o-mini", max_tokens=4000,
            messages=[{"role":"user","content":[
                {"type":"image_url","image_url":{"url":f"data:{media_type};base64,{b64}"}},
                {"type":"text","text":_EXTRACT_PROMPT},
            ]}],
        )
        return r.choices[0].message.content or ""

    async def chat_stream(messages: list[dict], context: str, on_token: Callable[[str], Awaitable[None]]) -> None:
        system = _rag_system(context)
        stream = await _c.chat.completions.create(
            model=_CHAT, max_tokens=1400, stream=True,
            messages=[{"role":"system","content":system}, *messages],
        )
        async for chunk in stream:
            d = chunk.choices[0].delta.content
            if d: await on_token(d)

# ══════════════════════════════════════════════════════
#  Gemini
# ══════════════════════════════════════════════════════
elif LLM_PROVIDER == "gemini":
    import google.generativeai as genai
    import PIL.Image
    genai.configure(api_key=os.getenv("GOOGLE_AI_KEY"))
    _EMBED = os.getenv("GEMINI_EMBED_MODEL", "models/text-embedding-004")
    _CHAT  = os.getenv("GEMINI_CHAT_MODEL",  "gemini-1.5-flash")

    async def embed(text: str) -> list[float]:
        r = await asyncio.to_thread(genai.embed_content, model=_EMBED, content=text[:8000], task_type="retrieval_document")
        return r["embedding"]

    async def embed_query(text: str) -> list[float]:
        r = await asyncio.to_thread(genai.embed_content, model=_EMBED, content=text[:8000], task_type="retrieval_query")
        return r["embedding"]

    async def vision_extract(file_path: str, media_type: str) -> str:
        m   = genai.GenerativeModel(_CHAT)
        img = PIL.Image.open(file_path)
        r   = await m.generate_content_async([_EXTRACT_PROMPT, img])
        return r.text or ""

    async def chat_stream(messages: list[dict], context: str, on_token: Callable[[str], Awaitable[None]]) -> None:
        system = _rag_system(context)
        m = genai.GenerativeModel(_CHAT, system_instruction=system)
        gm = [{"role":"user" if x["role"]=="user" else "model","parts":[x["content"]]} for x in messages]
        r  = await m.generate_content_async(gm, stream=True)
        async for chunk in r:
            if chunk.text: await on_token(chunk.text)

else:
    raise ValueError(f"Unknown LLM_PROVIDER={LLM_PROVIDER!r}")


def _rag_system(context: str) -> str:
    return (
        "You are DocIntel, a precise document intelligence assistant.\n\n"
        "RULES:\n"
        "1. Answer ONLY from the retrieved chunks below.\n"
        "2. Cite sources inline with [Source N].\n"
        "3. If the answer is absent, say: \"The provided documents don't contain this information.\"\n"
        "4. Never invent or extrapolate.\n\n"
        f"RETRIEVED CHUNKS:\n{context}"
    )
