# routes/voice.py
from __future__ import annotations

import base64
import logging
import os

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from auth.dependencies import CurrentUser
from database.connection import get_db
from services.limiter import ip_10_per_min
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


@router.post("/transcribe")
async def transcribe_voice(
    request: Request,
    current_user: CurrentUser,
    audio: UploadFile = File(...),
    language: str = Form(""),
    _rl=Depends(ip_10_per_min),
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
    google_ai_key = os.getenv("GOOGLE_AI_KEY", "").strip()
    if not google_ai_key:
        await finish_trace(trace_id, "error", "GOOGLE_AI_KEY is not configured")
        raise HTTPException(500, "GOOGLE_AI_KEY is not configured for voice transcription")

    content_type = (audio.content_type or "application/octet-stream").split(";")[0].strip().lower()
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
