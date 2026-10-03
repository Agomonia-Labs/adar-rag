from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Any

import httpx

STANDARD_PROFILE = "standard"
CULTURAL_PERFORMANCE_PROFILE = "cultural_performance"
VIDEO_PROCESSING_PROFILES = {STANDARD_PROFILE, CULTURAL_PERFORMANCE_PROFILE}


def normalize_processing_profile(value: str | None) -> str:
    profile = str(value or STANDARD_PROFILE).strip().lower()
    if profile not in VIDEO_PROCESSING_PROFILES:
        raise ValueError(f"Unsupported video processing profile: {profile}")
    return profile


async def analyze_cultural_clip(
    clip_path: str,
    *,
    start_seconds: float,
    end_seconds: float,
    transcript: str = "",
    frame_captions: list[str] | None = None,
) -> dict[str, Any]:
    """Analyze one short performance clip with its audio and motion intact."""
    api_key = os.getenv("GOOGLE_AI_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GOOGLE_AI_KEY is required for cultural performance analysis")

    max_bytes = int(os.getenv("VIDEO_CULTURAL_MAX_CLIP_BYTES", str(14 * 1024 * 1024)))
    clip_bytes = Path(clip_path).read_bytes()
    if len(clip_bytes) > max_bytes:
        raise RuntimeError(f"Cultural analysis clip exceeds {max_bytes} bytes")

    model = os.getenv("GEMINI_VIDEO_MODEL", os.getenv("GEMINI_CHAT_MODEL", "gemini-2.5-flash"))
    model = model.removeprefix("models/")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    prompt = _cultural_prompt(
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        transcript=transcript,
        frame_captions=frame_captions or [],
    )
    payload = {
        "contents": [{"parts": [
            {"inline_data": {"mime_type": "video/mp4", "data": base64.b64encode(clip_bytes).decode("ascii")}},
            {"text": prompt},
        ]}],
        "generationConfig": {
            "temperature": 0.15,
            "maxOutputTokens": 2200,
            "responseMimeType": "application/json",
        },
    }
    timeout = float(os.getenv("VIDEO_CULTURAL_ANALYSIS_TIMEOUT_SECONDS", "180"))
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, params={"key": api_key}, json=payload)
    if not response.is_success:
        raise RuntimeError(f"Gemini cultural video analysis failed ({response.status_code}): {response.text[:500]}")

    candidates = response.json().get("candidates") or []
    if not candidates:
        raise RuntimeError("Gemini cultural video analysis returned no candidate")
    text = "".join(
        str(part.get("text") or "")
        for part in candidates[0].get("content", {}).get("parts", [])
    )
    return _normalize_analysis(_parse_json_object(text))


def cultural_analysis_text(analysis: dict[str, Any] | None) -> str:
    if not analysis or analysis.get("analysis_status") != "completed":
        return ""
    sections = [
        ("Performance form", analysis.get("performance_form")),
        ("Narrative context", analysis.get("narrative_context")),
        ("Movement", analysis.get("movement_analysis")),
        ("Music and rhythm", analysis.get("music_analysis")),
        ("Expressive cues", _join(analysis.get("expressive_cues"))),
        ("Cultural context", analysis.get("cultural_context")),
        ("Interpretive themes", _join(analysis.get("interpretive_themes"))),
        ("Evidence", _join(analysis.get("evidence"))),
    ]
    return "\n".join(f"{label}: {value}" for label, value in sections if value)


def _cultural_prompt(
    *, start_seconds: float,
    end_seconds: float,
    transcript: str,
    frame_captions: list[str],
) -> str:
    return f"""
Analyze this cultural performance clip from {start_seconds:.2f}s to {end_seconds:.2f}s using its motion,
music, voice, visible expression, staging, and supplied context together. This may be dance, song,
theatre, ritual, or another cultural performance.

Describe only observable evidence. Never claim to know the performer's private feelings, identity,
intent, ethnicity, religion, health, or other sensitive traits. Phrase emotional interpretation as
"observable cues are consistent with..." and include uncertainty. Do not invent a named tradition,
gesture, mudra, tala, rasa, instrument, language, or story when the evidence is insufficient.

Transcript context:
{transcript[:4000] or "No transcript available."}

Nearby frame descriptions:
{json.dumps(frame_captions[:6], ensure_ascii=False)}

Return one JSON object with exactly these fields:
{{
  "performance_form": "observed or likely form, with uncertainty",
  "narrative_context": "what appears to be communicated in this interval",
  "movement_analysis": "posture, gesture, footwork, turns, tempo, stillness, group interaction and transitions",
  "music_analysis": "rhythm, tempo, dynamics, vocal or instrumental qualities and their relationship to movement",
  "expressive_cues": ["observable cue and cautious interpretation"],
  "cultural_context": "culturally relevant interpretation supported by evidence",
  "interpretive_themes": ["theme"],
  "evidence": ["timestamped visual or audio observation"],
  "confidence": 0.0
}}
""".strip()


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip(), flags=re.I | re.S)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.S)
        if not match:
            raise ValueError("Cultural video analysis did not return JSON")
        value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("Cultural video analysis must return a JSON object")
    return value


def _normalize_analysis(value: dict[str, Any]) -> dict[str, Any]:
    try:
        confidence = max(0.0, min(1.0, float(value.get("confidence") or 0)))
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "analysis_status": "completed",
        "performance_form": _text(value.get("performance_form")),
        "narrative_context": _text(value.get("narrative_context")),
        "movement_analysis": _text(value.get("movement_analysis")),
        "music_analysis": _text(value.get("music_analysis")),
        "expressive_cues": _list(value.get("expressive_cues")),
        "cultural_context": _text(value.get("cultural_context")),
        "interpretive_themes": _list(value.get("interpretive_themes")),
        "evidence": _list(value.get("evidence")),
        "confidence": confidence,
        "interpretation_guardrail": "Observable performance cues, not inferred private emotion.",
    }


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())[:2000]


def _list(value: Any) -> list[str]:
    if not isinstance(value, list):
        value = [value] if value else []
    return [_text(item)[:500] for item in value if _text(item)][:12]


def _join(value: Any) -> str:
    return "; ".join(_list(value))
