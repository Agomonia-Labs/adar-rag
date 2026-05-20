# services/storage.py
# Google Cloud Storage operations.
# Bucket structure:
#   users/{user_id}/documents/{doc_id}/source/{filename}
#   users/{user_id}/documents/{doc_id}/chunks/chunk_{n:04d}.txt
#   users/{user_id}/documents/{doc_id}/chunks/_metadata.json
from __future__ import annotations
import os, json, asyncio
from datetime import timedelta
from typing import Any

from google.cloud import storage
from google.oauth2 import service_account

GCS_BUCKET   = os.getenv("GCS_BUCKET_NAME", "docintel-documents")
KEY_PATH     = os.getenv("GCS_SERVICE_ACCOUNT_KEY_PATH")
KEY_JSON_STR = os.getenv("GCS_SERVICE_ACCOUNT_KEY_JSON")
SIGNED_EXPIRY = int(os.getenv("GCS_SIGNED_URL_EXPIRY_SECONDS", "3600"))


# ── Client factory ────────────────────────────────────────────────────────────
def _make_client() -> storage.Client:
    if KEY_JSON_STR:
        creds = service_account.Credentials.from_service_account_info(
            json.loads(KEY_JSON_STR)
        )
        return storage.Client(credentials=creds)
    if KEY_PATH and os.path.exists(KEY_PATH):
        creds = service_account.Credentials.from_service_account_file(KEY_PATH)
        return storage.Client(credentials=creds)
    return storage.Client()   # Application default credentials (Cloud Run / GKE)


# ── GCS path helpers ──────────────────────────────────────────────────────────
def source_path(user_id: str, doc_id: str, filename: str) -> str:
    return f"users/{user_id}/documents/{doc_id}/source/{filename}"

def chunk_path(user_id: str, doc_id: str, chunk_index: int) -> str:
    return f"users/{user_id}/documents/{doc_id}/chunks/chunk_{chunk_index:04d}.txt"

def metadata_path(user_id: str, doc_id: str) -> str:
    return f"users/{user_id}/documents/{doc_id}/chunks/_metadata.json"

def chunks_dir(user_id: str, doc_id: str) -> str:
    return f"users/{user_id}/documents/{doc_id}/chunks/"


# ── Upload ────────────────────────────────────────────────────────────────────
async def upload_bytes(blob_path: str, data: bytes, content_type: str = "application/octet-stream") -> None:
    def _do():
        client = _make_client()
        blob   = client.bucket(GCS_BUCKET).blob(blob_path)
        blob.upload_from_string(data, content_type=content_type)
    await asyncio.to_thread(_do)


async def upload_text(blob_path: str, text: str) -> None:
    await upload_bytes(blob_path, text.encode("utf-8"), content_type="text/plain; charset=utf-8")


async def upload_json(blob_path: str, obj: Any) -> None:
    await upload_bytes(blob_path, json.dumps(obj, indent=2).encode(), content_type="application/json")


# ── Download ──────────────────────────────────────────────────────────────────
async def download_text(blob_path: str) -> str:
    def _do():
        client = _make_client()
        blob   = client.bucket(GCS_BUCKET).blob(blob_path)
        return blob.download_as_text(encoding="utf-8")
    return await asyncio.to_thread(_do)


async def download_json(blob_path: str) -> Any:
    text = await download_text(blob_path)
    return json.loads(text)


async def download_bytes(blob_path: str) -> bytes:
    def _do():
        client = _make_client()
        blob   = client.bucket(GCS_BUCKET).blob(blob_path)
        return blob.download_as_bytes()
    return await asyncio.to_thread(_do)


# ── Signed URL (time-limited public link for viewing) ─────────────────────────
async def get_signed_url(blob_path: str, expiry_seconds: int = SIGNED_EXPIRY) -> str:
    def _do():
        if KEY_JSON_STR:
            creds = service_account.Credentials.from_service_account_info(
                json.loads(KEY_JSON_STR)
            )
        elif KEY_PATH and os.path.exists(KEY_PATH):
            creds = service_account.Credentials.from_service_account_file(KEY_PATH)
        else:
            raise RuntimeError(
                "Signed URLs require a service account key. "
                "Set GCS_SERVICE_ACCOUNT_KEY_PATH or GCS_SERVICE_ACCOUNT_KEY_JSON."
            )
        client = storage.Client(credentials=creds)
        blob   = client.bucket(GCS_BUCKET).blob(blob_path)
        return blob.generate_signed_url(
            expiration=timedelta(seconds=expiry_seconds),
            method="GET",
            version="v4",
        )
    return await asyncio.to_thread(_do)


# ── Delete ────────────────────────────────────────────────────────────────────
async def delete_prefix(prefix: str) -> int:
    """Delete all blobs under a GCS prefix. Returns count deleted."""
    def _do():
        client  = _make_client()
        bucket  = client.bucket(GCS_BUCKET)
        blobs   = list(bucket.list_blobs(prefix=prefix))
        bucket.delete_blobs(blobs)
        return len(blobs)
    return await asyncio.to_thread(_do)


# ── List chunks ───────────────────────────────────────────────────────────────
async def list_chunk_blobs(user_id: str, doc_id: str) -> list[str]:
    """Return sorted list of chunk blob paths (excludes _metadata.json)."""
    prefix = chunks_dir(user_id, doc_id)
    def _do():
        client = _make_client()
        blobs  = client.bucket(GCS_BUCKET).list_blobs(prefix=prefix)
        return sorted(
            b.name for b in blobs
            if b.name.endswith(".txt") and not b.name.endswith("_metadata.json")
        )
    return await asyncio.to_thread(_do)
