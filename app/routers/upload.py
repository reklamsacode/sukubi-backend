from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.models.base import get_db
from app.models.image import Image
from app.models.user import User
from app.schemas.image import UploadResponse
from app.services.storage import upload_file
from app.dependencies import get_current_user

router = APIRouter(prefix="/upload", tags=["upload"])

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}
MAX_FILES_PER_REQUEST = 40


@router.post("", response_model=UploadResponse)
async def upload_image(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Validate content type — images only
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail="Only image files are allowed (JPEG, PNG, WebP, HEIC)")

    # Read file — no size limit
    contents = await file.read()

    # Upload to storage
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
        thumbnail_url=public_url,
        original_url=public_url,
    )


@router.post("/bulk", response_model=List[UploadResponse])
async def upload_bulk(
    files: List[UploadFile] = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Upload up to 40 images at once."""
    if len(files) > MAX_FILES_PER_REQUEST:
        raise HTTPException(status_code=400, detail=f"Maximum {MAX_FILES_PER_REQUEST} files per upload")

    results = []
    for file in files:
        if file.content_type not in ALLOWED_TYPES:
            continue  # Skip non-image files silently

        contents = await file.read()
        s3_key, public_url = upload_file(contents, file.filename or "upload.jpg", file.content_type)

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

        results.append(UploadResponse(
            image_id=image.id,
            thumbnail_url=public_url,
            original_url=public_url,
        ))

    return results
