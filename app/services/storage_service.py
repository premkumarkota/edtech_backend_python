"""
Storage Service
Handles file uploads to GCP Cloud Storage.
Falls back to local temp storage in development (DEBUG=True).

Usage:
    url = await upload_file(file, folder="syllabus/1")
"""

import os
import uuid
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

MAX_FILE_SIZE_MB = 20


async def upload_file(
    file: UploadFile,
    folder: str,
    allowed_types: dict = None,
) -> str:
    """
    Upload a file and return its public or signed URL.
    In DEBUG mode: saves to local /tmp/edtech_uploads/ and returns a local URL.
    In production: uploads to GCP Cloud Storage.
    """
    if allowed_types is None:
        allowed_types = {**ALLOWED_DOCUMENT_TYPES, **ALLOWED_EXCEL_TYPES}

    # 1. Validate content type
    content_type = file.content_type or ""
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
    if size_mb > MAX_FILE_SIZE_MB:
        raise HTTPException(
            status_code=400,
            detail=f"File too large ({size_mb:.1f}MB). Max allowed: {MAX_FILE_SIZE_MB}MB"
        )

    # 4. Generate unique filename
    ext = allowed_types.get(content_type, "bin")
    filename = f"{uuid.uuid4().hex}_{file.filename or 'upload'}"
    if not filename.endswith(f".{ext}"):
        filename = f"{uuid.uuid4().hex}.{ext}"

    storage_path = f"{folder}/{filename}"

    # 5. Upload
    if settings.DEBUG or not getattr(settings, "GCP_BUCKET_NAME", None):
        return await _upload_local(contents, storage_path)
    else:
        return await _upload_gcp(contents, storage_path, content_type)


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
    """Production: upload to GCP Cloud Storage."""
    try:
        from google.cloud import storage as gcs
    except ImportError:
        raise RuntimeError("google-cloud-storage is required: pip install google-cloud-storage")

    client = gcs.Client()
    bucket = client.bucket(settings.GCP_BUCKET_NAME)
    blob = bucket.blob(path)
    blob.upload_from_string(contents, content_type=content_type)
    blob.make_public()
    return blob.public_url
