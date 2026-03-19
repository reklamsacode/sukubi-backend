from sqlalchemy import Column, String, Integer, DateTime, Text
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

from app.models.base import Base, generate_uuid


# Default credits per plan
PLAN_CREDITS = {
    "free": 3,
    "pro": 100,
    "agency": 9999,
}


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=generate_uuid)
    email = Column(String(255), unique=True, index=True, nullable=False)
    full_name = Column(String(255), nullable=True)
    avatar_url = Column(Text, nullable=True)
    user_type = Column(String(50), default="agent")  # agent, owner, other
    plan = Column(String(50), default="free")  # free, pro, agency
    credits_remaining = Column(Integer, default=3)
    credits_monthly_limit = Column(Integer, default=3)
    stripe_customer_id = Column(String(255), nullable=True)
    stripe_subscription_id = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    listings = relationship("Listing", back_populates="owner")
    jobs = relationship("Job", back_populates="user")
    images = relationship("Image", back_populates="user")
    payments = relationship("Payment", back_populates="user")

    def can_afford(self, credits: int) -> bool:
        return self.credits_remaining >= credits

    def deduct_credits(self, credits: int) -> bool:
        if not self.can_afford(credits):
            return False
        self.credits_remaining -= credits
        return True

    def refund_credits(self, credits: int) -> None:
        self.credits_remaining = min(
            self.credits_remaining + credits,
            self.credits_monthly_limit,
        )

    def reset_credits(self) -> None:
        """Reset credits for a new billing cycle."""
        self.credits_remaining = self.credits_monthly_limit
