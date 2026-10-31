"""Signed-in sessions.

Every sign-in creates a session row; the token handed to the browser carries a random
session id, and every request checks that session is still open. That's what lets VaultMind
end sessions: on sign-out, when a password changes, when someone is deactivated or loses
administrator rights, when an administrator ends them, and after a period without activity
(VAULTMIND_SESSION_IDLE_MINUTES).

Only the SHA-256 of the session id is stored, so a copy of the database can't be used to
sign in as anyone.
"""

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from . import audit
from .config import get_settings
from .models import User, UserSession, utcnow
from .security import create_access_token, decode_access_token

TOUCH_EVERY = timedelta(seconds=60)  # how often "last seen" is written, at most

# Why a session ended, as the person sees it when their next request is refused.
END_MESSAGES = {
    "signed_out": "You signed out. Sign in again to continue.",
    "password_changed": "You were signed out because the password was changed.",
    "deactivated": "This account has been deactivated.",
    "role_changed": "You were signed out because your administrator rights changed.",
    "ended_by_admin": "An administrator signed you out.",
    "ended_by_user": "You were signed out from another browser.",
    "idle": "You were signed out after a period without activity.",
}


class SessionInvalid(Exception):
    """The token doesn't belong to an open session. The message is safe to show."""


@dataclass
class Auth:
    user: User
    session: UserSession


def digest(session_id: str) -> str:
    return hashlib.sha256(session_id.encode()).hexdigest()


def aware(dt: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; everything is stored in UTC."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def client_ip(request: Request | None) -> str:
    return (request.client.host if request and request.client else "")[:64]


def start(db: Session, user: User, request: Request | None, method: str) -> str:
    """Open a session for a successful sign-in and return its token. The caller commits."""
    session_id = secrets.token_urlsafe(32)
    now = utcnow()
    expires = now + timedelta(minutes=get_settings().jwt_expire_minutes)
    agent = request.headers.get("user-agent", "") if request else ""
    db.add(
        UserSession(
            id=digest(session_id),
            user_id=user.id,
            method=method,
            ip=client_ip(request),
            user_agent=agent[:255],
            created_at=now,
            last_seen_at=now,
            expires_at=expires,
        )
    )
    return create_access_token(user.id, session_id, expires)


def authenticate(db: Session, token: str, *, touch: bool = True) -> Auth:
    """The open session and active user behind a token, or SessionInvalid."""
    claims = decode_access_token(token)
    if claims is None:
        raise SessionInvalid("Your session has expired. Sign in again.")
    user_id, session_id = claims
    session = db.get(UserSession, digest(session_id))
    now = utcnow()
    if session is None or session.user_id != user_id:
        raise SessionInvalid("Your session has expired. Sign in again.")
    if session.ended_at is not None:
        raise SessionInvalid(END_MESSAGES.get(session.end_reason, "You were signed out. Sign in again to continue."))
    if aware(session.expires_at) <= now:
        raise SessionInvalid("Your session has expired. Sign in again.")
    idle = get_settings().session_idle_minutes
    if idle and aware(session.last_seen_at) + timedelta(minutes=idle) < now:
        session.ended_at, session.end_reason = now, "idle"
        db.commit()
        raise SessionInvalid(f"You were signed out after {idle} minutes without activity.")
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise SessionInvalid("This account is disabled.")
    if touch and now - aware(session.last_seen_at) >= TOUCH_EVERY:
        session.last_seen_at = now
        db.commit()
    return Auth(user=user, session=session)


def end(db: Session, session: UserSession, reason: str) -> None:
    if session.ended_at is None:
        session.ended_at, session.end_reason = utcnow(), reason


def end_all(db: Session, user_id: int, reason: str, *, keep: str | None = None, actor: User | None = None) -> int:
    """End every open session of one person (except `keep`). The caller commits."""
    stmt = (
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.ended_at.is_(None))
        .values(ended_at=utcnow(), end_reason=reason)
        .execution_options(synchronize_session="fetch")
    )
    if keep is not None:
        stmt = stmt.where(UserSession.id != keep)
    count = db.execute(stmt).rowcount or 0
    if count:
        audit.record(db, actor, "session.end", "user", user_id, reason=reason, sessions=count)
    return count


def open_sessions(db: Session, user_id: int) -> list[UserSession]:
    now = utcnow()
    rows = db.scalars(
        select(UserSession)
        .where(UserSession.user_id == user_id, UserSession.ended_at.is_(None))
        .order_by(UserSession.last_seen_at.desc())
    )
    idle = get_settings().session_idle_minutes
    out = []
    for s in rows:
        if aware(s.expires_at) <= now:
            continue
        if idle and aware(s.last_seen_at) + timedelta(minutes=idle) < now:
            continue
        out.append(s)
    return out


_BROWSERS = [("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"), ("Safari/", "Safari")]
_SYSTEMS = [
    ("iPhone", "iPhone"),
    ("iPad", "iPad"),
    ("Android", "Android"),
    ("Windows", "Windows"),
    ("Mac OS X", "macOS"),
    ("CrOS", "ChromeOS"),
    ("Linux", "Linux"),
]


def describe_agent(agent: str) -> str:
    """'Chrome on Windows', from a User-Agent header, for the sessions list."""
    if not agent:
        return "Unknown browser"
    browser = next((name for marker, name in _BROWSERS if marker in agent), "")
    system = next((name for marker, name in _SYSTEMS if marker in agent), "")
    if not browser and not system:
        return re.split(r"[/ ]", agent, maxsplit=1)[0][:40] or "Unknown browser"
    return f"{browser or 'Browser'} on {system}" if system else browser
