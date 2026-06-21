"""Local object-storage shim.

The contract keeps large files OUT of the API: the client asks for a presigned
URL, PUTs the bytes straight to object storage, and references the returned
`storage_path`. In production you point this at a private S3-compatible bucket
(AWS S3 / Cloudflare R2 / Backblaze B2 / MinIO) and mint real presigned URLs
with boto3 — the request/response SHAPES the app sees do not change.

For zero-setup local dev this shim stores bytes on local disk and signs short-
lived URLs that resolve back to THIS API (see routers/uploads.py and the /files
route). Swap only this module to go to real S3.
"""
import os
from datetime import datetime, timedelta, timezone

from .config import settings
from .security import sign_file_token

_EXT = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}


def _path_for(kind: str, content_type: str) -> str:
    import uuid
    now = datetime.now(timezone.utc)
    ext = _EXT.get((content_type or "").lower(), "bin")
    return f"captures/{now:%Y}/{now:%m}/{uuid.uuid4()}-{kind}.{ext}"


def presign_put(kind: str, content_type: str) -> dict:
    storage_path = _path_for(kind, content_type)
    token = sign_file_token(storage_path, op="put", ttl_minutes=settings.PRESIGN_PUT_TTL_MIN)
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.PRESIGN_PUT_TTL_MIN)
    return {
        "upload_url": f"{settings.PUBLIC_BASE}/v1/uploads/local?token={token}",
        "storage_path": storage_path,
        "expires_at": expires.isoformat().replace("+00:00", "Z"),
    }


def signed_get_url(storage_path: str) -> dict:
    token = sign_file_token(storage_path, op="get", ttl_minutes=settings.SIGNED_GET_TTL_MIN)
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.SIGNED_GET_TTL_MIN)
    return {
        "url": f"{settings.PUBLIC_BASE}/v1/files?token={token}",
        "expires_at": expires.isoformat().replace("+00:00", "Z"),
    }


def _abs(storage_path: str) -> str:
    # Guard against path traversal in the storage key.
    safe = os.path.normpath(storage_path).lstrip("/").replace("..", "")
    return os.path.join(settings.STORAGE_DIR, safe)


def write_bytes(storage_path: str, data: bytes) -> None:
    target = _abs(storage_path)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "wb") as f:
        f.write(data)


def read_bytes(storage_path: str) -> bytes | None:
    target = _abs(storage_path)
    if not os.path.exists(target):
        return None
    with open(target, "rb") as f:
        return f.read()


def delete_bytes(storage_path: str) -> bool:
    """Permanently delete a stored object from local disk (mirrors the S3
    backend's delete_bytes — used by the asset purge / hard-delete endpoint).
    IDEMPOTENT: a missing file is treated as success.
    """
    target = _abs(storage_path)
    try:
        os.remove(target)
        return True
    except FileNotFoundError:
        return True
