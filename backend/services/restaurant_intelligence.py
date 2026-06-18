from __future__ import annotations

import base64
import json
import logging
import os
import re
from datetime import datetime
from typing import Any

import httpx

from services.llm import chat_stream

log = logging.getLogger("docintel.restaurant")

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


class RestaurantIntelligenceError(RuntimeError):
    """Raised when restaurant scribe output cannot be converted into usable data."""


async def transcribe_restaurant_audio(audio_bytes: bytes, content_type: str, language: str = "") -> dict[str, Any]:
    google_ai_key = os.getenv("GOOGLE_AI_KEY", "").strip()
    if not google_ai_key:
        raise RestaurantIntelligenceError("GOOGLE_AI_KEY is not configured for restaurant transcription")
    model = os.getenv("GEMINI_AUDIO_MODEL", os.getenv("GEMINI_CHAT_MODEL", "gemini-2.5-flash")).removeprefix("models/")
    prompt = (
        "Transcribe this restaurant owner/menu intake conversation into plain text. "
        "Preserve menu item names, prices, quantities, addresses, phone numbers, hours, and descriptions. "
        "If speaker turns are clear, label them as Owner, Staff, Customer, or Unknown. "
        "Do not summarize. Do not create sample restaurant menus, placeholder addresses, or plausible content. "
        "Only write words that are actually present in the audio. "
        "If the audio is empty, corrupt, not decodable, or unintelligible, return an empty string."
    )
    if language:
        prompt += f" The expected spoken language locale is {language}."
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {
                    "mime_type": content_type,
                    "data": base64.b64encode(audio_bytes).decode("ascii"),
                }},
            ],
        }],
        "generationConfig": {
            "temperature": 0,
            "maxOutputTokens": int(os.getenv("RESTAURANT_TRANSCRIPTION_MAX_OUTPUT_TOKENS", "8192")),
        },
    }
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(f"{GEMINI_BASE}/{model}:generateContent", params={"key": google_ai_key}, json=payload)
    except httpx.HTTPError as exc:
        raise RestaurantIntelligenceError(f"Restaurant transcription service unavailable: {exc}") from exc
    if not resp.is_success:
        raise RestaurantIntelligenceError(f"Restaurant transcription failed {resp.status_code}: {resp.text[:500]}")
    body = resp.json()
    parts = body.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    text = " ".join((p.get("text") or "").strip() for p in parts if p.get("text")).strip()
    return {
        "transcript_text": text,
        "language": language or "unknown",
        "audio_mime_type": content_type,
        "audio_bytes": len(audio_bytes),
        "model": model,
        "usage": body.get("usageMetadata") or {},
        "finish_reason": body.get("candidates", [{}])[0].get("finishReason"),
        "confidence": 0.85 if text else 0,
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


async def extract_restaurant_profile(transcript_text: str) -> dict[str, Any]:
    system = _system("Restaurant Profile Agent")
    user = f"""Extract restaurant business profile details from this transcript.

Return JSON in this exact shape:
{{
  "name": string|null,
  "description": string,
  "cuisine_type": string,
  "address": string,
  "phone": string,
  "email": string,
  "website": string,
  "hours": {{"monday": string, "tuesday": string, "wednesday": string, "thursday": string, "friday": string, "saturday": string, "sunday": string}},
  "service_options": ["dine_in|takeout|delivery|catering|reservation|unknown"],
  "payment_options": ["cash|card|mobile_payment|online|unknown"],
  "confidence": 0.0-1.0
}}

TRANSCRIPT:
{transcript_text[:24000]}"""
    return normalize_profile(await _complete_json(system, user))


async def extract_restaurant_menu(transcript_text: str, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    system = _system("Menu Extraction Agent")
    user = f"""Extract menu items from this restaurant transcript.

Return JSON in this exact shape:
{{
  "summary": "1-2 sentence menu summary",
  "items": [
    {{
      "category": string,
      "item_name": string,
      "price": number|null,
      "currency": "USD",
      "quantity": string,
      "description": string,
      "ingredients": [string],
      "dietary_tags": [string],
      "spice_level": string,
      "availability": "available|seasonal|limited|unknown",
      "options": [{{"name": string, "price_delta": number|null}}]
    }}
  ],
  "confidence": 0.0-1.0
}}

PROFILE:
{json.dumps(profile or {})[:4000]}

TRANSCRIPT:
{transcript_text[:26000]}"""
    return normalize_menu(await _complete_json(system, user))


async def normalize_restaurant_menu(profile: dict[str, Any], menu: dict[str, Any]) -> dict[str, Any]:
    system = _system("Menu Normalization Agent")
    user = f"""Normalize this restaurant profile and menu for database storage and comparison.

Return JSON in this exact shape:
{{
  "restaurant_profile": {{
    "name": string|null,
    "description": string,
    "cuisine_type": string,
    "address": string,
    "phone": string,
    "email": string,
    "website": string,
    "hours": object,
    "service_options": [string],
    "payment_options": [string]
  }},
  "menu_items": [
    {{
      "category": string,
      "item_name": string,
      "price": number|null,
      "currency": "USD",
      "quantity": string,
      "description": string,
      "ingredients": [string],
      "dietary_tags": [string],
      "spice_level": string,
      "availability": "available|seasonal|limited|unknown",
      "options": [{{"name": string, "price_delta": number|null}}]
    }}
  ],
  "confidence": 0.0-1.0
}}

PROFILE:
{json.dumps(profile or {})[:6000]}

MENU:
{json.dumps(menu or {})[:12000]}"""
    data = await _complete_json(system, user)
    return {
        "restaurant_profile": normalize_profile(data.get("restaurant_profile") or profile or {}),
        "menu_items": normalize_menu({"items": data.get("menu_items") or (menu or {}).get("items") or []}).get("items", []),
        "confidence": _float(data.get("confidence"), 0),
    }


async def review_restaurant_quality(profile: dict[str, Any], menu: dict[str, Any]) -> dict[str, Any]:
    system = _system("Restaurant Quality Review Agent")
    user = f"""Review this restaurant/menu packet for missing or ambiguous fields before owner approval.

Return JSON in this exact shape:
{{
  "summary": "1-2 sentence quality summary",
  "missing_profile_fields": [string],
  "menu_warnings": [{{"item_name": string, "issue": string, "recommended_fix": string}}],
  "approval_notes": [string],
  "ready_for_owner_approval": true,
  "confidence": 0.0-1.0
}}

PROFILE:
{json.dumps(profile or {})[:6000]}

MENU:
{json.dumps(menu or {})[:12000]}"""
    data = await _complete_json(system, user)
    return {
        "summary": data.get("summary") or "",
        "missing_profile_fields": _string_list(data.get("missing_profile_fields")),
        "menu_warnings": _list(data.get("menu_warnings")),
        "approval_notes": _string_list(data.get("approval_notes")),
        "ready_for_owner_approval": bool(data.get("ready_for_owner_approval", True)),
        "confidence": _float(data.get("confidence"), 0),
    }


def merge_restaurant_outputs(outputs: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    normalized = outputs.get("normalized_menu") or {}
    profile = normalize_profile(normalized.get("restaurant_profile") or outputs.get("restaurant_profile") or {})
    menu = normalize_menu({"items": normalized.get("menu_items") or (outputs.get("menu_items") or {}).get("items") or []})
    packet = {
        "restaurant_profile": profile,
        "menu_items": menu.get("items", []),
        "conversation_transcript": outputs.get("conversation_transcript") or {},
        "quality_review": outputs.get("quality_review") or {},
        "approved_for": "owner_review_required",
        "guardrail": "Restaurant owner or authorized operator must review and approve before publishing menu data.",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "source": {
            "audio_filename": (context or {}).get("audio_filename"),
            "language": (context or {}).get("language") or "unknown",
        },
    }
    return {**packet, "approved_packet": packet}


def normalize_profile(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": _text(data.get("name")),
        "description": _text(data.get("description")),
        "cuisine_type": _text(data.get("cuisine_type")),
        "address": _text(data.get("address")),
        "phone": _text(data.get("phone")),
        "email": _text(data.get("email")),
        "website": _text(data.get("website")),
        "hours": data.get("hours") if isinstance(data.get("hours"), dict) else {},
        "service_options": _string_list(data.get("service_options")),
        "payment_options": _string_list(data.get("payment_options")),
        "confidence": _float(data.get("confidence"), 0),
    }


def normalize_menu(data: dict[str, Any]) -> dict[str, Any]:
    items = []
    for item in _list(data.get("items")):
        if not isinstance(item, dict):
            continue
        name = _text(item.get("item_name") or item.get("name"))
        if not name:
            continue
        items.append({
            "category": _text(item.get("category")),
            "item_name": name,
            "price": _price(item.get("price")),
            "currency": (_text(item.get("currency")) or "USD").upper()[:8],
            "quantity": _text(item.get("quantity")),
            "description": _text(item.get("description")),
            "ingredients": _string_list(item.get("ingredients")),
            "dietary_tags": _string_list(item.get("dietary_tags")),
            "spice_level": _text(item.get("spice_level")),
            "availability": _text(item.get("availability")) or "available",
            "options": _list(item.get("options")),
        })
    return {"summary": _text(data.get("summary")), "items": items, "confidence": _float(data.get("confidence"), 0)}


async def _complete_json(system_prompt: str, user_prompt: str) -> dict[str, Any]:
    raw = await _complete_text(system_prompt, user_prompt)
    try:
        return _json_from_text(raw)
    except json.JSONDecodeError as exc:
        log.warning("Restaurant model returned malformed JSON; attempting repair: %s", exc)
        repaired = await _complete_text(
            "You repair malformed JSON. Return only valid JSON. Do not explain.",
            "Repair this malformed JSON into one valid JSON object. Preserve recoverable keys and values. "
            "If a value is incomplete, close it as a short string. Return only JSON:\n\n"
            + raw[:12000],
        )
        try:
            return _json_from_text(repaired)
        except json.JSONDecodeError as repair_exc:
            preview = re.sub(r"\s+", " ", raw[:500])
            raise RestaurantIntelligenceError(
                f"Restaurant model returned invalid JSON after repair attempt: {repair_exc}. Preview: {preview}"
            ) from repair_exc


async def _complete_text(system_prompt: str, user_prompt: str) -> str:
    chunks: list[str] = []

    async def on_token(token: str) -> None:
        chunks.append(token)

    await chat_stream([{"role": "user", "content": user_prompt}], system_prompt, on_token)
    text = "".join(chunks).strip()
    if not text:
        raise RestaurantIntelligenceError("Restaurant model returned no text")
    return text


def _json_from_text(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"```(?:json)?\s*", "", text or "").replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as first_error:
        obj = _extract_json_object(cleaned)
        if not obj:
            raise first_error
        return json.loads(obj)


def _extract_json_object(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for pos in range(start, len(text)):
        char = text[pos]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : pos + 1]
    return None


def _system(agent_name: str) -> str:
    return (
        f"You are the {agent_name} inside DocIntel's restaurant vertical. "
        "Extract only facts supported by the transcript or provided intermediate outputs. "
        "Return strict JSON only. Use null or empty strings when a value is missing. "
        "Do not invent prices, hours, addresses, ingredients, or menu items."
    )


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _string_list(value: Any) -> list[str]:
    return [str(v).strip() for v in _list(value) if str(v).strip()]


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _price(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return round(float(str(value).replace("$", "").replace(",", "").strip()), 2)
    except (TypeError, ValueError):
        return None
