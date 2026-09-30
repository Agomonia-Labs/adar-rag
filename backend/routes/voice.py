# routes/voice.py
from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import shutil
import subprocess
import tempfile
from xml.sax.saxutils import escape as _xml_escape

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from auth.dependencies import CurrentUser
from database.connection import get_db
from services.limiter import usr_voice_stt_15_per_min, usr_voice_tts_20_per_min
from services.tracing import start_trace, finish_trace, span, record_llm_event
from services.usage import check_and_log_daily_event

log = logging.getLogger("docintel.voice")
router = APIRouter()

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
MAX_AUDIO_BYTES = int(os.getenv("VOICE_INPUT_MAX_MB", "10")) * 1024 * 1024
SUPPORTED_AUDIO_TYPES = {
    "audio/webm",
    "audio/mp4",
    "audio/mpeg",
    "audio/mp3",
    "audio/wav",
    "audio/x-wav",
    "audio/ogg",
}

# iOS's native multipart uploader reports .m4a recordings using these
# platform-specific MIME aliases -- regardless of what the client's JS
# code declares as the part's Content-Type, the OS networking layer
# rewrites it based on the file extension. They're the same MPEG-4/AAC
# audio Gemini already accepts as "audio/mp4", so normalize rather than
# reject them (this is what the DocIntel mobile app's voice input hits
# on real iOS devices -- desktop's browser recorder sends audio/webm,
# which is why voice input there was unaffected).
AUDIO_TYPE_ALIASES = {
    "audio/x-m4a": "audio/mp4",
    "audio/m4a": "audio/mp4",
    "audio/aac": "audio/mp4",
}

# Bangla and English route to Google Cloud Speech-to-Text -- the same
# engine adar-core's Geetabitan /api/stt endpoint uses for Bangla, and
# the same call pattern this repo's own video_intelligence.py already
# uses for video transcripts -- instead of the generic Gemini path
# below. Everything else (Hindi, Spanish, French, or no language set)
# stays on Gemini. Keys here are the plain-language hints
# LanguageContext already sends (see i18n/languages.ts's sttHint),
# lowercased, so no mobile client change is needed.
GOOGLE_SPEECH_LANGUAGE_ROUTES = {
    "bengali": "bn-IN",
    "bangla": "bn-IN",
    "english": "en-US",
}


def _resolve_google_speech_language(language: str) -> str | None:
    return GOOGLE_SPEECH_LANGUAGE_ROUTES.get((language or "").strip().lower())


async def _transcribe_with_google_speech(data: bytes, content_type: str, language_code: str) -> str:
    """Same Google Cloud Speech-to-Text v1 recognize call adar-core's
    Geetabitan /api/stt endpoint and this repo's video_intelligence.py
    both use. Google STT wants a plain PCM/FLAC/OPUS stream, not an
    arbitrary upload container, so convert to 16kHz mono FLAC with
    ffmpeg first (mirrors video_intelligence.py's _extract_audio_chunk)."""
    api_key = (
        os.getenv("GOOGLE_SPEECH_API_KEY")
        or os.getenv("GOOGLE_STT_API_KEY")
        or os.getenv("GEETABITAN_SPEECH_API_KEY")
        or os.getenv("GOOGLE_AI_KEY")
        or os.getenv("GOOGLE_API_KEY")
        or ""
    ).strip()
    if not api_key:
        raise RuntimeError("Google Speech-to-Text API key is not configured")
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is not installed")

    src_ext = {
        "audio/webm": ".webm", "audio/mp4": ".mp4", "audio/mpeg": ".mp3",
        "audio/mp3": ".mp3", "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/ogg": ".ogg",
    }.get(content_type, ".bin")

    with tempfile.NamedTemporaryFile(suffix=src_ext, delete=False) as src_file:
        src_file.write(data)
        src_path = src_file.name
    flac_handle = tempfile.NamedTemporaryFile(suffix=".flac", delete=False)
    flac_handle.close()
    flac_path = flac_handle.name
    try:
        await asyncio.to_thread(
            subprocess.run,
            ["ffmpeg", "-y", "-i", src_path, "-vn", "-acodec", "flac", "-ar", "16000", "-ac", "1", flac_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        with open(flac_path, "rb") as f:
            audio_b64 = base64.b64encode(f.read()).decode()
    finally:
        for p in (src_path, flac_path):
            try:
                os.unlink(p)
            except OSError:
                pass

    payload = {
        "config": {
            "encoding": "FLAC",
            "sampleRateHertz": 16000,
            "languageCode": language_code,
            "enableAutomaticPunctuation": True,
        },
        "audio": {"content": audio_b64},
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://speech.googleapis.com/v1/speech:recognize",
            params={"key": api_key},
            json=payload,
        )
    if not resp.is_success:
        raise RuntimeError(f"Google Speech error {resp.status_code}: {resp.text[:300]}")
    parts = []
    for result in resp.json().get("results", []):
        alternatives = result.get("alternatives") or []
        if alternatives:
            parts.append(alternatives[0].get("transcript", ""))
    return " ".join(p for p in parts if p).strip()


# ── Text-to-speech (POST /speak) ────────────────────────────────────────────
# Same Google Cloud TTS voices adar-core's Geetabitan /api/tts and ADAR
# Front Desk /api/demo/tts already use -- most importantly
# bn-IN-Chirp3-HD-Fenrir, the explicitly male Bangla voice, rather than
# whatever generic voice the on-device OS picks for expo-speech. Keyed by
# the same 2-letter codes apps/docintel/src/i18n/languages.ts uses.
TTS_VOICE_BY_LANGUAGE = {
    "en": {"languageCode": "en-US", "name": "en-US-Chirp3-HD-Fenrir"},
    "bn": {"languageCode": "bn-IN", "name": "bn-IN-Chirp3-HD-Fenrir"},
    "hi": {"languageCode": "hi-IN"},
    "es": {"languageCode": "es-US"},
    # Added for the mobile Conversation Assistant (routes/telephony.py),
    # which offers the same 6 languages as ConversationPanel.jsx's
    # "Language" select -- ar/ur were missing here and fell back silently
    # to the English voice for those two.
    "ar": {"languageCode": "ar-XA"},
    "ur": {"languageCode": "ur-PK"},
}

_CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_HEADER_RE = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_BOLD_ITALIC_RE = re.compile(r"\*\*\*([^*]+)\*\*\*")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_BOLD_UNDERSCORE_RE = re.compile(r"__([^_]+)__")
_ITALIC_STAR_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_ITALIC_UNDERSCORE_RE = re.compile(r"(?<!_)_([^_\n]+)_(?!_)")
_BULLET_RE = re.compile(r"^\s{0,3}[-*+]\s+", re.MULTILINE)
_BLOCKQUOTE_RE = re.compile(r"^\s{0,3}>+\s?", re.MULTILINE)
_STRAY_MARK_RE = re.compile(r"[*_`#>]+")
_DR_TITLE_RE = re.compile(r"\bDr\.?\s+(?=[A-Z])")

# Chirp3-HD rejects any individual *sentence* that's too long (evaluated on
# Google's own sentence-boundary parsing, not our total text length) --
# mirrors adar-core's /api/tts rewrap exactly, including respecting the
# Bangla "।" danda as a sentence terminator.
SAFE_SENTENCE_CHARS = 150
SENTENCE_END_RE = re.compile(r"(?<=[।.!?])\s+")


def _strip_markdown_for_speech(text: str) -> str:
    """AI answers here are markdown (see MarkdownMessage.tsx) -- without
    this, Google's TTS voices read the literal symbols out loud (e.g. a
    blockquote's ">" comes out as the spoken word "greater than" right at
    a paragraph break), which is exactly the confusing-transition problem
    being fixed here."""
    text = _CODE_BLOCK_RE.sub(" ", text)
    text = _INLINE_CODE_RE.sub(r"\1", text)
    text = _HEADER_RE.sub("", text)
    text = _LINK_RE.sub(r"\1", text)
    text = _BOLD_ITALIC_RE.sub(r"\1", text)
    text = _BOLD_RE.sub(r"\1", text)
    text = _BOLD_UNDERSCORE_RE.sub(r"\1", text)
    text = _ITALIC_STAR_RE.sub(r"\1", text)
    text = _ITALIC_UNDERSCORE_RE.sub(r"\1", text)
    text = _BULLET_RE.sub("", text)
    text = _BLOCKQUOTE_RE.sub("", text)
    text = _STRAY_MARK_RE.sub("", text)
    text = _DR_TITLE_RE.sub("Doctor ", text)
    return text


def _rewrap_sentence(chunk: str) -> list[str]:
    chunk = chunk.strip()
    if not chunk:
        return []
    if len(chunk) <= SAFE_SENTENCE_CHARS:
        return [chunk]
    pieces: list[str] = []
    current = ""
    for word in chunk.split():
        candidate = f"{current} {word}".strip()
        if len(candidate) > SAFE_SENTENCE_CHARS and current:
            pieces.append(current.rstrip("।.!?,") + "।")
            current = word
        else:
            current = candidate
    if current:
        pieces.append(current.rstrip("।.!?,") + "।")
    return pieces


def _build_speech_ssml(raw_text: str) -> str:
    """Unlike adar-core's /api/tts (which collapses all whitespace,
    including newlines, into a single run of prose), this keeps paragraph
    and line breaks as real pauses: a 500ms break between paragraphs, a
    200ms break between lines within one paragraph -- an audible cue that
    a new thought is starting, for Bangla listeners especially."""
    paragraphs = re.split(r"\n\s*\n", raw_text.strip())
    paragraph_ssml: list[str] = []
    for para in paragraphs:
        if not para.strip():
            continue
        line_ssml: list[str] = []
        for line in para.split("\n"):
            if not line.strip():
                continue
            cleaned = _strip_markdown_for_speech(line)
            cleaned = re.sub(r"[ \t]+", " ", cleaned).strip()
            if not cleaned:
                continue
            sentences = [s for s in SENTENCE_END_RE.split(cleaned) if s.strip()]
            rewrapped: list[str] = []
            for sentence in sentences:
                rewrapped.extend(_rewrap_sentence(sentence))
            line_text = " ".join(rewrapped).strip()
            if line_text:
                line_ssml.append(_xml_escape(line_text))
        if line_ssml:
            paragraph_ssml.append('<break time="200ms"/>'.join(line_ssml))
    body = '<break time="500ms"/>'.join(paragraph_ssml)
    return f"<speak>{body}</speak>"


# Google's synthesize request caps the WHOLE payload (input.text or
# input.ssml) at 5000 BYTES, not characters -- "Either `input.text` or
# `input.ssml` is longer than the limit of 5000 bytes." The old cap here
# was `raw_text[:3500]`, a CHARACTER count. That's fine for English, but
# Bangla (and most non-Latin scripts) runs ~3 bytes/character in UTF-8, so
# 3500 Bangla characters can be 10000+ bytes -- more than double the real
# limit -- which is exactly what was tripping this error for Bangla
# answers specifically (the one language actually routed through this
# endpoint). Byte-cap the raw text first, then shrink further if the
# built SSML (which adds <speak>/<break> markup on top) is still over.
MAX_SSML_BYTES = 4900  # Google's hard limit is 5000; keep a safety margin


def _byte_truncate(text: str, max_bytes: int) -> str:
    """Cut text down to fit max_bytes once UTF-8 encoded, without splitting
    a multi-byte character (which would otherwise corrupt the last glyph
    or occasionally break JSON encoding)."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    cut = encoded[:max_bytes]
    while cut:
        try:
            return cut.decode("utf-8")
        except UnicodeDecodeError:
            cut = cut[:-1]
    return ""


class SpeakRequest(BaseModel):
    text: str
    language: str = "en"


@router.post("/speak")
async def speak_text(
    body: SpeakRequest,
    current_user: CurrentUser,
    _rl=Depends(usr_voice_tts_20_per_min),
):
    raw_text = (body.text or "").strip()
    if not raw_text:
        raise HTTPException(400, "text is required")
    # Byte-cap (not character-cap -- see MAX_SSML_BYTES comment above) with
    # headroom for the <speak>/<break> markup _build_speech_ssml adds.
    raw_text = _byte_truncate(raw_text, 3200)

    ssml = _build_speech_ssml(raw_text)
    # Defensive backstop: a pathologically line-broken answer adds a
    # <break> tag per line/paragraph, which can still push a 3200-byte
    # text over the 5000-byte SSML limit. Shrink and rebuild rather than
    # let Google 400 the request.
    shrink_attempts = 0
    while len(ssml.encode("utf-8")) > MAX_SSML_BYTES and raw_text and shrink_attempts < 10:
        raw_text = _byte_truncate(raw_text, max(0, len(raw_text.encode("utf-8")) - 500))
        ssml = _build_speech_ssml(raw_text)
        shrink_attempts += 1

    if ssml == "<speak></speak>" or len(ssml.encode("utf-8")) > MAX_SSML_BYTES:
        raise HTTPException(400, "Nothing left to speak after removing formatting")

    voice = TTS_VOICE_BY_LANGUAGE.get((body.language or "").strip().lower(), TTS_VOICE_BY_LANGUAGE["en"])

    api_key = (
        os.getenv("GOOGLE_TTS_API_KEY")
        or os.getenv("GOOGLE_SPEECH_API_KEY")
        or os.getenv("GOOGLE_AI_KEY")
        or os.getenv("GOOGLE_API_KEY")
        or ""
    ).strip()
    if not api_key:
        raise HTTPException(500, "Text-to-speech is not configured")

    payload = {
        "input": {"ssml": ssml},
        "voice": voice,
        "audioConfig": {"audioEncoding": "MP3"},
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://texttospeech.googleapis.com/v1/text:synthesize",
            params={"key": api_key},
            json=payload,
        )
    if not resp.is_success:
        log.warning("Text-to-speech failed %s: %s", resp.status_code, resp.text[:500])
        # Surface Google's actual error text instead of a generic message --
        # adar-core's own /api/demo/tts (the working Front Desk/Geetabitan
        # Listen button) does the same (`detail=f"TTS error: {resp.text[:200]}"`).
        # The most likely cause of a failure here specifically: adar-core
        # deliberately uses a SEPARATE, DEDICATED API key
        # (GEETABITAN_TTS_API_KEY) restricted only to
        # texttospeech.googleapis.com -- see its api/main.py comment "Use
        # dedicated TTS key (restricted only to texttospeech.googleapis.com)".
        # This endpoint currently falls back to GOOGLE_SPEECH_API_KEY, which
        # is scoped for Speech-to-Text and very likely does NOT have the
        # Text-to-Speech API in its allowed API restrictions -- Google
        # returns a 403 API_KEY_SERVICE_BLOCKED in that case, which is what
        # this now reports back instead of hiding it behind "failed".
        try:
            google_detail = resp.json().get("error", {}).get("message", "")
        except Exception:
            google_detail = ""
        detail = f"Text-to-speech failed: {google_detail or resp.text[:200]}"
        raise HTTPException(502, detail)

    audio_b64 = resp.json().get("audioContent") or ""
    if not audio_b64:
        raise HTTPException(502, "Text-to-speech returned no audio")
    return {"audio_base64": audio_b64, "mime_type": "audio/mpeg"}


@router.post("/transcribe")
async def transcribe_voice(
    request: Request,
    current_user: CurrentUser,
    audio: UploadFile = File(...),
    language: str = Form(""),
    _rl=Depends(usr_voice_stt_15_per_min),
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    trace_id = await start_trace(
        "voice_chat",
        trace_id=getattr(request.state, "trace_id", None),
        user_id=user_id,
        client_info={
            "ip": request.client.host if request.client else None,
            "user_agent": request.headers.get("user-agent"),
        },
        metadata={"language": language, "filename": audio.filename},
    )
    content_type = (audio.content_type or "application/octet-stream").split(";")[0].strip().lower()
    content_type = AUDIO_TYPE_ALIASES.get(content_type, content_type)
    if content_type not in SUPPORTED_AUDIO_TYPES:
        await finish_trace(trace_id, "error", f"Unsupported audio format: {content_type}")
        raise HTTPException(400, f"Unsupported audio format: {content_type}")

    data = await audio.read()
    if not data:
        await finish_trace(trace_id, "error", "No audio received")
        raise HTTPException(400, "No audio received")
    if len(data) > MAX_AUDIO_BYTES:
        await finish_trace(trace_id, "error", "Audio is too large")
        raise HTTPException(413, f"Audio is too large. Max {MAX_AUDIO_BYTES // 1024 // 1024} MB")
    await check_and_log_daily_event(
        db,
        user_id,
        "voice_transcription",
        "max_voice_transcriptions_day",
        metadata={"language": language, "filename": audio.filename, "audio_bytes": len(data)},
    )

    google_speech_language = _resolve_google_speech_language(language)
    if google_speech_language:
        async with span(
            "google_speech_transcription",
            trace_id=trace_id,
            metadata={"language_code": google_speech_language, "content_type": content_type, "audio_bytes": len(data)},
        ) as sp:
            try:
                text = await _transcribe_with_google_speech(data, content_type, google_speech_language)
            except Exception as exc:
                log.warning("Google Speech transcription failed for %s, falling back to Gemini: %s", google_speech_language, exc)
                await record_llm_event(
                    trace_id=trace_id,
                    span_id=sp,
                    provider="google_speech",
                    model="speech-to-text-v1",
                    operation="audio_transcribe",
                    tool_request={"language_code": google_speech_language, "audio_bytes": len(data)},
                    error=str(exc)[:500],
                )
            else:
                await record_llm_event(
                    trace_id=trace_id,
                    span_id=sp,
                    provider="google_speech",
                    model="speech-to-text-v1",
                    operation="audio_transcribe",
                    tool_request={"language_code": google_speech_language, "audio_bytes": len(data)},
                    llm_response=text,
                )
                await finish_trace(trace_id, "success")
                return {"text": text, "trace_id": trace_id}

    google_ai_key = os.getenv("GOOGLE_AI_KEY", "").strip()
    if not google_ai_key:
        await finish_trace(trace_id, "error", "GOOGLE_AI_KEY is not configured")
        raise HTTPException(500, "GOOGLE_AI_KEY is not configured for voice transcription")

    model = os.getenv("GEMINI_AUDIO_MODEL", os.getenv("GEMINI_CHAT_MODEL", "gemini-2.5-flash")).removeprefix("models/")
    prompt = (
        "Transcribe this microphone audio into plain text only. "
        "Do not translate. Do not summarize. Do not add punctuation unless it is clearly spoken. "
        "If the audio is empty or unintelligible, return an empty string."
    )
    if language:
        prompt += f" The expected spoken language locale is {language}."

    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {
                    "mime_type": content_type,
                    "data": base64.b64encode(data).decode("ascii"),
                }},
            ],
        }],
        "generationConfig": {
            "temperature": 0,
            "maxOutputTokens": 512,
        },
    }

    url = f"{GEMINI_BASE}/{model}:generateContent"
    try:
        async with span("gemini_audio_transcription", trace_id=trace_id, metadata={"model": model, "content_type": content_type, "audio_bytes": len(data)}) as sp:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(url, params={"key": google_ai_key}, json=payload)
    except httpx.HTTPError as exc:
        log.warning("Voice transcription request failed: %s", exc)
        await finish_trace(trace_id, "error", str(exc))
        raise HTTPException(502, "Voice transcription service is unavailable")

    if not resp.is_success:
        log.warning("Gemini voice transcription failed %s: %s", resp.status_code, resp.text[:500])
        await record_llm_event(
            trace_id=trace_id,
            span_id=sp,
            provider="gemini",
            model=model,
            operation="audio_transcribe",
            system_prompt=prompt,
            tool_request={"mime_type": content_type, "audio_bytes": len(data), "language": language},
            error=resp.text[:500],
        )
        await finish_trace(trace_id, "error", f"Gemini voice transcription failed {resp.status_code}")
        raise HTTPException(resp.status_code, "Voice transcription failed")

    body = resp.json()
    parts = body.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    text = " ".join((p.get("text") or "").strip() for p in parts if p.get("text")).strip()
    usage = body.get("usageMetadata") or {}
    await record_llm_event(
        trace_id=trace_id,
        span_id=sp,
        provider="gemini",
        model=model,
        operation="audio_transcribe",
        system_prompt=prompt,
        tool_request={"mime_type": content_type, "audio_bytes": len(data), "language": language},
        llm_response=text,
        input_tokens=usage.get("promptTokenCount"),
        output_tokens=usage.get("candidatesTokenCount"),
        finish_reason=body.get("candidates", [{}])[0].get("finishReason"),
    )
    await finish_trace(trace_id, "success")
    return {"text": text, "trace_id": trace_id}
