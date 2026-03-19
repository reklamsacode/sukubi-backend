import logging
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from jose import jwt, JWTError

from app.config import get_settings
from app.models.base import get_db
from app.models.user import User, PLAN_CREDITS

logger = logging.getLogger(__name__)
security = HTTPBearer(auto_error=False)
settings = get_settings()

# Supabase JWT secret — extracted from SUPABASE_URL or use JWT_SECRET_KEY
# Supabase JWTs are signed with the project's JWT secret
SUPABASE_JWT_SECRET = settings.JWT_SECRET_KEY


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    """
    Validate Supabase JWT token and return/create user.

    Flow:
    1. Decode Supabase JWT → get sub (user UUID) + email + user_metadata
    2. Look up user in our DB
    3. If not found → auto-create from Supabase data (first login sync)
    4. Return user
    """
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    token = credentials.credentials

    try:
        # Supabase JWTs use HS256 with the project's JWT secret
        # For development, also accept our own JWT_SECRET_KEY
        payload = jwt.decode(
            token,
            SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            options={"verify_aud": False},  # Supabase doesn't always set aud
        )
    except JWTError:
        # Try with Supabase service role key as fallback
        try:
            payload = jwt.decode(
                token,
                settings.SUPABASE_SERVICE_ROLE_KEY,
                algorithms=["HS256"],
                options={"verify_aud": False},
            )
        except JWTError:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    # Extract user info from JWT
    sub = payload.get("sub")  # Supabase user UUID
    email = payload.get("email")
    user_metadata = payload.get("user_metadata", {})

    if not sub:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: no sub")

    # Look up user
    user = db.query(User).filter(User.id == sub).first()

    if user is None:
        # Auto-create user on first backend access (Supabase Auth → our DB sync)
        if not email:
            email = user_metadata.get("email", f"{sub}@nescora.app")

        full_name = user_metadata.get("full_name") or user_metadata.get("name")
        avatar_url = user_metadata.get("avatar_url") or user_metadata.get("picture")
        user_type = user_metadata.get("role", "agent")

        user = User(
            id=sub,
            email=email,
            full_name=full_name,
            avatar_url=avatar_url,
            user_type=user_type,
            plan="free",
            credits_remaining=PLAN_CREDITS["free"],
            credits_monthly_limit=PLAN_CREDITS["free"],
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        logger.info(f"Auto-created user {sub} ({email})")

    return user


def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: Session = Depends(get_db),
) -> User | None:
    """Same as get_current_user but returns None if no token."""
    if not credentials:
        return None
    try:
        return get_current_user(credentials, db)
    except HTTPException:
        return None
