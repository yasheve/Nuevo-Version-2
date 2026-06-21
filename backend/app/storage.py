"""Production object storage for the NuEvo Asset Capture API (S3 / R2 / B2 / MinIO).

DROP-IN REPLACEMENT for app/storage.py.
  1. Save this file as  app/storage.py  (replacing the local-disk shim).
  2. Add  boto3  to requirements.txt   (pip install boto3)
  3. Set the environment variables below.
  4. Set the bucket's CORS (see the JSON at the bottom of this file).

It exposes the SAME four functions the rest of the app imports
(presign_put, signed_get_url, write_bytes, read_bytes) with the SAME
request/response shapes, so nothing else changes:

  - The phone asks POST /v1/uploads/presign, gets `upload_url`, and PUTs the
    JPEG bytes STRAIGHT TO THE BUCKET (never through the API).
  - presign_put signs the URL with ContentType = the type the app sends, so the
    app's `Content-Type: <type>` PUT header matches (mismatch => 403).
  - signed_get_url returns a short-lived bucket GET URL for synced photos.
  - read_bytes is used server-side by OCR to fetch the just-uploaded image.

ENVIRONMENT VARIABLES
  S3_BUCKET            (required)  e.g.  nuevo-captures-prod   -- keep it PRIVATE
  AWS_REGION           (required for AWS) e.g.  af-south-1 / eu-west-1
  AWS_ACCESS_KEY_ID        }  standard AWS creds, scoped to this bucket only;
  AWS_SECRET_ACCESS_KEY    }  boto3 reads them automatically (do not hard-code)
  S3_ENDPOINT_URL      (only for non-AWS) Cloudflare R2 / Backblaze B2 / MinIO
                       e.g.  https://<accountid>.r2.cloudflarestorage.com
  PRESIGN_PUT_TTL_MIN  (optional, default 10)  upload-link lifetime
  SIGNED_GET_TTL_MIN   (optional, default 15)  image-link lifetime

NOTE: with S3 you do NOT need PUBLIC_BASE — storage URLs come from the bucket.
"""
import os
import uuid
from datetime import datetime, timedelta, timezone

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from .config import settings

# --- bucket / client (built once) ---------------------------------------
S3_BUCKET = os.getenv("S3_BUCKET")
if not S3_BUCKET:
    raise RuntimeError("S3_BUCKET is required when using the S3 storage backend")

_REGION = os.getenv("AWS_REGION")
_ENDPOINT = os.getenv("S3_ENDPOINT_URL") or None  # set only for R2 / B2 / MinIO

# Signature v4 is required for presigned PUT-with-ContentType and for R2/B2.
_s3 = boto3.client(
    "s3",
    region_name=_REGION,
    endpoint_url=_ENDPOINT,
    config=Config(signature_version="s3v4"),
)

_EXT = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}


def _iso(minutes: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)) \
        .isoformat().replace("+00:00", "Z")


def _path_for(kind: str, content_type: str) -> str:
    now = datetime.now(timezone.utc)
    ext = _EXT.get((content_type or "").lower(), "bin")
    return f"captures/{now:%Y}/{now:%m}/{uuid.uuid4()}-{kind}.{ext}"


def presign_put(kind: str, content_type: str) -> dict:
    """Return a presigned URL the phone PUTs the bytes to directly."""
    storage_path = _path_for(kind, content_type)
    params = {"Bucket": S3_BUCKET, "Key": storage_path}
    # Bind the content type ONLY if the app sends one; the app's PUT header must
    # then equal it. (The app derives content_type from the image data URL.)
    if content_type:
        params["ContentType"] = content_type
    upload_url = _s3.generate_presigned_url(
        "put_object",
        Params=params,
        ExpiresIn=settings.PRESIGN_PUT_TTL_MIN * 60,
    )
    return {
        "upload_url": upload_url,
        "storage_path": storage_path,
        "expires_at": _iso(settings.PRESIGN_PUT_TTL_MIN),
    }


def signed_get_url(storage_path: str) -> dict:
    """Short-lived URL to read a stored object (for synced-record photos)."""
    url = _s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": S3_BUCKET, "Key": storage_path},
        ExpiresIn=settings.SIGNED_GET_TTL_MIN * 60,
    )
    return {"url": url, "expires_at": _iso(settings.SIGNED_GET_TTL_MIN)}


def write_bytes(storage_path: str, data: bytes) -> None:
    """Server-side write (used only by the /uploads multipart fallback)."""
    _s3.put_object(Bucket=S3_BUCKET, Key=storage_path, Body=data)


def read_bytes(storage_path: str) -> bytes | None:
    """Server-side read (used by OCR to fetch the just-uploaded image)."""
    try:
        obj = _s3.get_object(Bucket=S3_BUCKET, Key=storage_path)
        return obj["Body"].read()
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404", "NotFound"):
            return None
        raise


def delete_bytes(storage_path: str) -> bool:
    """Permanently delete a stored object from the bucket (used by the asset
    purge / hard-delete endpoint — POPIA erasure of the photo bytes).

    IDEMPOTENT: a missing key is treated as success, because the desired end
    state — the object is gone — is already true. S3/R2 ``delete_object`` itself
    returns 204 for an absent key; the ``ClientError`` guard is belt-and-braces.
    Any OTHER error is raised so the caller can abort before deleting the DB row
    (avoids orphaning bytes whose only pointer has been removed).
    """
    try:
        _s3.delete_object(Bucket=S3_BUCKET, Key=storage_path)
        return True
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404", "NotFound"):
            return True
        raise


# ---------------------------------------------------------------------------
# REQUIRED BUCKET CORS  (so the browser can PUT uploads and GET signed images)
# AWS S3 -> bucket -> Permissions -> CORS.  Replace the origin with YOUR app's.
# Cloudflare R2 / Backblaze B2 use the same JSON shape in their dashboards.
#
# [
#   {
#     "AllowedOrigins": ["https://capture.nuevo.co.za"],
#     "AllowedMethods": ["GET", "PUT"],
#     "AllowedHeaders": ["Content-Type"],
#     "ExposeHeaders": ["ETag"],
#     "MaxAgeSeconds": 3000
#   }
# ]
# ---------------------------------------------------------------------------
