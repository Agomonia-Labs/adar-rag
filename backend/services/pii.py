from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class PiiMatch:
    kind: str
    value: str
    start: int
    end: int


@dataclass(frozen=True)
class RedactionResult:
    text: str
    counts: dict[str, int]
    total: int


PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("email", re.compile(r"\b[A-Z0-9._%+-]+\s*@\s*[A-Z0-9.-]+\s*\.\s*[A-Z]{2,}\b", re.I)),
    ("ssn", re.compile(r"\b\d{3}\s*[- ]\s*\d{2}\s*[- ]\s*\d{4}\b")),
    ("phone", re.compile(r"(?<!\w)(?:\+?\s*1[\s.-]*)?(?:\(\s*\d{3}\s*\)|\d{3})[\s.-]*\d{3}[\s.-]*\d{4}(?!\w)")),
    ("credit_card", re.compile(r"(?<!\d)(?:\d[\s-]*?){13,19}(?!\d)")),
    ("bank_account", re.compile(r"\b(?:account|acct|routing|iban)\s*(?:#|number|no\.?)?\s*[:\-]?\s*[A-Z0-9][A-Z0-9\s-]{7,33}\b", re.I)),
    ("ip_address", re.compile(r"\b\d{1,3}\s*\.\s*\d{1,3}\s*\.\s*\d{1,3}\s*\.\s*\d{1,3}\b")),
    ("dob", re.compile(r"\b(?:DOB|D\s*\.?\s*O\s*\.?\s*B\s*\.?|date\s+of\s+birth)\s*[:\-]?\s*(?:\d{1,2}\s*[/-]\s*\d{1,2}\s*[/-]\s*\d{2,4}|[A-Z][a-z]+\s+\d{1,2}\s*,\s*\d{4})\b", re.I)),
)


def redact_text(text: str | None, enabled: bool = True) -> RedactionResult:
    if not text:
        return RedactionResult(text or "", {}, 0)
    if not enabled:
        return RedactionResult(text, {}, 0)

    matches = list(_find_matches(text))
    if not matches:
        return RedactionResult(text, {}, 0)

    counts: dict[str, int] = {}
    out: list[str] = []
    cursor = 0
    for match in matches:
        if match.start < cursor:
            continue
        out.append(text[cursor:match.start])
        out.append(f"[REDACTED_{match.kind.upper()}]")
        counts[match.kind] = counts.get(match.kind, 0) + 1
        cursor = match.end
    out.append(text[cursor:])
    total = sum(counts.values())
    return RedactionResult("".join(out), counts, total)


def redact_obj(value):
    if isinstance(value, str):
        return redact_text(value).text
    if isinstance(value, list):
        return [redact_obj(v) for v in value]
    if isinstance(value, dict):
        return {k: redact_obj(v) for k, v in value.items()}
    return value


def _find_matches(text: str) -> Iterable[PiiMatch]:
    raw: list[PiiMatch] = []
    for kind, pattern in PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(0)
            if kind == "credit_card" and not _looks_like_card(value):
                continue
            if kind == "ip_address" and not _valid_ipv4(value):
                continue
            raw.append(PiiMatch(kind, value, match.start(), match.end()))

    raw.sort(key=lambda m: (m.start, -(m.end - m.start)))
    selected: list[PiiMatch] = []
    last_end = -1
    for match in raw:
        if match.start >= last_end:
            selected.append(match)
            last_end = match.end
    return selected


def _looks_like_card(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    if not 13 <= len(digits) <= 19:
        return False
    return _luhn_valid(digits)


def _luhn_valid(digits: str) -> bool:
    total = 0
    reverse = digits[::-1]
    for i, char in enumerate(reverse):
        n = int(char)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _valid_ipv4(value: str) -> bool:
    try:
        return all(0 <= int(part.strip()) <= 255 for part in value.split("."))
    except ValueError:
        return False
