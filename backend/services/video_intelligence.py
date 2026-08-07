from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from database.connection import get_pool
from services import storage as gcs
from services.notifications import send_video_processing_notification
from services.text_safety import sanitize_text_for_storage
from services.vectordb import delete_document_vectors, store_chunk

log = logging.getLogger("docintel.video")

DEFAULT_MAX_FRAMES = int(os.getenv("VIDEO_MAX_FRAMES", "12"))
DEFAULT_SEGMENT_SECONDS = int(os.getenv("VIDEO_SEGMENT_SECONDS", "60"))
FRAME_CAPTION_ENABLED = os.getenv("VIDEO_FRAME_CAPTION_ENABLED", "true").lower() != "false"
TRANSCRIBE_AUDIO_ENABLED = os.getenv("VIDEO_TRANSCRIBE_AUDIO_ENABLED", "true").lower() != "false"
TRANSCRIBE_CHUNK_SECONDS = int(os.getenv("VIDEO_TRANSCRIBE_CHUNK_SECONDS", "55"))
VIDEO_TRANSCRIBE_CONCURRENCY = int(os.getenv("VIDEO_TRANSCRIBE_CONCURRENCY", "3"))
VIDEO_FRAME_CONCURRENCY = int(os.getenv("VIDEO_FRAME_CONCURRENCY", "4"))
VIDEO_EMBED_CONCURRENCY = int(os.getenv("VIDEO_EMBED_CONCURRENCY", "4"))
VIDEO_SOURCE_READ_URL_EXPIRY_SECONDS = int(os.getenv("VIDEO_SOURCE_READ_URL_EXPIRY_SECONDS", os.getenv("GCS_SIGNED_URL_EXPIRY_SECONDS", "21600")))
FFMPEG_REMOTE_TIMEOUT_US = int(os.getenv("FFMPEG_REMOTE_TIMEOUT_US", "30000000"))
VIDEO_REMOTE_STAGE_RETRIES = int(os.getenv("VIDEO_REMOTE_STAGE_RETRIES", "3"))
VIDEO_REMOTE_RETRY_DELAY_SECONDS = float(os.getenv("VIDEO_REMOTE_RETRY_DELAY_SECONDS", "3"))
FFMPEG_COMMAND_TIMEOUT_SECONDS = int(os.getenv("FFMPEG_COMMAND_TIMEOUT_SECONDS", "180"))


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
    await _update_video_progress(
        document_id,
        step="queued",
        progress_pct=2,
        message="Video processing job started.",
    )

    frame_paths: list[tuple[int, float, str]] = []
    local_source_path: str | None = None
    try:
        await _update_video_progress(
            document_id,
            step="preparing_remote_source",
            progress_pct=8,
            message="Creating signed read URL for remote video processing.",
        )
        source_ref = await _create_signed_source_ref(source_gcs_path)

        await _update_video_progress(
            document_id,
            step="extracting_metadata",
            progress_pct=16,
            message="Reading duration, codec, resolution, and audio metadata.",
        )
        try:
            metadata, source_ref = await _run_remote_stage_with_retries(
                document_id=document_id,
                source_gcs_path=source_gcs_path,
                source_ref=source_ref,
                stage_name="extracting_metadata",
                progress_pct=16,
                message="Retrying remote metadata extraction with a fresh signed URL.",
                run_stage=lambda ref: asyncio.to_thread(_probe_video, ref),
            )
        except Exception as exc:
            log.warning(
                "Remote ffprobe failed after retries for document %s; falling back to streaming temp file: %s",
                document_id,
                exc,
            )
            await _update_video_progress(
                document_id,
                step="remote_probe_fallback",
                progress_pct=18,
                message="Remote video probe failed; using streaming fallback for this file.",
            )
            source_ref = await _create_signed_source_ref(source_gcs_path)
            local_source_path = await asyncio.to_thread(_download_signed_url_to_temp_file, source_ref, filename)
            source_ref = local_source_path
            metadata = await asyncio.to_thread(_probe_video, source_ref)
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

        await _update_video_progress(
            document_id,
            step="transcribing_audio" if TRANSCRIBE_AUDIO_ENABLED else "audio_transcription_skipped",
            progress_pct=26,
            message="Extracting and transcribing the video audio." if TRANSCRIBE_AUDIO_ENABLED else "Audio transcription is disabled.",
        )
        if TRANSCRIBE_AUDIO_ENABLED and _is_remote_ref(source_ref):
            transcript, source_ref = await _run_remote_stage_with_retries(
                document_id=document_id,
                source_gcs_path=source_gcs_path,
                source_ref=source_ref,
                stage_name="transcribing_audio",
                progress_pct=26,
                message="Retrying remote audio transcription with a fresh signed URL.",
                run_stage=lambda ref: _transcribe_audio(ref, document_id=document_id),
            )
        else:
            transcript = await _transcribe_audio(
                source_ref,
                document_id=document_id,
            ) if TRANSCRIBE_AUDIO_ENABLED else ""
        await _update_video_progress(
            document_id,
            step="sampling_frames",
            progress_pct=45,
            message="Sampling representative frames from the video timeline.",
        )
        try:
            frame_paths, source_ref = await _run_remote_stage_with_retries(
                document_id=document_id,
                source_gcs_path=source_gcs_path,
                source_ref=source_ref,
                stage_name="sampling_frames",
                progress_pct=45,
                message="Retrying remote frame sampling with a fresh signed URL.",
                run_stage=lambda ref: asyncio.to_thread(_sample_frames, ref, duration, max_frames),
            )
        except Exception as exc:
            log.warning("Frame sampling skipped after signed URL retries for document %s: %s", document_id, exc)
            await _update_video_progress(
                document_id,
                step="sampling_frames_skipped",
                progress_pct=45,
                message="Frame sampling failed after retries; continuing with transcript and timeline chunks.",
            )
            frame_paths = []
        frames = await _process_frames_parallel(
            document_id=document_id,
            frames_dir=frames_dir,
            frame_paths=frame_paths,
        )

        await _update_video_progress(
            document_id,
            step="building_segments",
            progress_pct=62,
            message="Building timestamped video segments from transcript and sampled frames.",
        )
        segments = _build_segments(duration, segment_seconds, transcript, frames)
        await _update_video_progress(
            document_id,
            step="creating_chunks",
            progress_pct=72,
            message="Creating searchable timestamped chunks for retrieval.",
        )
        chunks = _build_video_chunks(document_id, user_id, filename, metadata, segments, frames, transcript)

        await _update_video_progress(
            document_id,
            step="saving_artifacts",
            progress_pct=80,
            message="Saving timeline, frames, transcript, and chunk artifacts.",
        )
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
                await _update_video_progress(
                    document_id,
                    step="embedding_chunks",
                    progress_pct=88,
                    message=f"Embedding {len(chunks)} video chunks for chat and search.",
                )
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
        await _update_video_progress(
            document_id,
            step="completed",
            progress_pct=100,
            message="Video processing completed.",
        )
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
        await _send_video_notification(
            user_id=user_id,
            filename=filename,
            status="completed",
            duration_seconds=duration,
            segment_count=len(segments),
            chunk_count=len(chunks),
            embed_status=embed_status,
        )
        return output

    except Exception as exc:
        error = str(exc)[:500]
        log.exception("Video processing failed for %s: %s", document_id, error)
        await _update_video_progress(
            document_id,
            step="failed",
            progress_pct=100,
            message=error,
        )
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
        await _send_video_notification(
            user_id=user_id,
            filename=filename,
            status="error",
            error_message=error,
        )
        raise
    finally:
        _safe_unlink(local_source_path, label="streaming fallback source file")
        for _, _, path in frame_paths:
            _safe_unlink(path, label="sampled frame temp file")


async def _send_video_notification(
    *,
    user_id: str,
    filename: str,
    status: str,
    duration_seconds: float | int | None = None,
    segment_count: int = 0,
    chunk_count: int = 0,
    embed_status: str = "",
    error_message: str = "",
) -> None:
    """Send best-effort video notification using the user's existing embed preference."""
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            user_row = await conn.fetchrow(
                "SELECT email, notify_on_embed FROM users WHERE id=$1",
                user_id,
            )
        if user_row and user_row.get("notify_on_embed", True) and user_row.get("email"):
            await send_video_processing_notification(
                user_email=user_row["email"],
                doc_name=filename,
                status=status,
                duration_seconds=duration_seconds,
                segment_count=segment_count,
                chunk_count=chunk_count,
                embed_status=embed_status,
                error_message=error_message,
            )
    except Exception as exc:
        log.warning("Video notification skipped for user=%s file=%s: %s", user_id, filename, exc)


async def _update_video_progress(
    document_id: str,
    *,
    step: str,
    progress_pct: int,
    message: str,
) -> None:
    progress_pct = max(0, min(100, int(progress_pct)))
    progress = {
        "step": step,
        "progress_pct": progress_pct,
        "message": message,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE documents
                   SET doc_metadata = COALESCE(doc_metadata, '{}'::jsonb) || $2::jsonb,
                       updated_at = NOW()
                 WHERE id = $1
                """,
                document_id,
                json.dumps({"video_progress": progress}),
            )
            await conn.execute(
                """
                UPDATE video_documents
                   SET metadata = COALESCE(metadata, '{}'::jsonb) || $2::jsonb,
                       updated_at = NOW()
                 WHERE document_id = $1
                """,
                document_id,
                json.dumps({"progress": progress}),
            )
    except Exception as exc:
        log.warning("Video progress update skipped for document=%s step=%s: %s", document_id, step, exc)


def _phase_pct(start_pct: int, end_pct: int, completed: int, total: int) -> int:
    if total <= 0:
        return start_pct
    ratio = max(0.0, min(1.0, completed / total))
    return int(round(start_pct + (end_pct - start_pct) * ratio))


def _is_remote_ref(source: str | None) -> bool:
    return bool(source and str(source).startswith(("http://", "https://")))


async def _create_signed_source_ref(source_gcs_path: str) -> str:
    source_ref = await gcs.get_signed_read_url(
        source_gcs_path,
        expiry_seconds=VIDEO_SOURCE_READ_URL_EXPIRY_SECONDS,
    )
    await asyncio.to_thread(_assert_remote_source_readable, source_ref)
    return source_ref


async def _run_remote_stage_with_retries(
    *,
    document_id: str,
    source_gcs_path: str,
    source_ref: str,
    stage_name: str,
    progress_pct: int,
    message: str,
    run_stage: Any,
) -> tuple[Any, str]:
    current_source_ref = source_ref
    attempts = max(1, VIDEO_REMOTE_STAGE_RETRIES)
    last_exc: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            return await run_stage(current_source_ref), current_source_ref
        except Exception as exc:
            last_exc = exc
            if not _is_remote_ref(current_source_ref) or attempt >= attempts:
                break
            log.warning(
                "Video stage %s failed on attempt %s/%s for document %s; refreshing signed URL: %s",
                stage_name,
                attempt,
                attempts,
                document_id,
                exc,
            )
            await _update_video_progress(
                document_id,
                step=f"{stage_name}_retry",
                progress_pct=progress_pct,
                message=f"{message} Attempt {attempt + 1} of {attempts}.",
            )
            if VIDEO_REMOTE_RETRY_DELAY_SECONDS > 0:
                await asyncio.sleep(VIDEO_REMOTE_RETRY_DELAY_SECONDS)
            current_source_ref = await _create_signed_source_ref(source_gcs_path)

    raise RuntimeError(f"{stage_name} failed after {attempts} attempt(s): {last_exc}")


def _safe_unlink(path: str | None, *, label: str = "temporary file") -> None:
    if not path:
        return
    try:
        os.unlink(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        log.warning("Could not delete %s %s: %s", label, path, exc)


def _ffmpeg_remote_input_options(source: str) -> list[str]:
    if not str(source).startswith(("http://", "https://")):
        return []
    return [
        "-rw_timeout",
        str(FFMPEG_REMOTE_TIMEOUT_US),
        "-reconnect",
        "1",
        "-reconnect_streamed",
        "1",
        "-reconnect_delay_max",
        "10",
    ]


def _assert_remote_source_readable(url: str) -> None:
    request = urllib.request.Request(
        url,
        headers={
            "Range": "bytes=0-1023",
            "User-Agent": "DocIntelVideoProcessor/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = getattr(response, "status", 200)
            if status not in {200, 206}:
                raise RuntimeError(f"signed read URL returned HTTP {status}")
            response.read(1)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"signed read URL returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"signed read URL is not reachable: {exc.reason}") from exc


def _download_signed_url_to_temp_file(url: str, filename: str) -> str:
    suffix = Path(filename or "").suffix or ".mp4"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp_path = tmp.name
    tmp.close()
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "DocIntelVideoProcessor/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            with open(tmp_path, "wb") as out:
                shutil.copyfileobj(response, out, length=1024 * 1024)
        return tmp_path
    except Exception:
        _safe_unlink(tmp_path, label="failed streaming fallback source file")
        raise


def _check_output_text(cmd: list[str]) -> str:
    completed = subprocess.run(cmd, text=True, capture_output=True, timeout=FFMPEG_COMMAND_TIMEOUT_SECONDS)
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        stdout = (completed.stdout or "").strip()
        detail = stderr or stdout or f"exit code {completed.returncode}"
        raise RuntimeError(detail[:2000])
    return completed.stdout


def _run_command(cmd: list[str]) -> None:
    completed = subprocess.run(cmd, text=True, capture_output=True, timeout=FFMPEG_COMMAND_TIMEOUT_SECONDS)
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        stdout = (completed.stdout or "").strip()
        detail = stderr or stdout or f"exit code {completed.returncode}"
        raise RuntimeError(detail[:2000])


def _probe_video(path: str) -> dict[str, Any]:
    _require_binary("ffprobe")
    cmd = [
        "ffprobe",
        "-v",
        "error",
        *(_ffmpeg_remote_input_options(path)),
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        path,
    ]
    data = json.loads(_check_output_text(cmd))
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
        early = [1.0, min(30.0, duration * 0.05), min(90.0, duration * 0.1)]
        early = [t for t in early if 0 < t < duration]
        remaining = max(0, count - len(early))
        if remaining:
            start = max(120.0, duration * 0.15)
            if start >= duration:
                spread = [duration * (i + 1) / (remaining + 1) for i in range(remaining)]
            elif remaining == 1:
                spread = [min(duration - 0.1, start)]
            else:
                step = (duration - start) / (remaining - 1)
                spread = [start + step * i for i in range(remaining)]
        else:
            spread = []
        timestamps = sorted({max(0.1, min(duration - 0.1, round(t, 2))) for t in [*early, *spread]})

    frames = []
    for idx, timestamp in enumerate(timestamps):
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
        tmp.close()
        fast_seek_cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            str(timestamp),
            *(_ffmpeg_remote_input_options(path)),
            "-i",
            path,
            "-frames:v",
            "1",
            "-q:v",
            "3",
            tmp.name,
        ]
        compatible_seek_cmd = [
            "ffmpeg",
            "-y",
            *(_ffmpeg_remote_input_options(path)),
            "-i",
            path,
            "-ss",
            str(timestamp),
            "-frames:v",
            "1",
            "-q:v",
            "3",
            tmp.name,
        ]
        try:
            _run_command(fast_seek_cmd)
        except Exception as fast_exc:
            log.warning("Fast frame seek failed at %.2fs; retrying compatible seek: %s", timestamp, fast_exc)
            try:
                _run_command(compatible_seek_cmd)
            except Exception as compat_exc:
                _safe_unlink(tmp.name, label="failed sampled frame")
                log.warning("Frame sampling skipped at %.2fs: %s", timestamp, compat_exc)
                continue
        frames.append((idx, timestamp, tmp.name))
    if timestamps and not frames:
        raise RuntimeError("No video frames could be sampled")
    return frames


async def _caption_frame(path: str, timestamp: float) -> str:
    try:
        from services.llm import vision_extract

        raw = await vision_extract(path, "image/jpeg")
        return sanitize_text_for_storage(raw).strip()[:1200]
    except Exception as exc:
        log.warning("Frame caption skipped at %.2fs: %s", timestamp, exc)
        return ""


async def _transcribe_audio(path: str, *, document_id: str | None = None) -> str:
    provider = os.getenv("VIDEO_TRANSCRIBE_PROVIDER") or os.getenv("LLM_PROVIDER", "openai")
    provider = provider.lower().strip()
    if provider == "openai":
        return await _transcribe_audio_openai(path)
    if provider in {"gemini", "google", "google_speech", "speech", "vertex"}:
        return await _transcribe_audio_google_speech(path, document_id=document_id)
    log.warning("Audio transcription skipped: unsupported provider %s", provider)
    return ""


async def _transcribe_audio_openai(path: str) -> str:
    if not os.getenv("OPENAI_API_KEY"):
        log.warning("Audio transcription skipped: OPENAI_API_KEY is not configured")
        return ""
    _require_binary("ffmpeg")
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
    tmp.close()
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                *(_ffmpeg_remote_input_options(path)),
                "-i",
                path,
                "-vn",
                "-acodec",
                "libmp3lame",
                "-ar",
                "16000",
                "-ac",
                "1",
                tmp.name,
            ],
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
        _safe_unlink(tmp.name, label="OpenAI audio transcript temp file")


async def _transcribe_audio_google_speech(path: str, *, document_id: str | None = None) -> str:
    api_key = (
        os.getenv("GOOGLE_SPEECH_API_KEY")
        or os.getenv("GOOGLE_STT_API_KEY")
        or os.getenv("GOOGLE_AI_KEY")
        or os.getenv("GOOGLE_API_KEY")
    )
    if not api_key:
        log.warning("Audio transcription skipped: GOOGLE_SPEECH_API_KEY or GOOGLE_AI_KEY is not configured")
        return ""

    _require_binary("ffmpeg")
    duration = _probe_video(path).get("duration_seconds") or 0
    chunk_seconds = max(15, min(55, TRANSCRIBE_CHUNK_SECONDS))
    starts = [0.0] if duration <= 0 else [float(s) for s in range(0, int(math.ceil(duration)), chunk_seconds)]
    language_code = os.getenv("VIDEO_TRANSCRIBE_LANGUAGE_CODE", "en-US")
    total = len(starts)
    completed = 0

    try:
        import httpx

        semaphore = asyncio.Semaphore(max(1, VIDEO_TRANSCRIBE_CONCURRENCY))

        async def transcribe_one(index: int, start: float, client: Any) -> tuple[int, str]:
            nonlocal completed
            length = chunk_seconds if duration <= 0 else min(chunk_seconds, max(1.0, duration - start))
            if length <= 0:
                return index, ""
            async with semaphore:
                if document_id:
                    await _update_video_progress(
                        document_id,
                        step="transcribing_audio",
                        progress_pct=_phase_pct(26, 44, completed, total),
                        message=f"Extracting audio chunk {index + 1} of {total} ({_fmt_time(start)}-{_fmt_time(start + length)}).",
                    )
                chunk_path = ""
                try:
                    chunk_path = await asyncio.to_thread(_extract_audio_chunk, path, start, length)
                    if document_id:
                        await _update_video_progress(
                            document_id,
                            step="transcribing_audio",
                            progress_pct=_phase_pct(26, 44, completed, total),
                            message=f"Sending audio chunk {index + 1} of {total} to speech recognition.",
                        )
                    text = await _google_speech_recognize(client, api_key, chunk_path, language_code)
                    text = sanitize_text_for_storage(text).strip()
                    if not text:
                        return index, ""
                    end = start + length
                    return index, f"[{_fmt_time(start)}-{_fmt_time(end)}] {text}"
                except Exception as exc:
                    log.warning("Audio chunk %s/%s skipped at %.2fs: %s", index + 1, total, start, exc)
                    if document_id:
                        await _update_video_progress(
                            document_id,
                            step="transcribing_audio",
                            progress_pct=_phase_pct(26, 44, completed, total),
                            message=f"Skipped audio chunk {index + 1} of {total}: {str(exc)[:160]}",
                        )
                    return index, ""
                finally:
                    _safe_unlink(chunk_path, label="audio transcript chunk temp file")
                    completed += 1
                    if document_id:
                        await _update_video_progress(
                            document_id,
                            step="transcribing_audio",
                            progress_pct=_phase_pct(26, 44, completed, total),
                            message=f"Finished audio chunk {completed} of {total}.",
                        )

        async with httpx.AsyncClient(timeout=90) as client:
            results = await asyncio.gather(*(transcribe_one(i, start, client) for i, start in enumerate(starts)))
        return "\n".join(text for _, text in sorted(results) if text)
    except Exception as exc:
        log.warning("Google audio transcription skipped: %s", exc)
        return ""


async def _process_frames_parallel(
    *,
    document_id: str,
    frames_dir: str,
    frame_paths: list[tuple[int, float, str]],
) -> list[dict]:
    if not frame_paths:
        return []

    total = len(frame_paths)
    completed = 0
    semaphore = asyncio.Semaphore(max(1, VIDEO_FRAME_CONCURRENCY))

    async def process_one(item: tuple[int, float, str]) -> dict:
        nonlocal completed
        frame_index, timestamp, path = item
        async with semaphore:
            await _update_video_progress(
                document_id,
                step="processing_frames",
                progress_pct=_phase_pct(46, 61, completed, total),
                message=f"Uploading and captioning frame {completed + 1} of {total}.",
            )
            frame_gcs_path = f"{frames_dir}frame_{frame_index:04d}_{int(timestamp):06d}s.jpg"
            await gcs.upload_bytes(frame_gcs_path, Path(path).read_bytes(), "image/jpeg")
            caption = await _caption_frame(path, timestamp) if FRAME_CAPTION_ENABLED else ""
            completed += 1
            await _update_video_progress(
                document_id,
                step="processing_frames",
                progress_pct=_phase_pct(46, 61, completed, total),
                message=f"Processed frame {completed} of {total}.",
            )
            return {
                "frame_index": frame_index,
                "timestamp_seconds": timestamp,
                "frame_path": frame_gcs_path,
                "caption": caption,
            }

    frames = await asyncio.gather(*(process_one(item) for item in frame_paths))
    return sorted(frames, key=lambda frame: frame["frame_index"])


def _extract_audio_chunk(path: str, start: float, length: float) -> str:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".flac")
    tmp.close()
    try:
        _run_command(
            [
                "ffmpeg",
                "-y",
                "-ss",
                str(max(0, start)),
                "-t",
                str(max(1, length)),
                *(_ffmpeg_remote_input_options(path)),
                "-i",
                path,
                "-vn",
                "-acodec",
                "flac",
                "-ar",
                "16000",
                "-ac",
                "1",
                tmp.name,
            ]
        )
        return tmp.name
    except Exception:
        _safe_unlink(tmp.name, label="failed audio transcript chunk temp file")
        raise


async def _google_speech_recognize(client: Any, api_key: str, audio_path: str, language_code: str) -> str:
    with open(audio_path, "rb") as f:
        audio_b64 = base64.b64encode(f.read()).decode()
    payload = {
        "config": {
            "encoding": "FLAC",
            "sampleRateHertz": 16000,
            "languageCode": language_code,
            "enableAutomaticPunctuation": True,
            "model": os.getenv("GOOGLE_SPEECH_MODEL", "latest_long"),
        },
        "audio": {"content": audio_b64},
    }
    resp = await client.post(
        "https://speech.googleapis.com/v1/speech:recognize",
        params={"key": api_key},
        json=payload,
    )
    if not resp.is_success:
        raise RuntimeError(f"Google Speech error {resp.status_code}: {resp.text[:300]}")
    parts: list[str] = []
    for result in resp.json().get("results", []):
        alternatives = result.get("alternatives") or []
        if alternatives:
            parts.append(alternatives[0].get("transcript", ""))
    return " ".join(p for p in parts if p).strip()


def _build_segments(duration: float, segment_seconds: int, transcript: str, frames: list[dict]) -> list[dict]:
    segment_seconds = max(15, int(segment_seconds or DEFAULT_SEGMENT_SECONDS))
    if duration <= 0:
        duration = float(segment_seconds)
    count = max(1, math.ceil(duration / segment_seconds))
    transcript_entries = _parse_timestamped_transcript(transcript)
    transcript_parts = _split_text_evenly(transcript, count) if not transcript_entries else [""] * count
    segments = []
    for idx in range(count):
        start = float(idx * segment_seconds)
        end = float(min(duration, (idx + 1) * segment_seconds))
        transcript_text = _transcript_for_range(transcript_entries, start, end) if transcript_entries else transcript_parts[idx].strip()
        segment_frames = [f for f in frames if start <= float(f["timestamp_seconds"]) <= end]
        captions = [f.get("caption", "") for f in segment_frames if f.get("caption")]
        summary = " ".join(captions).strip()
        if not summary and transcript_text:
            summary = transcript_text
        if not summary:
            summary = f"Video segment from {_fmt_time(start)} to {_fmt_time(end)}."
        segments.append({
            "segment_index": idx,
            "start_seconds": start,
            "end_seconds": end,
            "segment_type": "timeline",
            "title": f"{_fmt_time(start)} - {_fmt_time(end)}",
            "summary": summary[:2500],
            "transcript": transcript_text,
            "ocr_text": "",
            "thumbnail_path": segment_frames[0]["frame_path"] if segment_frames else None,
            "confidence": 0.85 if transcript_text or captions else 0.4,
            "metadata": {
                "frame_indices": [f["frame_index"] for f in segment_frames],
                "transcript_entry_count": len(_transcript_entries_for_range(transcript_entries, start, end)) if transcript_entries else 0,
            },
        })
    return segments


def _parse_timestamped_transcript(transcript: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    pattern = re.compile(r"\[(?P<start>\d{1,2}:\d{2}(?::\d{2})?)-(?P<end>\d{1,2}:\d{2}(?::\d{2})?)\]\s*(?P<text>.*?)(?=\n\[\d{1,2}:\d{2}(?::\d{2})?-|\Z)", re.S)
    for match in pattern.finditer(transcript or ""):
        text = " ".join((match.group("text") or "").split()).strip()
        if not text:
            continue
        start = _parse_timecode(match.group("start"))
        end = _parse_timecode(match.group("end"))
        if end < start:
            end = start
        entries.append({"start": start, "end": end, "text": text})
    return entries


def _transcript_entries_for_range(entries: list[dict[str, Any]], start: float, end: float) -> list[dict[str, Any]]:
    return [
        entry
        for entry in entries
        if float(entry.get("start") or 0) < end and float(entry.get("end") or 0) > start
    ]


def _transcript_for_range(entries: list[dict[str, Any]], start: float, end: float) -> str:
    return " ".join(entry["text"] for entry in _transcript_entries_for_range(entries, start, end)).strip()


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
    total = len(chunks)
    if total == 0:
        return

    completed = 0
    semaphore = asyncio.Semaphore(max(1, VIDEO_EMBED_CONCURRENCY))

    async def embed_one(chunk: dict) -> None:
        nonlocal completed
        async with semaphore:
            await _update_video_progress(
                document_id,
                step="embedding_chunks",
                progress_pct=_phase_pct(88, 99, completed, total),
                message=f"Embedding video chunk {completed + 1} of {total}.",
            )
            content = sanitize_text_for_storage(chunk["content"])
            vector = await embed(content)
            await store_chunk(
                document_id=document_id,
                user_id=user_id,
                workspace_id=workspace_id,
                chunk_index=chunk["chunk_index"],
                chunk_total=total,
                content=content,
                embedding=vector,
                chunk_metadata=chunk["chunk_metadata"],
            )
            completed += 1
            await _update_video_progress(
                document_id,
                step="embedding_chunks",
                progress_pct=_phase_pct(88, 99, completed, total),
                message=f"Embedded video chunk {completed} of {total}.",
            )

    await asyncio.gather(*(embed_one(chunk) for chunk in chunks))


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


def _parse_timecode(value: str) -> float:
    parts = [int(part or 0) for part in str(value or "0").split(":")]
    if len(parts) == 3:
        return float(parts[0] * 3600 + parts[1] * 60 + parts[2])
    if len(parts) == 2:
        return float(parts[0] * 60 + parts[1])
    return float(parts[0] if parts else 0)
