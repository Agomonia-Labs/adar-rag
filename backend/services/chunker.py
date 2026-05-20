# services/chunker.py
from __future__ import annotations
import os, re
from dataclasses import dataclass, field

CHUNK_SIZE    = int(os.getenv("CHUNK_SIZE",    "350"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "60"))
MIN_CHARS     = 30


@dataclass
class Chunk:
    text:     str
    index:    int
    total:    int
    # document-level metadata attached to every chunk
    doc_meta: dict = field(default_factory=dict)

    @property
    def word_count(self) -> int:
        return len(self.text.split())

    @property
    def char_count(self) -> int:
        return len(self.text)

    def to_metadata(self) -> dict:
        """Stored alongside the chunk in GCS _metadata.json and pgvector."""
        return {
            "chunk_index": self.index,
            "chunk_total": self.total,
            "word_count":  self.word_count,
            "char_count":  self.char_count,
            **self.doc_meta,
        }


def chunk_text(
    text: str,
    doc_meta: dict | None = None,
    chunk_size:    int = CHUNK_SIZE,
    chunk_overlap: int = CHUNK_OVERLAP,
) -> list[Chunk]:
    """
    Split `text` into overlapping word windows.
    `doc_meta` is attached to every chunk for traceability.
    """
    if not text or not text.strip():
        return []

    # Normalise whitespace
    cleaned = re.sub(r"[ \t]{2,}", " ", text.replace("\r\n", "\n")).strip()
    words   = cleaned.split()
    if not words:
        return []

    stride = max(1, chunk_size - chunk_overlap)
    slices: list[str] = []
    i = 0
    while i < len(words):
        s = " ".join(words[i : i + chunk_size])
        if len(s) >= MIN_CHARS:
            slices.append(s)
        i += stride

    total    = len(slices)
    meta     = doc_meta or {}
    return [
        Chunk(text=s, index=idx, total=total, doc_meta=meta)
        for idx, s in enumerate(slices)
    ]
