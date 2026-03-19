import uuid
import logging
import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

SUPABASE_STORAGE_URL = f"{settings.SUPABASE_URL}/storage/v1" if settings.SUPABASE_URL else ""
BUCKET = "uploads"
_active = bool(settings.SUPABASE_URL and settings.SUPABASE_SERVICE_ROLE_KEY)


def _headers():
    return {
        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}",
        "apikey": settings.SUPABASE_SERVICE_ROLE_KEY,
    }


def _ensure_bucket():
    """Create the storage bucket if it doesn't exist."""
    if not _active:
        return
    try:
        with httpx.Client(timeout=10) as client:
            # Check if bucket exists
            resp = client.get(f"{SUPABASE_STORAGE_URL}/bucket/{BUCKET}", headers=_headers())
            if resp.status_code == 404:
                # Create public bucket
                client.post(
                    f"{SUPABASE_STORAGE_URL}/bucket",
                    headers={**_headers(), "Content-Type": "application/json"},
                    json={"id": BUCKET, "name": BUCKET, "public": True},
                )
                logger.info(f"Created Supabase storage bucket: {BUCKET}")
    except Exception as e:
        logger.warning(f"Bucket check failed: {e}")


# Ensure bucket on import
_ensure_bucket()


def upload_file(file_content: bytes, original_filename: str, content_type: str) -> tuple[str, str]:
    """Upload file to Supabase Storage. Returns (storage_path, public_url)."""
    ext = original_filename.rsplit(".", 1)[-1] if "." in original_filename else "jpg"
    path = f"{uuid.uuid4()}.{ext}"

    if not _active:
        mock_url = f"https://mock-storage.nescora.dev/{BUCKET}/{path}"
        return path, mock_url

    with httpx.Client(timeout=60) as client:
        resp = client.post(
            f"{SUPABASE_STORAGE_URL}/object/{BUCKET}/{path}",
            headers={**_headers(), "Content-Type": content_type},
            content=file_content,
        )
        resp.raise_for_status()

    public_url = f"{settings.SUPABASE_URL}/storage/v1/object/public/{BUCKET}/{path}"
    logger.info(f"Uploaded to Supabase Storage: {path} ({len(file_content)} bytes)")
    return path, public_url


def download_from_url_and_upload(source_url: str, dest_prefix: str = "results") -> tuple[str, str]:
    """Download from external URL and re-upload to Supabase Storage."""
    path = f"{dest_prefix}/{uuid.uuid4()}.jpg"

    if not _active:
        mock_url = f"https://mock-storage.nescora.dev/{BUCKET}/{path}"
        logger.info(f"Mock: would copy {source_url} → {path}")
        return path, mock_url

    with httpx.Client(timeout=60) as client:
        resp = client.get(source_url)
        resp.raise_for_status()
        content = resp.content
        content_type = resp.headers.get("content-type", "image/jpeg")

    # Upload to Supabase
    with httpx.Client(timeout=60) as client:
        resp = client.post(
            f"{SUPABASE_STORAGE_URL}/object/{BUCKET}/{path}",
            headers={**_headers(), "Content-Type": content_type},
            content=content,
        )
        resp.raise_for_status()

    public_url = f"{settings.SUPABASE_URL}/storage/v1/object/public/{BUCKET}/{path}"
    logger.info(f"Copied to Supabase Storage: {path} ({len(content)} bytes)")
    return path, public_url


def generate_presigned_url(key: str, expires_in: int = 3600) -> str:
    """Generate a temporary signed URL for a file."""
    if not _active:
        return f"https://mock-storage.nescora.dev/{BUCKET}/{key}?token=mock"

    with httpx.Client(timeout=10) as client:
        resp = client.post(
            f"{SUPABASE_STORAGE_URL}/object/sign/{BUCKET}/{key}",
            headers={**_headers(), "Content-Type": "application/json"},
            json={"expiresIn": expires_in},
        )
        resp.raise_for_status()
        data = resp.json()
        return f"{settings.SUPABASE_URL}/storage/v1{data['signedURL']}"


def delete_file(key: str) -> None:
    """Delete a file from Supabase Storage."""
    if not _active:
        return

    with httpx.Client(timeout=10) as client:
        client.delete(
            f"{SUPABASE_STORAGE_URL}/object/{BUCKET}",
            headers={**_headers(), "Content-Type": "application/json"},
            json={"prefixes": [key]},
        )
