import logging
import stripe
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.user import User, PLAN_CREDITS

logger = logging.getLogger(__name__)
settings = get_settings()

stripe.api_key = settings.STRIPE_SECRET_KEY

PLAN_PRICE_MAP = {
    "pro": settings.STRIPE_PRICE_PRO,
    "agency": settings.STRIPE_PRICE_AGENCY,
}

PLAN_FROM_PRICE: dict[str, str] = {}
if settings.STRIPE_PRICE_PRO:
    PLAN_FROM_PRICE[settings.STRIPE_PRICE_PRO] = "pro"
if settings.STRIPE_PRICE_AGENCY:
    PLAN_FROM_PRICE[settings.STRIPE_PRICE_AGENCY] = "agency"

_mock = not settings.STRIPE_SECRET_KEY


def create_checkout_session(user: User, plan: str, success_url: str, cancel_url: str) -> str:
    if plan not in PLAN_PRICE_MAP:
        raise ValueError(f"Invalid plan: {plan}")

    price_id = PLAN_PRICE_MAP[plan]
    if not price_id:
        raise ValueError(f"Stripe price not configured for plan: {plan}")

    if _mock:
        logger.info(f"Mock: creating checkout for user {user.id}, plan={plan}")
        return f"{settings.FRONTEND_URL}/dashboard?payment=success&plan={plan}&mock=true"

    if not user.stripe_customer_id:
        customer = stripe.Customer.create(
            email=user.email,
            name=user.full_name,
            metadata={"user_id": user.id},
        )
        user.stripe_customer_id = customer.id

    session = stripe.checkout.Session.create(
        customer=user.stripe_customer_id,
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"user_id": user.id, "plan": plan},
        subscription_data={"metadata": {"user_id": user.id, "plan": plan}},
    )

    return session.url


def handle_checkout_completed(session_data: dict, db: Session):
    user_id = session_data.get("metadata", {}).get("user_id")
    plan = session_data.get("metadata", {}).get("plan")
    subscription_id = session_data.get("subscription")
    customer_id = session_data.get("customer")

    if not user_id or not plan:
        return

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return

    user.plan = plan
    user.credits_remaining = PLAN_CREDITS.get(plan, 3)
    user.credits_monthly_limit = PLAN_CREDITS.get(plan, 3)
    user.stripe_customer_id = customer_id
    user.stripe_subscription_id = subscription_id
    db.commit()

    logger.info(f"User {user_id} upgraded to {plan}")

    # Send payment email
    try:
        from app.services.email_service import send_payment_success
        amount = "$29" if plan == "pro" else "$79"
        send_payment_success(user.email, plan.capitalize(), amount)
    except Exception:
        pass


def handle_invoice_paid(invoice: dict, db: Session):
    subscription_id = invoice.get("subscription")
    if not subscription_id:
        return

    user = db.query(User).filter(User.stripe_subscription_id == subscription_id).first()
    if not user:
        return

    user.credits_remaining = user.credits_monthly_limit
    db.commit()
    logger.info(f"Credits renewed for user {user.id}")


def handle_subscription_deleted(subscription: dict, db: Session):
    subscription_id = subscription.get("id")
    user = db.query(User).filter(User.stripe_subscription_id == subscription_id).first()
    if not user:
        return

    user.plan = "free"
    user.credits_remaining = PLAN_CREDITS["free"]
    user.credits_monthly_limit = PLAN_CREDITS["free"]
    user.stripe_subscription_id = None
    db.commit()
    logger.info(f"User {user.id} downgraded to free")


def cancel_subscription(user: User, db: Session) -> bool:
    if not user.stripe_subscription_id:
        return False

    if _mock:
        user.plan = "free"
        user.credits_remaining = PLAN_CREDITS["free"]
        user.credits_monthly_limit = PLAN_CREDITS["free"]
        user.stripe_subscription_id = None
        db.commit()
        return True

    try:
        stripe.Subscription.modify(user.stripe_subscription_id, cancel_at_period_end=True)
        return True
    except stripe.StripeError as e:
        logger.error(f"Failed to cancel subscription: {e}")
        return False


def get_subscription_status(user: User) -> dict:
    base = {
        "plan": user.plan,
        "credits_remaining": user.credits_remaining,
        "credits_monthly_limit": user.credits_monthly_limit,
        "cancel_at_period_end": False,
        "current_period_end": None,
    }

    if _mock or not user.stripe_subscription_id:
        return base

    try:
        sub = stripe.Subscription.retrieve(user.stripe_subscription_id)
        base["cancel_at_period_end"] = sub.cancel_at_period_end
        base["current_period_end"] = sub.current_period_end
    except stripe.StripeError:
        pass

    return base
