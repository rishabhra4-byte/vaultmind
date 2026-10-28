"""The signed-in person's own settings and first-week checklist."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..config import get_settings
from ..database import get_db
from ..deps import current_user
from ..models import User, UserIdentity
from ..onboarding import checklist
from ..schemas import UTCDatetime

router = APIRouter(prefix="/api", tags=["me"])


class PreferencesOut(BaseModel):
    notify_email: bool
    email_available: bool  # the server can send email
    slack_available: bool  # the Slack app is set up
    slack_linked: bool  # VaultMind knows this person's Slack account


class PreferencesIn(BaseModel):
    notify_email: bool | None = None


def _preferences(db: Session, user: User) -> PreferencesOut:
    s = get_settings()
    linked = db.scalar(select(UserIdentity.id).where(UserIdentity.user_id == user.id, UserIdentity.provider == "slack"))
    return PreferencesOut(
        notify_email=user.notify_email,
        email_available=s.email_configured,
        slack_available=s.slack_configured,
        slack_linked=linked is not None,
    )


@router.get("/me/preferences", response_model=PreferencesOut)
def preferences(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _preferences(db, user)


@router.patch("/me/preferences", response_model=PreferencesOut)
def update_preferences(body: PreferencesIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if body.notify_email is not None:
        user.notify_email = body.notify_email
        audit.record(db, user, "user.preferences", "user", user.id, notify_email=body.notify_email)
        db.commit()
    return _preferences(db, user)


class Step(BaseModel):
    id: str
    label: str
    hint: str = ""
    done: bool
    link: str = ""


class OnboardingOut(BaseModel):
    active: bool
    until: UTCDatetime | None
    steps: list[Step]
    done: int
    total: int


@router.get("/onboarding", response_model=OnboardingOut)
def onboarding(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return checklist(db, user)


@router.post("/onboarding/dismiss", response_model=OnboardingOut)
def dismiss_onboarding(user: User = Depends(current_user), db: Session = Depends(get_db)):
    user.onboarding_until = None
    db.commit()
    return checklist(db, user)
