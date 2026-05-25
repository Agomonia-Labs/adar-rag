from __future__ import annotations
from collections import Counter

LANGUAGE_NAMES = {
    "en": "English",
    "bn": "Bengali",
    "hi": "Hindi",
    "ar": "Arabic",
}

RTL_LANGUAGES = {"ar", "he", "fa", "ur"}


def normalize_language(code: str | None) -> str:
    if not code:
        return "en"
    base = code.strip().lower().split("-")[0]
    return base if len(base) == 2 else "en"


def primary_language(codes: list[str | None]) -> str:
    normalized = [normalize_language(c) for c in codes if c]
    if not normalized:
        return "en"
    return Counter(normalized).most_common(1)[0][0]


def language_name(code: str | None) -> str:
    normalized = normalize_language(code)
    return LANGUAGE_NAMES.get(normalized, normalized)


def response_language_instruction(code: str | None) -> str:
    normalized = normalize_language(code)
    name = language_name(normalized)
    if normalized == "en":
        return "Respond in English unless the user explicitly asks for another language."
    return (
        f"Respond in {name}. Keep document titles, proper nouns, citations, numbers, "
        "and legal/technical terms exact when translating them would reduce precision."
    )
