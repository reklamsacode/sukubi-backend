from sqlalchemy import Column, String, Integer, Float, DateTime, ForeignKey, Text, Numeric
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
import secrets

from app.models.base import Base, generate_uuid


def generate_share_token() -> str:
    return secrets.token_urlsafe(16)


class Listing(Base):
    __tablename__ = "listings"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(500), nullable=True)
    description = Column(Text, nullable=True)
    address = Column(Text, nullable=True)
    price = Column(Numeric(12, 2), nullable=True)
    property_type = Column(String(100), nullable=True)  # apartment, house, villa, commercial
    images = Column(JSONB, nullable=True)  # [{ imageId, enhanced, order }]
    video_job_id = Column(String, ForeignKey("jobs.id"), nullable=True)
    voiceover_job_id = Column(String, ForeignKey("jobs.id"), nullable=True)
    status = Column(String(50), default="draft")  # draft, published
    share_token = Column(String(100), unique=True, default=generate_share_token)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    owner = relationship("User", back_populates="listings")
    video_job = relationship("Job", foreign_keys=[video_job_id])
    voiceover_job = relationship("Job", foreign_keys=[voiceover_job_id])
