from services.chunker import chunk_text
from services.text_safety import sanitize_text_for_storage


def test_sanitize_text_for_storage_removes_nul_and_controls():
    assert sanitize_text_for_storage("abc\x00def\x01ghi\nok\tok") == "abcdefghi\nok\tok"


def test_chunk_text_removes_nul_before_storage():
    chunks = chunk_text("Lease\x00 agreement " * 20)

    assert chunks
    assert all("\x00" not in chunk.text for chunk in chunks)
