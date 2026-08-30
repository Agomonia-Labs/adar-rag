from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any

from services.chunker import Chunk
from services.conversation_assistant import json_object_value
from services.llm import embed
from services.notifications import send_call_processing_notification
from services.pii import redact_text
from services.text_safety import sanitize_text_for_storage
from services.tracing import trace_span
from services.vectordb import delete_document_vectors, store_chunk
import services.storage as gcs

log = logging.getLogger("docintel.telephony")


def gcs_uri_for_path(path: str) -> str:
    if path.startswith("gs://"):
        return path
    return f"gs://{gcs.GCS_BUCKET}/{path.lstrip('/')}"


def path_from_gcs_uri(uri: str) -> str:
    prefix = f"gs://{gcs.GCS_BUCKET}/"
    if not uri.startswith(prefix):
        raise ValueError(f"Recording must be stored in the configured GCS bucket {gcs.GCS_BUCKET}")
    return uri[len(prefix):]


def _seconds(duration: Any) -> float:
    if duration is None:
        return 0.0
    if hasattr(duration, "total_seconds"):
        return float(duration.total_seconds())
    return float(duration)


def parse_transcript_override(text: str) -> list[dict]:
    """Parse test/manual transcripts while preserving optional speaker labels."""
    lines = [line.strip() for line in sanitize_text_for_storage(text).splitlines() if line.strip()]
    segments = []
    cursor = 0.0
    for index, line in enumerate(lines):
        match = re.match(r"^(?:\[(\d+(?:\.\d+)?)\s*[-:]\s*(\d+(?:\.\d+)?)\]\s*)?([^:]{1,40}):\s*(.+)$", line)
        if match:
            start = float(match.group(1) or cursor)
            end = float(match.group(2) or (start + max(2, len(match.group(4).split()) / 2.4)))
            speaker, content = match.group(3).strip(), match.group(4).strip()
        else:
            start = cursor
            end = start + max(2, len(line.split()) / 2.4)
            speaker, content = "speaker_1", line
        segments.append({"segment_index": index, "speaker": speaker, "start_seconds": start,
                         "end_seconds": end, "transcript": content, "confidence": 1.0})
        cursor = end
    return segments


def _recognize_gcs_sync(uri: str, language_code: str, mime_type: str) -> list[dict]:
    from google.cloud import speech_v1 as speech

    client = speech.SpeechClient()
    encoding = speech.RecognitionConfig.AudioEncoding.ENCODING_UNSPECIFIED
    lowered = (mime_type or "").lower()
    if "flac" in lowered:
        encoding = speech.RecognitionConfig.AudioEncoding.FLAC
    elif "mp3" in lowered or "mpeg" in lowered:
        encoding = speech.RecognitionConfig.AudioEncoding.MP3

    config = speech.RecognitionConfig(
        encoding=encoding,
        language_code=language_code or "en-US",
        enable_automatic_punctuation=True,
        enable_word_time_offsets=True,
        diarization_config=speech.SpeakerDiarizationConfig(
            enable_speaker_diarization=True,
            min_speaker_count=2,
            max_speaker_count=8,
        ),
        model=os.getenv("TELEPHONY_SPEECH_MODEL", "telephony"),
        use_enhanced=True,
    )
    operation = client.long_running_recognize(config=config, audio=speech.RecognitionAudio(uri=uri))
    response = operation.result(timeout=int(os.getenv("TELEPHONY_TRANSCRIPTION_TIMEOUT_SECONDS", "3600")))
    words = []
    confidence = None
    for result in response.results:
        if not result.alternatives:
            continue
        alternative = result.alternatives[0]
        confidence = float(alternative.confidence or 0)
        words.extend(alternative.words)
    if not words:
        return []

    turns, current = [], None
    for word in words:
        tag = int(getattr(word, "speaker_tag", 0) or 1)
        if current is None or current["speaker"] != f"speaker_{tag}":
            if current:
                turns.append(current)
            current = {"speaker": f"speaker_{tag}", "start_seconds": _seconds(word.start_time),
                       "end_seconds": _seconds(word.end_time), "words": [word.word]}
        else:
            current["words"].append(word.word)
            current["end_seconds"] = _seconds(word.end_time)
    if current:
        turns.append(current)
    return [{"segment_index": i, "speaker": turn["speaker"], "start_seconds": turn["start_seconds"],
             "end_seconds": turn["end_seconds"], "transcript": " ".join(turn.pop("words")),
             "confidence": confidence} for i, turn in enumerate(turns)]


async def _set_progress(call_id: str, status: str, step: str, pct: int, error: str | None = None) -> None:
    from database.connection import get_pool
    async with get_pool().acquire() as db:
        await db.execute(
            """UPDATE telephony_calls SET processing_status=$2, processing_step=$3,
               progress_pct=$4, error_message=$5, updated_at=NOW() WHERE id=$1""",
            call_id, status, step, pct, error,
        )


def _summary(segments: list[dict]) -> dict:
    speakers = sorted({s["speaker"] for s in segments})
    transcript = " ".join(s["transcript"] for s in segments)
    sentences = re.split(r"(?<=[.!?])\s+", transcript)
    return {
        "overview": " ".join(sentences[:4])[:1600],
        "key_points": [sentence[:300] for sentence in sentences[:8] if sentence.strip()],
        "speakers": speakers,
        "segment_count": len(segments),
    }


@trace_span("telephony.process_completed_call")
async def process_call(call_id: str, transcript_override: str | None = None, redact_pii: bool = True) -> None:
    from database.connection import get_pool
    pool = get_pool()
    try:
        async with pool.acquire() as db:
            row = await db.fetchrow(
                """SELECT tc.*, d.original_name, u.email FROM telephony_calls tc
                   JOIN documents d ON d.id=tc.document_id JOIN users u ON u.id=tc.user_id
                   WHERE tc.id=$1""", call_id,
            )
        if not row:
            raise ValueError("Call not found")
        call = dict(row)
        source_type = "conversation_assistant" if call.get("source_channel") == "in_app" else "telephony"
        await _set_progress(call_id, "processing", "transcribing", 20)

        if transcript_override and transcript_override.strip():
            segments = parse_transcript_override(transcript_override)
        else:
            import asyncio
            if not call.get("recording_gcs_uri"):
                raise ValueError("recording_gcs_uri is required when no transcript is supplied")
            segments = await asyncio.to_thread(
                _recognize_gcs_sync, call["recording_gcs_uri"], call["language_code"], call["recording_mime_type"]
            )
        if not segments:
            raise ValueError("Speech-to-Text returned no transcript segments")

        redaction_total = 0
        for segment in segments:
            result = redact_text(segment["transcript"], enabled=redact_pii)
            segment["transcript"] = result.text
            redaction_total += result.total

        await _set_progress(call_id, "processing", "saving_transcript", 45)
        async with pool.acquire() as db:
            async with db.transaction():
                await db.execute("DELETE FROM telephony_segments WHERE call_id=$1", call_id)
                for segment in segments:
                    await db.execute(
                        """INSERT INTO telephony_segments
                           (call_id,segment_index,speaker,start_seconds,end_seconds,transcript,confidence,metadata)
                           VALUES($1,$2,$3,$4,$5,$6,$7,$8::jsonb)""",
                        call_id, segment["segment_index"], segment["speaker"], segment["start_seconds"],
                        segment["end_seconds"], segment["transcript"], segment.get("confidence"),
                        json.dumps({"pii_redacted": redact_pii}),
                    )

        doc_id, user_id = str(call["document_id"]), str(call["user_id"])
        chunks = [Chunk(
            text=f"[{s['start_seconds']:.2f}-{s['end_seconds']:.2f}] {s['speaker']}: {s['transcript']}",
            index=i, total=len(segments),
            doc_meta={"source_type": source_type, "call_id": call_id, "speaker": s["speaker"],
                      "start_seconds": s["start_seconds"], "end_seconds": s["end_seconds"]},
        ) for i, s in enumerate(segments)]
        for chunk in chunks:
            await gcs.upload_text(gcs.chunk_path(user_id, doc_id, chunk.index), chunk.text)
        await gcs.upload_json(gcs.metadata_path(user_id, doc_id), {
            "document": {"id": doc_id, "source_type": source_type, "call_id": call_id,
                         "total_chunks": len(chunks), "pii_redactions": redaction_total},
            "chunks": [{"index": c.index, "gcs_path": gcs.chunk_path(user_id, doc_id, c.index),
                        **c.to_metadata()} for c in chunks],
        })

        await _set_progress(call_id, "processing", "embedding", 70)
        await delete_document_vectors(doc_id)
        for chunk in chunks:
            await store_chunk(document_id=doc_id, user_id=user_id,
                              workspace_id=str(call["workspace_id"]) if call.get("workspace_id") else None,
                              chunk_index=chunk.index, chunk_total=len(chunks), content=chunk.text,
                              embedding=await embed(chunk.text), chunk_metadata=chunk.to_metadata())

        summary = _summary(segments)
        session_state = json_object_value(call.get("session_state"))
        summary["collected_fields"] = json_object_value(session_state.get("collected_fields"))
        summary["missing_required_fields"] = list(session_state.get("missing_required_fields") or [])
        async with pool.acquire() as db:
            await db.execute(
                """UPDATE telephony_calls SET processing_status='completed', processing_step='completed',
                   progress_pct=100, summary=$2::jsonb, duration_seconds=$3, processed_at=NOW(),
                   error_message=NULL, updated_at=NOW() WHERE id=$1""",
                call_id, json.dumps(summary), max(s["end_seconds"] for s in segments),
            )
            await db.execute(
                """UPDATE documents SET status='embedded', chunk_count=$2, doc_type='call_transcript',
                   doc_domain='conversation', doc_language=$3, error_message=NULL,
                   doc_metadata=doc_metadata || $4::jsonb, updated_at=NOW() WHERE id=$1""",
                doc_id, len(chunks), call["language_code"].split("-")[0],
                json.dumps({"conversation_id": call_id, "source_type": source_type,
                            "review_status": call.get("review_status") or "draft"}),
            )
        await send_call_processing_notification(call["email"], call_name=call["original_name"],
                                                status="completed", segment_count=len(segments))
    except Exception as exc:
        log.exception("Telephony processing failed call=%s", call_id)
        await _set_progress(call_id, "error", "failed", 0, str(exc)[:2000])
        try:
            async with pool.acquire() as db:
                await db.execute(
                    """UPDATE documents d SET status='error',error_message=$2,updated_at=NOW()
                       FROM telephony_calls tc WHERE tc.id=$1 AND d.id=tc.document_id""",
                    call_id, str(exc)[:2000],
                )
                row = await db.fetchrow("SELECT u.email,d.original_name FROM telephony_calls tc JOIN users u ON u.id=tc.user_id JOIN documents d ON d.id=tc.document_id WHERE tc.id=$1", call_id)
            if row:
                await send_call_processing_notification(row["email"], call_name=row["original_name"], status="failed", error_message=str(exc))
        except Exception:
            log.exception("Could not send telephony failure notification")
