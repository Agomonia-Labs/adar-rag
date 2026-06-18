from __future__ import annotations

import os
import re
from typing import Any

from services.restaurant_intelligence import (
    RestaurantIntelligenceError,
    extract_restaurant_menu,
    extract_restaurant_profile,
    merge_restaurant_outputs,
    normalize_menu,
    normalize_profile,
    review_restaurant_quality,
    transcribe_restaurant_audio,
)


TRANSCRIPT_WINDOW_CHARS = int(os.getenv("RESTAURANT_TRANSCRIPT_WINDOW_CHARS", "16000"))
TRANSCRIPT_WINDOW_OVERLAP = int(os.getenv("RESTAURANT_TRANSCRIPT_WINDOW_OVERLAP", "1200"))
TRANSCRIPT_MAX_WINDOWS = int(os.getenv("RESTAURANT_TRANSCRIPT_MAX_WINDOWS", "12"))


async def transcription_tool(context: dict[str, Any], outputs: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
    previous = _first_dict(agent.get("previous_output"), outputs.get("conversation_transcript"))
    if previous and previous.get("transcript_text"):
        return _with_quality(previous, {"complete": True, "missing": [], "confidence": previous.get("confidence", 0)})
    segments = context.get("audio_segments") if isinstance(context.get("audio_segments"), list) else []
    if segments:
        segment_results = []
        transcript_parts = []
        for index, segment in enumerate(segments):
            audio_bytes = segment.get("bytes")
            if not audio_bytes:
                continue
            result = await transcribe_restaurant_audio(
                audio_bytes,
                segment.get("content_type") or context.get("audio_mime_type") or "application/octet-stream",
                context.get("language") or "",
            )
            result["segment_index"] = segment.get("index", index)
            result["audio_filename"] = segment.get("filename") or f"segment-{index + 1}"
            segment_results.append(result)
            text = (result.get("transcript_text") or "").strip()
            if text:
                transcript_parts.append(f"[Audio segment {index + 1} of {len(segments)} | {result['audio_filename']}]\n{text}")
        merged_text = "\n\n".join(transcript_parts).strip()
        if not merged_text:
            raise RestaurantIntelligenceError("Restaurant segmented transcription returned no text")
        windows = _transcript_windows(merged_text)
        finish_reasons = [r.get("finish_reason") for r in segment_results if r.get("finish_reason")]
        result = {
            "transcript_text": merged_text,
            "language": context.get("language") or "unknown",
            "audio_mime_type": context.get("audio_mime_type") or "",
            "audio_bytes": sum(int(s.get("size") or len(s.get("bytes") or b"")) for s in segments),
            "audio_segment_count": len(segments),
            "segment_transcripts": segment_results,
            "finish_reason": ",".join(finish_reasons),
            "confidence": max([r.get("confidence", 0) for r in segment_results] or [0]),
            "transcript_window_count": len(windows),
            "transcript_chars": len(merged_text),
        }
        return _with_quality(result, {
            "complete": True,
            "missing": [],
            "confidence": result.get("confidence", 0),
            "finish_reason": result.get("finish_reason"),
            "audio_segment_count": len(segments),
            "transcript_window_count": len(windows),
        })
    audio_bytes = context.get("audio_bytes")
    if not audio_bytes:
        raise RestaurantIntelligenceError("No audio bytes were provided for restaurant transcription")
    result = await transcribe_restaurant_audio(
        audio_bytes,
        context.get("audio_mime_type") or "application/octet-stream",
        context.get("language") or "",
    )
    if not result.get("transcript_text"):
        raise RestaurantIntelligenceError("Restaurant transcription returned no text")
    windows = _transcript_windows(result.get("transcript_text") or "")
    result["transcript_window_count"] = len(windows)
    result["transcript_chars"] = len(result.get("transcript_text") or "")
    return _with_quality(result, {
        "complete": True,
        "missing": [],
        "confidence": result.get("confidence", 0),
        "finish_reason": result.get("finish_reason"),
        "transcript_window_count": len(windows),
    })


async def profile_tool(context: dict[str, Any], outputs: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
    previous = _first_dict(agent.get("previous_output"), outputs.get("restaurant_profile"))
    try:
        windows = _transcript_windows(_transcript_text(outputs))
        result = {}
        window_results = []
        for index, window in enumerate(windows):
            window_result = await extract_restaurant_profile(_window_label(window, index, len(windows)))
            window_result["window_index"] = index
            window_results.append(window_result)
            result = _merge_profile(result, window_result)
        result = normalize_profile(result)
        result["extraction_metadata"] = {
            "strategy": "windowed_transcript",
            "windows_processed": len(windows),
            "window_chars": TRANSCRIPT_WINDOW_CHARS,
            "overlap_chars": TRANSCRIPT_WINDOW_OVERLAP,
            "window_confidences": [r.get("confidence", 0) for r in window_results],
        }
    except RestaurantIntelligenceError:
        if previous:
            result = previous
        else:
            raise
    return _with_quality(result, _profile_quality(result))


async def menu_tool(context: dict[str, Any], outputs: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
    previous = _first_dict(agent.get("previous_output"), outputs.get("menu_items"))
    try:
        transcript_text = _transcript_text(outputs)
        windows = _transcript_windows(transcript_text)
        result = {"summary": "", "items": [], "confidence": 0}
        window_counts = []
        confidences = []
        for index, window in enumerate(windows):
            window_result = await extract_restaurant_menu(_window_label(window, index, len(windows)), outputs.get("restaurant_profile") or {})
            window_counts.append(len(window_result.get("items") or []))
            confidences.append(window_result.get("confidence", 0))
            result = _merge_item_lists(result, window_result)
        parser_result = _parse_menu_items_from_transcript(transcript_text)
        result = _merge_item_lists(result, parser_result)
        result = normalize_menu({"summary": result.get("summary") or "Menu extracted from transcript windows.", "items": result.get("items") or [], "confidence": max(confidences or [0.75 if parser_result.get("items") else 0])})
        result["extraction_metadata"] = {
            "strategy": "windowed_transcript_plus_pattern_parser",
            "windows_processed": len(windows),
            "window_chars": TRANSCRIPT_WINDOW_CHARS,
            "overlap_chars": TRANSCRIPT_WINDOW_OVERLAP,
            "items_per_window": window_counts,
            "window_confidences": confidences,
            "pattern_parser_items": len(parser_result.get("items") or []),
        }
    except RestaurantIntelligenceError:
        result = previous or {"summary": "Menu extraction failed and requires owner review.", "items": [], "confidence": 0}
    if previous:
        result = _merge_item_lists(previous, result)
    return _with_quality(result, _menu_quality(result))


async def normalize_tool(context: dict[str, Any], outputs: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
    previous = _first_dict(agent.get("previous_output"), outputs.get("normalized_menu"))
    menu = normalize_menu(outputs.get("menu_items") or {})
    result = {
        "restaurant_profile": normalize_profile(outputs.get("restaurant_profile") or {}),
        "menu_items": menu.get("items") or [],
        "confidence": menu.get("confidence") or (outputs.get("menu_items") or {}).get("confidence") or 0,
        "normalization_metadata": {
            "strategy": "deterministic_preserve_all_extracted_rows",
            "input_item_count": len((outputs.get("menu_items") or {}).get("items") or []),
            "output_item_count": len(menu.get("items") or []),
        },
    }
    if previous:
        result["menu_items"] = _merge_item_lists({"items": previous.get("menu_items") or []}, {"items": result["menu_items"]}).get("items") or []
    return _with_quality(result, _normalized_quality(result))


async def quality_tool(context: dict[str, Any], outputs: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
    previous = _first_dict(agent.get("previous_output"), outputs.get("quality_review"))
    try:
        result = await review_restaurant_quality(outputs.get("restaurant_profile") or {}, outputs.get("normalized_menu") or {})
    except RestaurantIntelligenceError:
        result = previous or {
            "summary": "Quality review failed. Owner approval is required before publishing.",
            "missing_profile_fields": [],
            "menu_warnings": [],
            "approval_notes": ["Review all profile and menu fields manually."],
            "ready_for_owner_approval": False,
            "confidence": 0,
        }
    return _with_quality(result, {"complete": bool(result.get("ready_for_owner_approval")), "missing": result.get("missing_profile_fields") or [], "confidence": result.get("confidence", 0)})


async def merge_tool(context: dict[str, Any], outputs: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
    return merge_restaurant_outputs(outputs, context)


RESTAURANT_AGENT_TOOLS = {
    "restaurant.transcribe_audio": transcription_tool,
    "restaurant.extract_profile": profile_tool,
    "restaurant.extract_menu": menu_tool,
    "restaurant.normalize_menu": normalize_tool,
    "restaurant.review_quality": quality_tool,
    "restaurant.merge_outputs": merge_tool,
}


def _transcript_text(outputs: dict[str, Any]) -> str:
    transcript = outputs.get("conversation_transcript") or {}
    text = transcript.get("transcript_text") if isinstance(transcript, dict) else ""
    if not text:
        raise RestaurantIntelligenceError("Transcript is not available for restaurant extraction")
    return text


def _transcript_windows(text: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    window_size = max(4000, TRANSCRIPT_WINDOW_CHARS)
    overlap = max(0, min(TRANSCRIPT_WINDOW_OVERLAP, window_size // 3))
    step = max(1000, window_size - overlap)
    windows = []
    start = 0
    while start < len(text) and len(windows) < max(1, TRANSCRIPT_MAX_WINDOWS):
        end = min(len(text), start + window_size)
        if end < len(text):
            boundary = max(text.rfind("\n", start, end), text.rfind(". ", start, end), text.rfind("; ", start, end))
            if boundary > start + window_size * 0.65:
                end = boundary + 1
        windows.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(0, end - overlap)
    return [w for w in windows if w]


def _window_label(window: str, index: int, total: int) -> str:
    return f"[Transcript window {index + 1} of {total}]\n{window}"


def _with_quality(output: dict[str, Any], quality: dict[str, Any]) -> dict[str, Any]:
    output = dict(output)
    output["agent_quality"] = quality
    return output


def _first_dict(*values: Any) -> dict[str, Any] | None:
    for value in values:
        if isinstance(value, dict) and value:
            return value
    return None


def _profile_quality(data: dict[str, Any]) -> dict[str, Any]:
    missing = [key for key in ("name", "address", "phone") if not data.get(key)]
    return {"complete": not missing, "missing": missing, "confidence": data.get("confidence", 0)}


def _merge_profile(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    merged = dict(previous or {})
    for key, value in (current or {}).items():
        if key in {"agent_quality", "extraction_metadata", "window_index"}:
            continue
        if key == "confidence":
            merged[key] = max(_num(merged.get(key)), _num(value))
        elif isinstance(value, list):
            merged[key] = _dedupe_strings([*(merged.get(key) if isinstance(merged.get(key), list) else []), *value])
        elif isinstance(value, dict):
            existing = merged.get(key) if isinstance(merged.get(key), dict) else {}
            merged[key] = {**existing, **{k: v for k, v in value.items() if v not in (None, "", [])}}
        elif value not in (None, "") and merged.get(key) in (None, ""):
            merged[key] = value
    return merged


def _menu_quality(data: dict[str, Any]) -> dict[str, Any]:
    items = data.get("items") if isinstance(data.get("items"), list) else []
    missing = [] if items else ["menu_items"]
    missing.extend(f"price:{item.get('item_name')}" for item in items if isinstance(item, dict) and item.get("price") in (None, ""))
    return {"complete": not missing, "missing": missing[:20], "confidence": data.get("confidence", 0)}


def _normalized_quality(data: dict[str, Any]) -> dict[str, Any]:
    profile = data.get("restaurant_profile") if isinstance(data.get("restaurant_profile"), dict) else {}
    items = data.get("menu_items") if isinstance(data.get("menu_items"), list) else []
    missing = []
    if not profile.get("name"):
        missing.append("restaurant_name")
    if not items:
        missing.append("menu_items")
    return {"complete": not missing, "missing": missing, "confidence": data.get("confidence", 0)}


def _merge_item_lists(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    merged = dict(current)
    items = list(previous.get("items") or [])
    seen = {_item_key(item) for item in items if isinstance(item, dict)}
    for item in current.get("items") or []:
        if not isinstance(item, dict):
            continue
        key = _item_key(item)
        if not key:
            continue
        if key not in seen:
            items.append(item)
            seen.add(key)
            continue
        for existing in items:
            if _item_key(existing) == key:
                _fill_item(existing, item)
                break
    merged["items"] = items
    return merged


def _parse_menu_items_from_transcript(transcript: str) -> dict[str, Any]:
    text = re.sub(r"\[[^\]]+\]", " ", transcript or "")
    text = re.sub(r"\b(?:Owner|Staff|Unknown|Customer)\s*:\s*", " ", text)
    menu_start = re.search(r"\bmenus?\b|\bappetizer\b|\bbiryani\b|\bentree\b|\bbeverages?\b|\bdesserts?\b", text, re.I)
    if menu_start:
        text = text[menu_start.start():]
    parts = [p.strip(" \t\n:.-") for p in re.split(r"[\n;,]+|(?<!\d)\.\s+|(?<=\d)\.\s+", text) if p.strip()]
    items: list[dict[str, Any]] = []
    current_category = ""
    for part in parts:
        category, remainder = _extract_category(part)
        if category:
            current_category = category
            part = remainder
        if not part:
            continue
        for item in _parse_priced_items(part, current_category):
            items.append(item)
    normalized = normalize_menu({"items": items, "summary": "Menu items parsed directly from transcript item-price patterns.", "confidence": 0.78})
    return normalized


def _extract_category(text: str) -> tuple[str, str]:
    cleaned = text.strip()
    lowered = cleaned.lower()
    markers = (" section", " sections", ":", " beverages", " desserts", " breads")
    if not any(marker in lowered for marker in markers):
        return "", cleaned
    category = ""
    remainder = cleaned
    colon_match = re.match(r"^(?:in\s+|the\s+|now\s+|now\s+i\s+will\s+explain\s+about\s+|i\s+will\s+explain\s+about\s+)?(.{2,80}?)(?:\s+sections?|\s*:)\s*(.*)$", cleaned, re.I)
    if colon_match:
        category = colon_match.group(1)
        remainder = colon_match.group(2) or ""
    elif re.match(r"^(?:in\s+)?(?:beverages|desserts?|breads|extras?)\b", cleaned, re.I):
        words = re.match(r"^(?:in\s+)?([A-Za-z -]+?)(?:\s+you\s+can|\s*$)", cleaned, re.I)
        category = words.group(1) if words else cleaned
        remainder = ""
    if not category:
        return "", cleaned
    category = re.sub(r"^(?:now\s+i\s+will\s+explain\s+about\s+the|now\s+i\s+will\s+explain\s+about|i\s+will\s+explain\s+about\s+the|i\s+will\s+explain\s+about|now\s+|the|in)\s+", "", category, flags=re.I)
    category = re.sub(r"\b(menu|menus|divided|different|multiple|steps|is|are|about|our)\b", " ", category, flags=re.I)
    category = re.sub(r"\s+", " ", category).strip(" -:")
    category = _known_category(category)
    return category.title(), remainder.strip(" -:")


def _parse_priced_items(text: str, category: str) -> list[dict[str, Any]]:
    out = []
    skip = re.search(r"\b(address|phone|website|hour|open|zip code|washington|bothell everett)\b", text, re.I)
    if skip:
        return out
    pattern = re.compile(
        r"(?P<name>[A-Za-z][A-Za-z0-9 '&/$.-]{1,90})\s+"
        r"(?:is\s+|this\s+is\s+|for\s+)?\$?(?P<price>\d{1,3}(?:\.\d{1,2})?)"
        r"(?:\s+to\s+\$?(?P<price_to>\d{1,3}(?:\.\d{1,2})?))?",
        re.I,
    )
    for match in pattern.finditer(text):
        name = re.sub(r"^(?:and|now|in|the)\s+", "", match.group("name").strip(), flags=re.I)
        name = re.sub(r"\b(?:section|sections|menu|menus)$", "", name, flags=re.I).strip(" -:")
        if len(name) < 2 or len(name.split()) > 8:
            continue
        price = match.group("price")
        price_to = match.group("price_to")
        quantity = f"{price} to {price_to}" if price_to else ""
        out.append({
            "category": category,
            "item_name": name,
            "price": price,
            "currency": "USD",
            "quantity": quantity,
            "description": "",
            "ingredients": [],
            "dietary_tags": [],
            "spice_level": "",
            "availability": "available",
            "options": [],
        })
    return out


def _known_category(text: str) -> str:
    lowered = text.lower()
    known = [
        ("non-veg appetizer", "Non-Veg Appetizers"),
        ("non-vegetable appetizer", "Non-Veg Appetizers"),
        ("non vegetable appetizer", "Non-Veg Appetizers"),
        ("vegetable appetizer", "Veg Appetizers"),
        ("veg appetizer", "Veg Appetizers"),
        ("non-veg biryani", "Non-Veg Biryani"),
        ("non veg biryani", "Non-Veg Biryani"),
        ("veg biryani", "Veg Biryani"),
        ("non-veg entree", "Non-Veg Entrees"),
        ("non veg entree", "Non-Veg Entrees"),
        ("veg entree", "Veg Entrees"),
        ("bread", "Breads"),
        ("beverage", "Beverages"),
        ("dessert", "Desserts"),
        ("extra", "Extras"),
    ]
    for needle, label in known:
        if needle in lowered:
            return label
    return text


def _item_key(item: dict[str, Any]) -> str:
    name = str(item.get("item_name") or "").strip().lower()
    category = str(item.get("category") or "").strip().lower()
    return f"{category}:{name}" if name else ""


def _fill_item(existing: dict[str, Any], candidate: dict[str, Any]) -> None:
    for key, value in candidate.items():
        if isinstance(value, list):
            existing[key] = _dedupe_strings([*(existing.get(key) if isinstance(existing.get(key), list) else []), *value])
        elif value not in (None, "", []) and existing.get(key) in (None, "", []):
            existing[key] = value


def _dedupe_strings(values: list[Any]) -> list[str]:
    seen = set()
    out = []
    for value in values:
        text = str(value).strip()
        key = text.lower()
        if text and key not in seen:
            out.append(text)
            seen.add(key)
    return out


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
