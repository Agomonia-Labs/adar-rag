# services/storage.py
# Google Cloud Storage — works with both service account key (local/Docker)
# and Application Default Credentials (Cloud Run / GKE).
from __future__ import annotations
import os, json, asyncio
from datetime import timedelta
from typing import Any

from google.cloud import storage
from google.oauth2 import service_account

GCS_BUCKET    = os.getenv("GCS_BUCKET_NAME", "docintel-documents")
KEY_PATH      = os.getenv("GCS_SERVICE_ACCOUNT_KEY_PATH", "")
KEY_JSON_STR  = os.getenv("GCS_SERVICE_ACCOUNT_KEY_JSON", "")
SIGNED_EXPIRY = int(os.getenv("GCS_SIGNED_URL_EXPIRY_SECONDS", "3600"))
_IS_CLOUD_RUN = os.getenv("K_SERVICE") is not None   # Cloud Run sets K_SERVICE


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
    # Cloud Run / GKE — use attached service account (ADC)
    return storage.Client()


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
    await upload_bytes(blob_path, text.encode("utf-8"), "text/plain; charset=utf-8")

async def upload_json(blob_path: str, obj: Any) -> None:
    await upload_bytes(blob_path, json.dumps(obj, indent=2).encode(), "application/json")


# ── Download ──────────────────────────────────────────────────────────────────
async def download_text(blob_path: str) -> str:
    def _do():
        return _make_client().bucket(GCS_BUCKET).blob(blob_path).download_as_text(encoding="utf-8")
    return await asyncio.to_thread(_do)

async def download_json(blob_path: str) -> Any:
    return json.loads(await download_text(blob_path))

async def download_bytes(blob_path: str) -> bytes:
    def _do():
        return _make_client().bucket(GCS_BUCKET).blob(blob_path).download_as_bytes()
    return await asyncio.to_thread(_do)


# ── Signed URL ────────────────────────────────────────────────────────────────
async def get_signed_upload_url(
    blob_path: str,
    content_type: str = "application/octet-stream",
    expiry_seconds: int = SIGNED_EXPIRY,
) -> str:
    """Generate a signed PUT URL so browsers can upload large files directly to GCS."""
    def _do():
        if _IS_CLOUD_RUN:
            import google.auth
            from google.auth.transport import requests as google_requests

            credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            credentials.refresh(google_requests.Request())
            client = storage.Client(credentials=credentials)
            blob = client.bucket(GCS_BUCKET).blob(blob_path)
            return blob.generate_signed_url(
                expiration=timedelta(seconds=expiry_seconds),
                method="PUT",
                version="v4",
                content_type=content_type,
                service_account_email=credentials.service_account_email,
                access_token=credentials.token,
            )

        if KEY_JSON_STR:
            creds = service_account.Credentials.from_service_account_info(
                json.loads(KEY_JSON_STR)
            )
        elif KEY_PATH and os.path.exists(KEY_PATH):
            creds = service_account.Credentials.from_service_account_file(KEY_PATH)
        else:
            raise RuntimeError(
                "Signed upload URLs require a service account key locally. "
                "Set GCS_SERVICE_ACCOUNT_KEY_PATH or GCS_SERVICE_ACCOUNT_KEY_JSON."
            )
        client = storage.Client(credentials=creds)
        blob = client.bucket(GCS_BUCKET).blob(blob_path)
        return blob.generate_signed_url(
            expiration=timedelta(seconds=expiry_seconds),
            method="PUT",
            version="v4",
            content_type=content_type,
        )

    return await asyncio.to_thread(_do)


async def blob_metadata(blob_path: str) -> dict[str, Any] | None:
    """Return uploaded object metadata, or None if the object does not exist."""
    def _do():
        blob = _make_client().bucket(GCS_BUCKET).blob(blob_path)
        if not blob.exists():
            return None
        blob.reload()
        return {
            "name": blob.name,
            "size": int(blob.size or 0),
            "content_type": blob.content_type,
            "updated": blob.updated.isoformat() if blob.updated else None,
            "generation": str(blob.generation) if blob.generation else None,
        }

    return await asyncio.to_thread(_do)


async def get_signed_url(blob_path: str, expiry_seconds: int = SIGNED_EXPIRY) -> str:
    def _do():
        if _IS_CLOUD_RUN:
            # On Cloud Run: use IAM credentials API for signing (no key file needed)
            # Requires roles/iam.serviceAccountTokenCreator on the service account
            import google.auth
            from google.auth.transport import requests as google_requests

            credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            credentials.refresh(google_requests.Request())
            client = storage.Client(credentials=credentials)
            blob   = client.bucket(GCS_BUCKET).blob(blob_path)
            return blob.generate_signed_url(
                expiration=timedelta(seconds=expiry_seconds),
                method="GET",
                version="v4",
                service_account_email=credentials.service_account_email,
                access_token=credentials.token,
            )

        # Local / Docker: use service account key
        if KEY_JSON_STR:
            creds = service_account.Credentials.from_service_account_info(
                json.loads(KEY_JSON_STR)
            )
        elif KEY_PATH and os.path.exists(KEY_PATH):
            creds = service_account.Credentials.from_service_account_file(KEY_PATH)
        else:
            raise RuntimeError(
                "Signed URLs require a service account key locally. "
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
    def _do():
        client  = _make_client()
        bucket  = client.bucket(GCS_BUCKET)
        blobs   = list(bucket.list_blobs(prefix=prefix))
        if blobs:
            bucket.delete_blobs(blobs)
        return len(blobs)
    return await asyncio.to_thread(_do)

async def list_chunk_blobs(user_id: str, doc_id: str) -> list[str]:
    prefix = chunks_dir(user_id, doc_id)
    def _do():
        client = _make_client()
        blobs  = client.bucket(GCS_BUCKET).list_blobs(prefix=prefix)
        return sorted(b.name for b in blobs if b.name.endswith(".txt"))
    return await asyncio.to_thread(_do)
