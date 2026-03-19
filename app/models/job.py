from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

from app.models.base import Base, generate_uuid


# Credits cost per job type
JOB_CREDITS = {
    "enhance": 1,
    "stage": 2,
    "remove": 2,
    "video": 3,
    "voiceover": 2,
}


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    type = Column(String(50), nullable=False)  # enhance, stage, remove, video, voiceover
    status = Column(String(50), default="pending", index=True)  # pending, processing, completed, failed
    progress = Column(Integer, default=0)
    input_data = Column(JSONB, nullable=True)  # { imageIds, settings }
    result_url = Column(Text, nullable=True)
    result_s3_key = Column(String(500), nullable=True)
    error_message = Column(Text, nullable=True)
    credits_used = Column(Integer, default=0)
    processing_time_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="jobs")
