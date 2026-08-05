from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from database.connection import get_pool
from services import storage as gcs
from services.text_safety import sanitize_text_for_storage
from services.vectordb import delete_document_vectors, store_chunk

log = logging.getLogger("docintel.video")

DEFAULT_MAX_FRAMES = int(os.getenv("VIDEO_MAX_FRAMES", "12"))
DEFAULT_SEGMENT_SECONDS = int(os.getenv("VIDEO_SEGMENT_SECONDS", "60"))
FRAME_CAPTION_ENABLED = os.getenv("VIDEO_FRAME_CAPTION_ENABLED", "true").lower() != "false"
TRANSCRIBE_AUDIO_ENABLED = os.getenv("VIDEO_TRANSCRIBE_AUDIO_ENABLED", "true").lower() != "false"


def is_video_file(filename: str = "", file_type: str = "", content_type: str = "") -> bool:
    ext = Path(filename or "").suffix.lower().lstrip(".")
    return file_type == "video" or content_type.startswith("video/") or ext in {"mp4", "mov", "m4v", "avi", "mkv", "webm"}


async def process_video_document(
    *,
    document_id: str,
    user_id: str,
    workspace_id: str | None,
    source_gcs_path: str,
    filename: str,
    rights_confirmed: bool = False,
    source_type: str = "upload",
    source_url: str | None = None,
    max_frames: int = DEFAULT_MAX_FRAMES,
    segment_seconds: int = DEFAULT_SEGMENT_SECONDS,
    embed_after_processing: bool = True,
) -> dict[str, Any]:
    pool = get_pool()
    job_id = str(uuid4())
    video_id: str | None = None

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO video_processing_jobs
              (id, document_id, user_id, workspace_id, job_type, status, input_data, started_at)
            VALUES ($1,$2,$3,$4,'process_video','running',$5::jsonb,NOW())
            """,
            job_id,
            document_id,
            user_id,
            workspace_id,
            json.dumps({
                "filename": filename,
                "source_type": source_type,
                "source_url": source_url,
                "max_frames": max_frames,
                "segment_seconds": segment_seconds,
                "embed_after_processing": embed_after_processing,
            }),
        )
        await conn.execute(
            "UPDATE documents SET status='chunking', error_message=NULL, updated_at=NOW() WHERE id=$1",
            document_id,
        )

    tmp_video = None
    frame_paths: list[tuple[int, float, str]] = []
    try:
        suffix = Path(filename or "video.mp4").suffix or ".mp4"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp.write(await gcs.download_bytes(source_gcs_path))
        tmp.close()
        tmp_video = tmp.name

        metadata = await asyncio.to_thread(_probe_video, tmp_video)
        duration = float(metadata.get("duration_seconds") or 0)
        frames_dir = f"users/{user_id}/documents/{document_id}/video/frames/"
        clips_dir = f"users/{user_id}/documents/{document_id}/video/clips/"

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO video_documents
                  (document_id, user_id, workspace_id, source_type, source_url, rights_confirmed,
                   duration_seconds, fps, width, height, codec, audio_codec, bitrate, frame_count,
                   gcs_video_path, gcs_frames_dir, gcs_clips_dir, processing_status, metadata)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,'running',$18::jsonb)
                ON CONFLICT (document_id) DO UPDATE SET
                   user_id = EXCLUDED.user_id,
                   workspace_id = EXCLUDED.workspace_id,
                   source_type = EXCLUDED.source_type,
                   source_url = EXCLUDED.source_url,
                   rights_confirmed = EXCLUDED.rights_confirmed,
                   duration_seconds = EXCLUDED.duration_seconds,
                   fps = EXCLUDED.fps,
                   width = EXCLUDED.width,
                   height = EXCLUDED.height,
                   codec = EXCLUDED.codec,
                   audio_codec = EXCLUDED.audio_codec,
                   bitrate = EXCLUDED.bitrate,
                   frame_count = EXCLUDED.frame_count,
                   gcs_video_path = EXCLUDED.gcs_video_path,
                   gcs_frames_dir = EXCLUDED.gcs_frames_dir,
                   gcs_clips_dir = EXCLUDED.gcs_clips_dir,
                   processing_status = 'running',
                   error_message = NULL,
                   metadata = EXCLUDED.metadata,
                   updated_at = NOW()
                RETURNING id
                """,
                document_id,
                user_id,
                workspace_id,
                source_type,
                source_url,
                rights_confirmed,
                metadata.get("duration_seconds"),
                metadata.get("fps"),
                metadata.get("width"),
                metadata.get("height"),
                metadata.get("codec"),
                metadata.get("audio_codec"),
                metadata.get("bitrate"),
                metadata.get("frame_count"),
                source_gcs_path,
                frames_dir,
                clips_dir,
                json.dumps(metadata),
            )
            video_id = str(row["id"])
            await conn.execute(
                "UPDATE video_processing_jobs SET video_document_id=$1 WHERE id=$2",
                video_id,
                job_id,
            )

        transcript = await _transcribe_audio(tmp_video) if TRANSCRIBE_AUDIO_ENABLED else ""
        frame_paths = await asyncio.to_thread(_sample_frames, tmp_video, duration, max_frames)
        frames = []
        for frame_index, timestamp, path in frame_paths:
            frame_gcs_path = f"{frames_dir}frame_{frame_index:04d}_{int(timestamp):06d}s.jpg"
            await gcs.upload_bytes(frame_gcs_path, Path(path).read_bytes(), "image/jpeg")
            caption = await _caption_frame(path, timestamp) if FRAME_CAPTION_ENABLED else ""
            frames.append({
                "frame_index": frame_index,
                "timestamp_seconds": timestamp,
                "frame_path": frame_gcs_path,
                "caption": caption,
            })

        segments = _build_segments(duration, segment_seconds, transcript, frames)
        chunks = _build_video_chunks(document_id, user_id, filename, metadata, segments, frames, transcript)

        await _persist_video_artifacts(
            document_id=document_id,
            user_id=user_id,
            workspace_id=workspace_id,
            video_id=video_id,
            frames=frames,
            segments=segments,
            chunks=chunks,
        )

        embed_status = "not_requested"
        embed_error = ""
        if embed_after_processing:
            try:
                await _embed_generated_chunks(document_id, user_id, workspace_id, chunks)
                embed_status = "embedded"
                document_status = "embedded"
            except Exception as exc:
                embed_status = "failed"
                embed_error = str(exc)[:500]
                document_status = "chunked"
        else:
            document_status = "chunked"

        output = {
            "video_id": video_id,
            "duration_seconds": duration,
            "frame_count": len(frames),
            "segment_count": len(segments),
            "chunk_count": len(chunks),
            "embed_status": embed_status,
            "embed_error": embed_error,
        }
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE video_documents
                   SET processing_status='ready', error_message=NULL, updated_at=NOW()
                 WHERE id=$1
                """,
                video_id,
            )
            await conn.execute(
                """
                UPDATE video_processing_jobs
                   SET status='completed', output_data=$1::jsonb, completed_at=NOW(), updated_at=NOW()
                 WHERE id=$2
                """,
                json.dumps(output),
                job_id,
            )
            await conn.execute(
                """
                UPDATE documents
                   SET status=$1, chunk_count=$2, doc_type='video', doc_domain='general',
                       doc_metadata=COALESCE(doc_metadata, '{}'::jsonb) || $3::jsonb,
                       updated_at=NOW()
                 WHERE id=$4
                """,
                document_status,
                len(chunks),
                json.dumps({"video_intelligence": output}),
                document_id,
            )
        return output

    except Exception as exc:
        error = str(exc)[:500]
        log.exception("Video processing failed for %s: %s", document_id, error)
        async with pool.acquire() as conn:
            if video_id:
                await conn.execute(
                    "UPDATE video_documents SET processing_status='error', error_message=$1, updated_at=NOW() WHERE id=$2",
                    error,
                    video_id,
                )
            await conn.execute(
                """
                UPDATE video_processing_jobs
                   SET status='error', error_message=$1, completed_at=NOW(), updated_at=NOW()
                 WHERE id=$2
                """,
                error,
                job_id,
            )
            await conn.execute(
                "UPDATE documents SET status='error', error_message=$1, updated_at=NOW() WHERE id=$2",
                error,
                document_id,
            )
        raise
    finally:
        if tmp_video:
            try:
                os.unlink(tmp_video)
            except OSError:
                pass
        for _, _, path in frame_paths:
            try:
                os.unlink(path)
            except OSError:
                pass


def _probe_video(path: str) -> dict[str, Any]:
    _require_binary("ffprobe")
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        path,
    ]
    data = json.loads(subprocess.check_output(cmd, text=True))
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    fmt = data.get("format") or {}
    fps = _parse_fps(video.get("avg_frame_rate") or video.get("r_frame_rate"))
    duration = _num(video.get("duration")) or _num(fmt.get("duration")) or 0
    return {
        "duration_seconds": duration,
        "fps": fps,
        "width": _int(video.get("width")),
        "height": _int(video.get("height")),
        "codec": video.get("codec_name") or "",
        "audio_codec": audio.get("codec_name") or "",
        "bitrate": _int(fmt.get("bit_rate")),
        "frame_count": _int(video.get("nb_frames")),
        "format_name": fmt.get("format_name") or "",
    }


def _sample_frames(path: str, duration: float, max_frames: int) -> list[tuple[int, float, str]]:
    _require_binary("ffmpeg")
    if duration <= 0:
        timestamps = [0.5]
    else:
        count = max(1, min(max_frames, max(1, math.ceil(duration / DEFAULT_SEGMENT_SECONDS))))
        step = duration / (count + 1)
        timestamps = [max(0.1, round(step * (i + 1), 2)) for i in range(count)]

    frames = []
    for idx, timestamp in enumerate(timestamps):
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
        tmp.close()
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            str(timestamp),
            "-i",
            path,
            "-frames:v",
            "1",
            "-q:v",
            "3",
            tmp.name,
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        frames.append((idx, timestamp, tmp.name))
    return frames


async def _caption_frame(path: str, timestamp: float) -> str:
    try:
        from services.llm import vision_extract

        raw = await vision_extract(path, "image/jpeg")
        return sanitize_text_for_storage(raw).strip()[:1200]
    except Exception as exc:
        log.warning("Frame caption skipped at %.2fs: %s", timestamp, exc)
        return ""


async def _transcribe_audio(path: str) -> str:
    if os.getenv("LLM_PROVIDER", "openai").lower() != "openai":
        return ""
    if not os.getenv("OPENAI_API_KEY"):
        return ""
    _require_binary("ffmpeg")
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
    tmp.close()
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", path, "-vn", "-acodec", "libmp3lame", "-ar", "16000", "-ac", "1", tmp.name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        with open(tmp.name, "rb") as audio:
            result = await client.audio.transcriptions.create(
                model=os.getenv("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe"),
                file=audio,
            )
        return sanitize_text_for_storage(getattr(result, "text", "") or "")
    except Exception as exc:
        log.warning("Audio transcription skipped: %s", exc)
        return ""
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def _build_segments(duration: float, segment_seconds: int, transcript: str, frames: list[dict]) -> list[dict]:
    segment_seconds = max(15, int(segment_seconds or DEFAULT_SEGMENT_SECONDS))
    if duration <= 0:
        duration = float(segment_seconds)
    count = max(1, math.ceil(duration / segment_seconds))
    transcript_parts = _split_text_evenly(transcript, count)
    segments = []
    for idx in range(count):
        start = float(idx * segment_seconds)
        end = float(min(duration, (idx + 1) * segment_seconds))
        segment_frames = [f for f in frames if start <= float(f["timestamp_seconds"]) <= end]
        captions = [f.get("caption", "") for f in segment_frames if f.get("caption")]
        summary = " ".join(captions).strip()
        if not summary and transcript_parts[idx].strip():
            summary = transcript_parts[idx].strip()
        if not summary:
            summary = f"Video segment from {_fmt_time(start)} to {_fmt_time(end)}."
        segments.append({
            "segment_index": idx,
            "start_seconds": start,
            "end_seconds": end,
            "segment_type": "timeline",
            "title": f"{_fmt_time(start)} - {_fmt_time(end)}",
            "summary": summary[:2500],
            "transcript": transcript_parts[idx].strip(),
            "ocr_text": "",
            "thumbnail_path": segment_frames[0]["frame_path"] if segment_frames else None,
            "confidence": 0.75 if summary else 0.4,
            "metadata": {"frame_indices": [f["frame_index"] for f in segment_frames]},
        })
    return segments


def _build_video_chunks(document_id: str, user_id: str, filename: str, metadata: dict, segments: list[dict], frames: list[dict], transcript: str) -> list[dict]:
    chunks = []
    overview = (
        f"Video: {filename}\n"
        f"Duration: {_fmt_time(float(metadata.get('duration_seconds') or 0))}\n"
        f"Resolution: {metadata.get('width') or 'unknown'}x{metadata.get('height') or 'unknown'}\n"
        f"Codec: {metadata.get('codec') or 'unknown'}; audio codec: {metadata.get('audio_codec') or 'unknown'}\n"
        f"Transcript available: {'yes' if transcript.strip() else 'no'}\n"
        f"Sampled frames: {len(frames)}"
    )
    chunks.append(_chunk(0, document_id, user_id, filename, "video_overview", 0, float(metadata.get("duration_seconds") or 0), overview))
    for seg in segments:
        text = (
            f"Video segment {seg['segment_index'] + 1}: {seg['title']}\n"
            f"Time range: {_fmt_time(seg['start_seconds'])} to {_fmt_time(seg['end_seconds'])}\n"
            f"Summary: {seg.get('summary') or ''}\n"
            f"Transcript: {seg.get('transcript') or ''}\n"
            f"OCR text: {seg.get('ocr_text') or ''}"
        )
        chunks.append(_chunk(
            len(chunks),
            document_id,
            user_id,
            filename,
            "video_segment",
            seg["start_seconds"],
            seg["end_seconds"],
            text,
            extra={"segment_index": seg["segment_index"], "thumbnail_path": seg.get("thumbnail_path")},
        ))
    for frame in frames:
        if not frame.get("caption"):
            continue
        timestamp = float(frame["timestamp_seconds"])
        text = (
            f"Video frame at {_fmt_time(timestamp)}\n"
            f"Visual description: {frame.get('caption') or ''}\n"
            f"Frame path: {frame.get('frame_path') or ''}"
        )
        chunks.append(_chunk(
            len(chunks),
            document_id,
            user_id,
            filename,
            "video_frame",
            timestamp,
            timestamp,
            text,
            extra={"frame_index": frame["frame_index"], "frame_path": frame["frame_path"]},
        ))
    return chunks


async def _persist_video_artifacts(
    *,
    document_id: str,
    user_id: str,
    workspace_id: str | None,
    video_id: str,
    frames: list[dict],
    segments: list[dict],
    chunks: list[dict],
) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM video_frames WHERE video_document_id=$1", video_id)
        await conn.execute("DELETE FROM video_segments WHERE video_document_id=$1", video_id)
        await conn.execute("DELETE FROM video_transcript_chunks WHERE video_document_id=$1", video_id)
        await conn.execute("DELETE FROM video_events WHERE video_document_id=$1", video_id)

        segment_ids: dict[int, str] = {}
        for seg in segments:
            row = await conn.fetchrow(
                """
                INSERT INTO video_segments
                  (document_id, video_document_id, segment_index, start_seconds, end_seconds,
                   segment_type, title, summary, transcript, ocr_text, thumbnail_path, confidence, metadata)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::jsonb)
                RETURNING id
                """,
                document_id,
                video_id,
                seg["segment_index"],
                seg["start_seconds"],
                seg["end_seconds"],
                seg["segment_type"],
                seg["title"],
                seg["summary"],
                seg["transcript"],
                seg["ocr_text"],
                seg.get("thumbnail_path"),
                seg.get("confidence"),
                json.dumps(seg.get("metadata") or {}),
            )
            segment_ids[seg["segment_index"]] = str(row["id"])
            if seg.get("transcript"):
                await conn.execute(
                    """
                    INSERT INTO video_transcript_chunks
                      (document_id, video_document_id, segment_id, chunk_index, start_seconds, end_seconds, transcript)
                    VALUES ($1,$2,$3,$4,$5,$6,$7)
                    """,
                    document_id,
                    video_id,
                    row["id"],
                    seg["segment_index"],
                    seg["start_seconds"],
                    seg["end_seconds"],
                    seg["transcript"],
                )

        for frame in frames:
            segment_id = _segment_for_timestamp(segment_ids, segments, float(frame["timestamp_seconds"]))
            await conn.execute(
                """
                INSERT INTO video_frames
                  (document_id, video_document_id, segment_id, frame_index, timestamp_seconds,
                   frame_path, thumbnail_path, caption, ocr_text, metadata)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'',$9::jsonb)
                """,
                document_id,
                video_id,
                segment_id,
                frame["frame_index"],
                frame["timestamp_seconds"],
                frame["frame_path"],
                frame["frame_path"],
                frame.get("caption") or "",
                json.dumps({}),
            )

    for chunk in chunks:
        await gcs.upload_text(gcs.chunk_path(user_id, document_id, chunk["chunk_index"]), chunk["content"])

    meta_obj = {
        "document": {
            "id": document_id,
            "user_id": user_id,
            "filename": chunks[0]["chunk_metadata"].get("filename") if chunks else "",
            "file_type": "video",
            "total_chunks": len(chunks),
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        "chunks": [
            {
                "index": c["chunk_index"],
                "word_count": len(c["content"].split()),
                "char_count": len(c["content"]),
                "gcs_path": gcs.chunk_path(user_id, document_id, c["chunk_index"]),
                **c["chunk_metadata"],
            }
            for c in chunks
        ],
    }
    await gcs.upload_json(gcs.metadata_path(user_id, document_id), meta_obj)


async def _embed_generated_chunks(document_id: str, user_id: str, workspace_id: str | None, chunks: list[dict]) -> None:
    from services.llm import embed

    await delete_document_vectors(document_id)
    for chunk in chunks:
        content = sanitize_text_for_storage(chunk["content"])
        vector = await embed(content)
        await store_chunk(
            document_id=document_id,
            user_id=user_id,
            workspace_id=workspace_id,
            chunk_index=chunk["chunk_index"],
            chunk_total=len(chunks),
            content=content,
            embedding=vector,
            chunk_metadata=chunk["chunk_metadata"],
        )
        await asyncio.sleep(0.08)


def _chunk(index: int, document_id: str, user_id: str, filename: str, chunk_type: str, start: float, end: float, content: str, extra: dict | None = None) -> dict:
    metadata = {
        "document_id": document_id,
        "user_id": user_id,
        "filename": filename,
        "file_type": "video",
        "chunk_type": chunk_type,
        "start_seconds": start,
        "end_seconds": end,
        "start_time": _fmt_time(start),
        "end_time": _fmt_time(end),
    }
    metadata.update(extra or {})
    return {
        "chunk_index": index,
        "content": sanitize_text_for_storage(content),
        "chunk_metadata": metadata,
    }


def _segment_for_timestamp(segment_ids: dict[int, str], segments: list[dict], timestamp: float) -> str | None:
    for seg in segments:
        if float(seg["start_seconds"]) <= timestamp <= float(seg["end_seconds"]):
            return segment_ids.get(seg["segment_index"])
    return None


def _split_text_evenly(text: str, count: int) -> list[str]:
    if count <= 0:
        return []
    if not text.strip():
        return [""] * count
    words = text.split()
    size = max(1, math.ceil(len(words) / count))
    parts = [" ".join(words[i * size:(i + 1) * size]) for i in range(count)]
    while len(parts) < count:
        parts.append("")
    return parts[:count]


def _require_binary(name: str) -> None:
    from shutil import which

    if not which(name):
        raise RuntimeError(f"{name} is required for video processing. Install ffmpeg so both ffmpeg and ffprobe are available.")


def _parse_fps(value: str | None) -> float | None:
    if not value or value == "0/0":
        return None
    if "/" in value:
        left, right = value.split("/", 1)
        denom = float(right or 1)
        return round(float(left) / denom, 3) if denom else None
    return _num(value)


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _fmt_time(seconds: float | int | None) -> str:
    total = int(float(seconds or 0))
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
