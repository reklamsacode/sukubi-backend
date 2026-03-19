from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.models.base import get_db
from app.models.user import User
from app.schemas.user import UserResponse
from app.dependencies import get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me", response_model=UserResponse)
def get_me(user: User = Depends(get_current_user)):
    """Get current user profile. Auto-creates user on first call."""
    return user


@router.patch("/me", response_model=UserResponse)
def update_me(
    updates: dict,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update current user profile."""
    allowed = {"full_name", "avatar_url", "user_type"}
    for key, value in updates.items():
        if key in allowed and value is not None:
            setattr(user, key, value)
    db.commit()
    db.refresh(user)
    return user
