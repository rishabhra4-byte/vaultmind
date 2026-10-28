"""The signed-in person's own settings, sessions, password and first-week checklist."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, sessions
from ..config import get_settings
from ..database import get_db
from ..deps import current_auth, current_user, login_limiter_account
from ..models import User, UserIdentity, UserSession
from ..onboarding import checklist
from ..schemas import SessionOut, UTCDatetime
from ..security import hash_password, verify_password
from ..sessions import Auth

MIN_PASSWORD = 12

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


# ---------------------------------------------------------------- sessions and password


@router.get("/me/sessions", response_model=list[SessionOut])
def my_sessions(auth: Auth = Depends(current_auth), db: Session = Depends(get_db)):
    return [SessionOut.of(s, auth.session.id) for s in sessions.open_sessions(db, auth.user.id)]


@router.delete("/me/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def end_my_session(session_id: str, auth: Auth = Depends(current_auth), db: Session = Depends(get_db)):
    target = db.get(UserSession, session_id)
    if target is None or target.user_id != auth.user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    sessions.end(db, target, "signed_out" if target.id == auth.session.id else "ended_by_user")
    audit.record(db, auth.user, "session.end", "user", auth.user.id, reason="ended_by_user", sessions=1)
    db.commit()


@router.post("/me/sessions/end-others")
def end_my_other_sessions(auth: Auth = Depends(current_auth), db: Session = Depends(get_db)):
    ended = sessions.end_all(db, auth.user.id, "ended_by_user", keep=auth.session.id, actor=auth.user)
    db.commit()
    return {"ended": ended}


class PasswordChangeIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


@router.post("/me/password")
def change_password(body: PasswordChangeIn, auth: Auth = Depends(current_auth), db: Session = Depends(get_db)):
    """Change your own password. Every other session ends; this one stays signed in."""
    user = auth.user
    if user.password_hash in ("", "!"):
        raise HTTPException(status.HTTP_409_CONFLICT, "You sign in with your company account, so there's no password to change")
    key = user.email.lower()
    login_limiter_account.ensure_allowed(key)  # wrong current passwords count like failed sign-ins
    if not verify_password(body.current_password, user.password_hash):
        login_limiter_account.record(key)
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your current password isn't right")
    new = body.new_password
    if len(new) < MIN_PASSWORD:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Use at least {MIN_PASSWORD} characters")
    if len(new.encode()) > 72:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Passwords are limited to 72 bytes")
    if new == body.current_password:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose a password different from the current one")
    user.password_hash = hash_password(new)
    audit.record(db, user, "auth.password_change", "user", user.id)
    ended = sessions.end_all(db, user.id, "password_changed", keep=auth.session.id, actor=user)
    db.commit()
    return {"ended": ended}
