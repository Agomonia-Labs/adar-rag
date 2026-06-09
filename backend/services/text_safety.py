
from __future__ import annotations

import re

# PostgreSQL text cannot contain NUL. Other non-whitespace C0 controls are also
# poor search/index content, so strip them while preserving tabs and newlines.
_UNSAFE_TEXT_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_text_for_storage(value: str | None) -> str:
    if value is None:
        return ""
    return _UNSAFE_TEXT_CHARS.sub("", str(value))
