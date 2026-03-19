from pydantic import BaseModel
from datetime import datetime
from decimal import Decimal


class ListingCreate(BaseModel):
    title: str
    description: str | None = None
    address: str | None = None
    price: Decimal | None = None
    property_type: str | None = None
    images: list[dict] | None = None  # [{ imageId, enhanced, order }]
    video_job_id: str | None = None
    voiceover_job_id: str | None = None


class ListingUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    address: str | None = None
    price: Decimal | None = None
    property_type: str | None = None
    images: list[dict] | None = None
    video_job_id: str | None = None
    voiceover_job_id: str | None = None
    status: str | None = None


class ListingResponse(BaseModel):
    id: str
    title: str | None
    description: str | None
    address: str | None
    price: Decimal | None
    property_type: str | None
    images: list[dict] | None
    video_job_id: str | None
    voiceover_job_id: str | None
    status: str
    share_token: str
    created_at: datetime

    model_config = {"from_attributes": True}
