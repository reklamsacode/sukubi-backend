from sqlalchemy import Column, String, DateTime, ForeignKey, Text
from datetime import datetime, timezone

from app.models.base import Base, generate_uuid


class AnalyticsEvent(Base):
    __tablename__ = "analytics_events"

    id = Column(String, primary_key=True, default=generate_uuid)
    listing_id = Column(String, ForeignKey("listings.id"), nullable=False, index=True)
    event_type = Column(String(50), nullable=False)  # view, click, share, download
    source = Column(String(100), nullable=True)  # direct, social, email
    ip_address = Column(String(45), nullable=True)  # IPv4/IPv6
    user_agent = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
