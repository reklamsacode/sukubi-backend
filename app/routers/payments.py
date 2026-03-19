import logging
import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.config import get_settings
from app.models.base import get_db
from app.models.user import User
from app.dependencies import get_current_user
from app.services.stripe_service import (
    create_checkout_session,
    handle_checkout_completed,
    handle_invoice_paid,
    handle_subscription_deleted,
    cancel_subscription,
    get_subscription_status,
)

logger = logging.getLogger(__name__)
settings = get_settings()
router = APIRouter(prefix="/payments", tags=["payments"])


class CheckoutRequest(BaseModel):
    plan: str


class CheckoutResponse(BaseModel):
    checkout_url: str


class SubscriptionResponse(BaseModel):
    plan: str
    credits_remaining: int
    credits_monthly_limit: int
    cancel_at_period_end: bool
    current_period_end: int | None


@router.post("/create-checkout", response_model=CheckoutResponse)
def create_checkout(
    data: CheckoutRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if data.plan not in ("pro", "agency"):
        raise HTTPException(status_code=400, detail="Plan must be 'pro' or 'agency'")

    if user.plan == data.plan:
        raise HTTPException(status_code=400, detail="Already on this plan")

    try:
        url = create_checkout_session(
            user=user,
            plan=data.plan,
            success_url=f"{settings.FRONTEND_URL}/dashboard?payment=success",
            cancel_url=f"{settings.FRONTEND_URL}/pricing?payment=cancelled",
        )
        db.commit()
        return CheckoutResponse(checkout_url=url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    body = await request.body()
    sig = request.headers.get("stripe-signature", "")

    if not settings.STRIPE_WEBHOOK_SECRET:
        import json
        event = json.loads(body)
    else:
        try:
            event = stripe.Webhook.construct_event(body, sig, settings.STRIPE_WEBHOOK_SECRET)
        except stripe.SignatureVerificationError:
            raise HTTPException(status_code=400, detail="Invalid webhook signature")

    event_type = event.get("type", "")
    event_data = event.get("data", {}).get("object", {})

    if event_type == "checkout.session.completed":
        handle_checkout_completed(event_data, db)
    elif event_type == "invoice.paid":
        handle_invoice_paid(event_data, db)
    elif event_type == "customer.subscription.deleted":
        handle_subscription_deleted(event_data, db)

    return {"status": "ok"}


@router.get("/subscription", response_model=SubscriptionResponse)
def get_subscription(user: User = Depends(get_current_user)):
    return get_subscription_status(user)


@router.post("/cancel")
def cancel_sub(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.plan == "free":
        raise HTTPException(status_code=400, detail="No active subscription")

    if not cancel_subscription(user, db):
        raise HTTPException(status_code=500, detail="Failed to cancel subscription")

    return {"status": "cancelled"}
