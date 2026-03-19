import uuid
import logging
import boto3
import httpx
from botocore.config import Config as BotoConfig

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Mock mode when R2 is not configured
_mock_mode = not settings.R2_ENDPOINT


def get_r2_client():
    return boto3.client(
        "s3",
        endpoint_url=settings.R2_ENDPOINT,
        aws_access_key_id=settings.R2_ACCESS_KEY,
        aws_secret_access_key=settings.R2_SECRET_KEY,
        config=BotoConfig(signature_version="s3v4"),
        region_name="auto",
    )


def upload_file(file_content: bytes, original_filename: str, content_type: str) -> tuple[str, str]:
    """Upload file bytes to R2. Returns (s3_key, public_url)."""
    ext = original_filename.rsplit(".", 1)[-1] if "." in original_filename else "jpg"
    key = f"uploads/{uuid.uuid4()}.{ext}"

    if _mock_mode:
        mock_url = f"https://mock-r2.nescora.dev/{key}"
        return key, mock_url

    client = get_r2_client()
    client.put_object(
        Bucket=settings.R2_BUCKET,
        Key=key,
        Body=file_content,
        ContentType=content_type,
    )

    public_url = f"{settings.R2_PUBLIC_URL}/{key}"
    return key, public_url


def download_from_url_and_upload(source_url: str, dest_prefix: str = "results") -> tuple[str, str]:
    """
    Download an image from an external URL (e.g. Replicate CDN)
    and re-upload it to R2 for permanent storage.

    Returns (s3_key, public_url).
    """
    key = f"{dest_prefix}/{uuid.uuid4()}.jpg"

    if _mock_mode:
        mock_url = f"https://mock-r2.nescora.dev/{key}"
        logger.info(f"Mock: would copy {source_url} → {key}")
        return key, mock_url

    # Download from source
    with httpx.Client(timeout=60.0) as client:
        resp = client.get(source_url)
        resp.raise_for_status()
        content = resp.content
        content_type = resp.headers.get("content-type", "image/jpeg")

    # Upload to R2
    r2 = get_r2_client()
    r2.put_object(
        Bucket=settings.R2_BUCKET,
        Key=key,
        Body=content,
        ContentType=content_type,
    )

    public_url = f"{settings.R2_PUBLIC_URL}/{key}"
    logger.info(f"Copied result to R2: {key} ({len(content)} bytes)")
    return key, public_url


def generate_presigned_url(key: str, expires_in: int = 3600) -> str:
    """Generate a temporary presigned URL for a file."""
    if _mock_mode:
        return f"https://mock-r2.nescora.dev/{key}?token=mock-presigned"

    client = get_r2_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.R2_BUCKET, "Key": key},
        ExpiresIn=expires_in,
    )


def delete_file(key: str) -> None:
    """Delete a file from R2."""
    if _mock_mode:
        return

    client = get_r2_client()
    client.delete_object(Bucket=settings.R2_BUCKET, Key=key)
