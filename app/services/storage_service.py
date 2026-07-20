"""
Storage Service
Handles file uploads to GCP Cloud Storage.
Falls back to local temp storage in development (DEBUG=True).

Usage:
    url = await upload_file(file, folder="syllabus/1")
"""

import os
import uuid
import datetime
from fastapi import UploadFile, HTTPException
from app.config import settings

# Allowed MIME types and their extensions
ALLOWED_DOCUMENT_TYPES = {
    "application/pdf": "pdf",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.ms-powerpoint": "ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "image/jpeg": "jpg",
    "image/png": "png",
}

ALLOWED_IMAGE_TYPES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}

ALLOWED_EXCEL_TYPES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.ms-excel": "xls",
    "application/octet-stream": "xlsx",  # Some browsers send this for xlsx
}

ALLOWED_VIDEO_TYPES = {
    "video/mp4": "mp4",
    "video/quicktime": "mov",
    "video/x-msvideo": "avi",
    "video/x-matroska": "mkv",
    "video/webm": "webm",
}

MAX_FILE_SIZE_MB = 20
MAX_VIDEO_SIZE_MB = 10
# Profile avatars (student / teacher apps)
MAX_PROFILE_AVATAR_MB = 5

# Admin syllabus chapter materials (larger than generic document upload)
SYLLABUS_CONTENT_MAX_VIDEO_MB = 100
SYLLABUS_CONTENT_MAX_DOC_MB = 50


async def upload_file(
    file: UploadFile,
    folder: str,
    allowed_types: dict = None,
    max_size_mb: float = None,
) -> str:
    """
    Upload a file and return its public or signed URL.
    In DEBUG mode: saves to local /tmp/edtech_uploads/ and returns a local URL.
    In production: uploads to GCP Cloud Storage.
    """
    if allowed_types is None:
        allowed_types = {**ALLOWED_DOCUMENT_TYPES, **ALLOWED_EXCEL_TYPES}

    # 1. Validate content type
    # Android often sends 'application/octet-stream' for valid media files —
    # fall back to extension-based detection in that case.
    content_type = file.content_type or ""
    if content_type == "application/octet-stream" and file.filename:
        ext_map = {
            ".mp4": "video/mp4", ".mov": "video/quicktime",
            ".avi": "video/x-msvideo", ".mkv": "video/x-matroska",
            ".webm": "video/webm", ".pdf": "application/pdf",
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
            ".doc": "application/msword",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".ppt": "application/vnd.ms-powerpoint",
            ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        }
        _, file_ext = os.path.splitext(file.filename.lower())
        content_type = ext_map.get(file_ext, content_type)

    if content_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail=f"File type '{content_type}' not allowed. "
                   f"Allowed: {list(allowed_types.keys())}"
        )

    # 2. Read file content
    contents = await file.read()

    # 3. Validate file size
    size_mb = len(contents) / (1024 * 1024)
    limit_mb = max_size_mb if max_size_mb is not None else MAX_FILE_SIZE_MB
    if size_mb > limit_mb:
        raise HTTPException(
            status_code=400,
            detail=f"File too large ({size_mb:.1f}MB). Max allowed: {limit_mb}MB"
        )

    # 4. Generate unique filename
    ext = allowed_types.get(content_type, "bin")
    filename = f"{uuid.uuid4().hex}_{file.filename or 'upload'}"
    if not filename.endswith(f".{ext}"):
        filename = f"{uuid.uuid4().hex}.{ext}"

    storage_path = f"{folder}/{filename}"

    # 5. Upload — use GCS when bucket is configured, local disk for dev only
    if getattr(settings, "GCP_BUCKET_NAME", None):
        return await _upload_gcp(contents, storage_path, content_type)
    else:
        return await _upload_local(contents, storage_path)


async def _upload_local(contents: bytes, path: str) -> str:
    """Dev only: save to local filesystem, return a /files/ URL."""
    base_dir = os.path.join(os.path.dirname(__file__), "..", "..", "uploads")
    full_dir = os.path.join(base_dir, os.path.dirname(path))
    os.makedirs(full_dir, exist_ok=True)

    full_path = os.path.join(base_dir, path)
    with open(full_path, "wb") as f:
        f.write(contents)

    # Return a URL that the /files/{path} route can serve
    return f"/files/{path}"


async def _upload_gcp(contents: bytes, path: str, content_type: str) -> str:
    """Production: upload to GCP Cloud Storage using ADC or service account JSON."""
    try:
        from google.cloud import storage as gcs
    except ImportError:
        raise RuntimeError("google-cloud-storage is required: pip install google-cloud-storage")

    # Use service account JSON if provided, otherwise use Application Default Credentials
    # (Cloud Run automatically provides ADC via the instance's service account)
    sa_path = getattr(settings, "FIREBASE_SERVICE_ACCOUNT_PATH", None)
    if sa_path and os.path.exists(sa_path):
        client = gcs.Client.from_service_account_json(sa_path)
    else:
        client = gcs.Client()

    bucket = client.bucket(settings.GCP_BUCKET_NAME)
    blob = bucket.blob(path)
    blob.cache_control = "public, max-age=3600"
    blob.upload_from_string(contents, content_type=content_type)
    # Prefer public URL for category/syllabus images shown in admin + apps.
    # With uniform bucket-level access this is a no-op / may raise — ignore safely.
    try:
        blob.make_public()
    except Exception:
        pass
    return blob.public_url


def generate_signed_url(blob_name: str, content_type: str) -> dict:
    """
    Generates a v4 signed URL for uploading a file directly to GCS.
    """
    try:
        from google.cloud import storage as gcs
    except ImportError:
        raise RuntimeError("google-cloud-storage is required: pip install google-cloud-storage")

    if settings.DEBUG and not getattr(settings, "GCP_BUCKET_NAME", None):
        # Fallback for local testing: we'll simulate a signed URL
        # pointing back to our own server, but for pure S3/GCP flow, 
        # this is where the magic happens.
        return {
            "upload_url": f"http://localhost:8000/api/teacher/auth/mock-upload?path={blob_name}",
            "final_url": f"/files/{blob_name}",
            "fields": {}
        }

    client = gcs.Client()
    bucket = client.bucket(settings.GCP_BUCKET_NAME)
    blob = bucket.blob(blob_name)

    url = blob.generate_signed_url(
        version="v4",
        expiration=datetime.timedelta(minutes=15),
        method="PUT",
        content_type=content_type,
    )

    return {
        "upload_url": url,
        "final_url": blob.public_url,
    }
