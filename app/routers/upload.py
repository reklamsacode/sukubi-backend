from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from sqlalchemy.orm import Session

from app.models.base import get_db
from app.models.image import Image
from app.models.user import User
from app.schemas.image import UploadResponse
from app.services.storage import upload_file
from app.dependencies import get_current_user

router = APIRouter(prefix="/upload", tags=["upload"])

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_SIZE_BYTES = 10 * 1024 * 1024  # 10MB


@router.post("", response_model=UploadResponse)
async def upload_image(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Validate content type
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail="Only JPEG, PNG, and WebP images are allowed")

    # Read and validate size
    contents = await file.read()
    if len(contents) > MAX_SIZE_BYTES:
        raise HTTPException(status_code=400, detail="File size exceeds 10MB limit")

    # Upload to R2
    s3_key, public_url = upload_file(contents, file.filename or "upload.jpg", file.content_type)

    # Save to database
    image = Image(
        user_id=user.id,
        original_url=public_url,
        s3_key=s3_key,
        file_name=file.filename or "upload.jpg",
        file_size=len(contents),
        mime_type=file.content_type,
    )
    db.add(image)
    db.commit()
    db.refresh(image)

    return UploadResponse(
        image_id=image.id,
        thumbnail_url=public_url,  # In production, generate a resized thumbnail
        original_url=public_url,
    )
