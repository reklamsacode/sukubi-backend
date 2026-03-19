import logging
import httpx
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

RESEND_API = "https://api.resend.com/emails"
FROM_EMAIL = "Nescora <onboarding@resend.dev>"  # Use custom domain later


def _send(to: str, subject: str, html: str):
    """Send email via Resend API."""
    if not settings.RESEND_API_KEY:
        logger.info(f"Email skipped (no API key): {subject} → {to}")
        return

    try:
        resp = httpx.post(
            RESEND_API,
            headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}", "Content-Type": "application/json"},
            json={"from": FROM_EMAIL, "to": [to], "subject": subject, "html": html},
            timeout=10,
        )
        resp.raise_for_status()
        logger.info(f"Email sent: {subject} → {to}")
    except Exception as e:
        logger.error(f"Email failed: {e}")


def send_welcome(email: str, name: str):
    _send(email, "Welcome to Nescora! 🏠", f"""
    <div style="font-family:sans-serif;max-width:500px;margin:0 auto;padding:40px 20px">
      <h1 style="font-size:24px;color:#111">Welcome to Nescora, {name or 'there'}!</h1>
      <p style="color:#666;line-height:1.6">Your AI-powered real estate photo platform is ready. You have <strong>3 free credits</strong> to get started.</p>
      <a href="{settings.FRONTEND_URL}/upload" style="display:inline-block;background:#111;color:#fff;padding:12px 24px;border-radius:999px;text-decoration:none;margin-top:16px">Start Enhancing Photos →</a>
      <p style="color:#999;font-size:12px;margin-top:32px">Nescora · AI Photo Enhancement for Real Estate</p>
    </div>
    """)


def send_job_completed(email: str, job_type: str, job_id: str):
    type_labels = {"enhance": "Photo Enhancement", "stage": "Virtual Staging", "remove": "Object Removal", "video": "Video Tour", "voiceover": "AI Voiceover"}
    label = type_labels.get(job_type, job_type)
    _send(email, f"✅ {label} Complete!", f"""
    <div style="font-family:sans-serif;max-width:500px;margin:0 auto;padding:40px 20px">
      <h1 style="font-size:24px;color:#111">Your {label} is Ready!</h1>
      <p style="color:#666;line-height:1.6">Your AI processing job has been completed successfully.</p>
      <a href="{settings.FRONTEND_URL}/editor/{job_id}" style="display:inline-block;background:#111;color:#fff;padding:12px 24px;border-radius:999px;text-decoration:none;margin-top:16px">View Result →</a>
      <p style="color:#999;font-size:12px;margin-top:32px">Nescora · AI Photo Enhancement for Real Estate</p>
    </div>
    """)


def send_job_failed(email: str, job_type: str, error: str):
    _send(email, f"❌ Processing Failed", f"""
    <div style="font-family:sans-serif;max-width:500px;margin:0 auto;padding:40px 20px">
      <h1 style="font-size:24px;color:#111">Processing Failed</h1>
      <p style="color:#666;line-height:1.6">Unfortunately, your {job_type} job encountered an error. Your credits have been refunded.</p>
      <p style="color:#999;font-size:13px;background:#f5f5f5;padding:12px;border-radius:8px">{error[:200]}</p>
      <a href="{settings.FRONTEND_URL}/upload" style="display:inline-block;background:#111;color:#fff;padding:12px 24px;border-radius:999px;text-decoration:none;margin-top:16px">Try Again →</a>
    </div>
    """)


def send_credits_low(email: str, remaining: int):
    _send(email, f"⚠️ {remaining} Credits Remaining", f"""
    <div style="font-family:sans-serif;max-width:500px;margin:0 auto;padding:40px 20px">
      <h1 style="font-size:24px;color:#111">Credits Running Low</h1>
      <p style="color:#666;line-height:1.6">You have <strong>{remaining} credits</strong> remaining. Upgrade your plan for more.</p>
      <a href="{settings.FRONTEND_URL}/pricing" style="display:inline-block;background:#111;color:#fff;padding:12px 24px;border-radius:999px;text-decoration:none;margin-top:16px">Upgrade Plan →</a>
    </div>
    """)


def send_payment_success(email: str, plan: str, amount: str):
    _send(email, f"💳 Payment Confirmed — {plan} Plan", f"""
    <div style="font-family:sans-serif;max-width:500px;margin:0 auto;padding:40px 20px">
      <h1 style="font-size:24px;color:#111">Payment Successful!</h1>
      <p style="color:#666;line-height:1.6">Your <strong>{plan}</strong> plan subscription ({amount}) has been activated. Enjoy unlimited AI tools!</p>
      <a href="{settings.FRONTEND_URL}/dashboard" style="display:inline-block;background:#111;color:#fff;padding:12px 24px;border-radius:999px;text-decoration:none;margin-top:16px">Go to Dashboard →</a>
    </div>
    """)
