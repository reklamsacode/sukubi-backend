from pydantic import BaseModel
from datetime import datetime


class EnhanceSettings(BaseModel):
    level: int = 1
    style: str = "natural"
    output_size: str = "original"


class StagingSettings(BaseModel):
    room_type: str = "living_room"
    furniture_style: str = "modern"
    remove_existing: bool = False
    keep_decorations: bool = True


class VideoSettings(BaseModel):
    motion_type: str = "pan_right"
    duration: int = 5


class VoiceoverSettings(BaseModel):
    text: str
    voice: str = "alloy"
    language: str = "en"
    speed: float = 1.0
    attach_to_video: str | None = None


class JobCreateRequest(BaseModel):
    image_ids: list[str]
    settings: EnhanceSettings = EnhanceSettings()


class StagingJobCreateRequest(BaseModel):
    image_ids: list[str]
    settings: StagingSettings = StagingSettings()


class VideoJobCreateRequest(BaseModel):
    image_ids: list[str]
    settings: VideoSettings = VideoSettings()


class VoiceoverJobCreateRequest(BaseModel):
    settings: VoiceoverSettings


class RemovalJobCreateRequest(BaseModel):
    image_ids: list[str]
    mask_data: dict


class BulkEnhanceRequest(BaseModel):
    image_ids: list[str]
    listing_id: str | None = None  # optional: attach results to a listing


class JobResponse(BaseModel):
    id: str
    type: str
    status: str
    progress: int
    input_data: dict | None
    result_url: str | None
    credits_used: int
    error_message: str | None
    processing_time_ms: int | None
    created_at: datetime
    completed_at: datetime | None

    model_config = {"from_attributes": True}
