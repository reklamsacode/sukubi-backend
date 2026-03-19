import logging
from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session
from pydantic import BaseModel
from datetime import datetime, timedelta, timezone
import random

from app.models.base import get_db
from app.models.user import User
from app.dependencies import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/analytics", tags=["analytics"])


# ── Response schemas ──

class OverviewResponse(BaseModel):
    total_listings: int
    total_views: int
    avg_engagement: float
    credits_remaining: int
    credits_monthly_limit: int


class DailyViews(BaseModel):
    date: str
    views: int
    clicks: int


class ListingPerformance(BaseModel):
    id: str
    title: str
    views: int
    clicks: int
    ctr: float  # click-through rate percentage
    created_at: str
    enhanced: bool


# ── Mock data generators ──

def _mock_overview(user: User) -> OverviewResponse:
    return OverviewResponse(
        total_listings=12,
        total_views=3847,
        avg_engagement=4.2,
        credits_remaining=user.credits_remaining,
        credits_monthly_limit=user.credits_monthly_limit,
    )


def _mock_daily_views(days: int) -> list[DailyViews]:
    result = []
    now = datetime.now(timezone.utc)
    base_views = 80
    for i in range(days):
        d = now - timedelta(days=days - 1 - i)
        # Add some realistic variation
        weekday_boost = 1.3 if d.weekday() < 5 else 0.7
        trend = 1 + (i / days) * 0.3  # slight upward trend
        views = int(base_views * weekday_boost * trend * (0.7 + random.random() * 0.6))
        clicks = int(views * (0.03 + random.random() * 0.04))
        result.append(DailyViews(
            date=d.strftime("%Y-%m-%d"),
            views=views,
            clicks=clicks,
        ))
    return result


def _mock_listings() -> list[ListingPerformance]:
    listings = [
        ("lst-1", "Modern Apartment, Downtown", 1247, True, "2026-02-15"),
        ("lst-2", "Villa with Garden, Suburbs", 892, True, "2026-02-20"),
        ("lst-3", "Sea View Penthouse", 634, True, "2026-03-01"),
        ("lst-4", "Cozy Studio, City Center", 421, False, "2026-03-05"),
        ("lst-5", "Family Home, Green District", 312, True, "2026-03-08"),
        ("lst-6", "Luxury Loft, Art Quarter", 189, True, "2026-03-10"),
        ("lst-7", "Budget Flat, East Side", 87, False, "2026-03-12"),
        ("lst-8", "Renovated Duplex", 65, True, "2026-03-14"),
    ]
    result = []
    for lid, title, views, enhanced, created in listings:
        clicks = int(views * (0.04 + random.random() * 0.03))
        ctr = round((clicks / views) * 100, 1) if views > 0 else 0
        result.append(ListingPerformance(
            id=lid,
            title=title,
            views=views,
            clicks=clicks,
            ctr=ctr,
            created_at=created,
            enhanced=enhanced,
        ))
    return result


# ── Endpoints ──

@router.get("/overview", response_model=OverviewResponse)
def get_overview(
    user: User = Depends(get_current_user),
):
    # In production: query DB for real analytics
    return _mock_overview(user)


@router.get("/views", response_model=list[DailyViews])
def get_views(
    period: str = Query(default="30d", pattern="^(7d|30d|90d)$"),
    user: User = Depends(get_current_user),
):
    days = {"7d": 7, "30d": 30, "90d": 90}[period]
    return _mock_daily_views(days)


@router.get("/listings", response_model=list[ListingPerformance])
def get_listing_performance(
    user: User = Depends(get_current_user),
):
    return _mock_listings()


# ── Tracking pixel ──

PIXEL_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
    b"\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
    b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


@router.get("/t/{listing_id}")
def track_view(listing_id: str):
    """1x1 transparent pixel for view tracking."""
    # In production: insert AnalyticsEvent(listing_id, "view") into DB
    logger.info(f"View tracked: {listing_id}")
    return Response(
        content=PIXEL_PNG,
        media_type="image/png",
        headers={"Cache-Control": "no-cache, no-store"},
    )
