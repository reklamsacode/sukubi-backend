from pydantic import BaseModel
from datetime import datetime


class ImageResponse(BaseModel):
    id: str
    original_url: str
    file_name: str
    file_size: int
    mime_type: str
    created_at: datetime

    model_config = {"from_attributes": True}


class UploadResponse(BaseModel):
    image_id: str
    thumbnail_url: str
    original_url: str
