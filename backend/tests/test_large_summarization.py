import json

import pytest

from routes import summarize


def _events(raw_events):
    return [json.loads(event.removeprefix("data: ").strip()) for event in raw_events]


def test_group_texts_by_chars_is_bounded_and_ordered():
    source = ["A" * 9, "B" * 17, "C" * 6]

    groups = summarize._group_texts_by_chars(source, 10)

    assert all(len(group) <= 10 for group in groups)
    assert "".join(groups).replace("\n", "") == "".join(source)


@pytest.mark.asyncio
async def test_large_summary_maps_sections_then_creates_final(monkeypatch):
    monkeypatch.setattr(summarize, "DIRECT_CHAR_LIMIT", 1_000)
    monkeypatch.setattr(summarize, "MAP_CHAR_LIMIT", 800)
    monkeypatch.setattr(summarize, "SUMMARY_CONCURRENCY", 2)

    calls = []

    async def fake_chat(messages, _system, on_token):
        prompt = messages[0]["content"]
        calls.append(prompt)
        if "source section thoroughly" in prompt:
            await on_token("M" * 400)
        elif "Consolidate these partial summaries" in prompt:
            await on_token("R" * 50)
        else:
            await on_token("final summary")

    monkeypatch.setattr(summarize, "chat_stream", fake_chat)

    raw = []
    async for event in summarize._stream_summary(
        ["A" * 700, "B" * 700, "C" * 700, "D" * 700],
        "executive",
        "",
        "large.pdf",
    ):
        raw.append(event)

    events = _events(raw)
    assert any(event.get("stage") == "map" for event in events)
    assert any(event.get("stage") == "reduce" for event in events)
    assert any(event.get("stage") == "final" for event in events)
    assert events[-1]["type"] == "done"
    assert "".join(event.get("text", "") for event in events) == "final summary"
    assert len(calls) > 5


@pytest.mark.asyncio
async def test_summary_model_call_retries_transient_failure(monkeypatch):
    monkeypatch.setattr(summarize, "SUMMARY_RETRIES", 2)
    monkeypatch.setattr(summarize, "SUMMARY_RETRY_BASE_SECONDS", 0)
    calls = 0

    async def flaky_chat(_messages, _system, on_token):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("temporary model failure")
        await on_token("recovered")

    monkeypatch.setattr(summarize, "chat_stream", flaky_chat)

    result = await summarize._generate_text_with_retries([], "system")

    assert result == "recovered"
    assert calls == 2


@pytest.mark.asyncio
async def test_completed_summary_batch_is_reused_from_cache(monkeypatch):
    objects = {}
    calls = 0

    async def metadata(path):
        return {"name": path} if path in objects else None

    async def download(path):
        return objects[path]

    async def upload(path, text):
        objects[path] = text

    async def fake_chat(_messages, _system, on_token):
        nonlocal calls
        calls += 1
        await on_token("cached summary")

    monkeypatch.setattr(summarize.gcs, "blob_metadata", metadata)
    monkeypatch.setattr(summarize.gcs, "download_text", download)
    monkeypatch.setattr(summarize.gcs, "upload_text", upload)
    monkeypatch.setattr(summarize, "chat_stream", fake_chat)

    first = await summarize._cached_or_generate(
        cache_path="summaries/level-0/batch-000000.txt",
        messages=[],
        system="system",
    )
    second = await summarize._cached_or_generate(
        cache_path="summaries/level-0/batch-000000.txt",
        messages=[],
        system="system",
    )

    assert first == second == "cached summary"
    assert calls == 1
