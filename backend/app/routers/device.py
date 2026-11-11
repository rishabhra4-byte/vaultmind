"""Signing in an editor (the VS Code extension) without giving it a password.

Like signing in to GitHub's CLI: the editor asks for a code, shows it, and opens
<VaultMind>/connect?code=…; the person, signed in to VaultMind in their browser (by password or
company sign-in), checks the code matches and approves; the editor, which has been asking
every few seconds, then gets an ordinary session of its own. That session shows in the person's
sessions list as "VS Code extension" and ends like any other (sign out, password change, idle
time, an administrator).

- The editor's secret (the device code) is 32 random bytes; only its SHA-256 is stored.
- The code the person reads is 8 letters from an alphabet without look-alikes, and only works
  for someone already signed in; requests expire after 10 minutes and are used once.
- The approval page says which app asked, from which network address and when, so a code
  someone else sent you is easy to spot; approving and denying are logged.
"""

import hashlib
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, metrics, sessions
from ..config import get_settings
from ..database import get_db
from ..deps import RateLimiter, current_user
from ..models import DeviceSignIn, User, utcnow
from ..schemas import TokenOut, UserOut
from ..sessions import aware, client_ip

router = APIRouter(prefix="/api/auth/device", tags=["auth"])

LIFETIME = timedelta(minutes=10)
INTERVAL = 2  # seconds the editor waits between asks
ALPHABET = "BCDFGHJKLMNPQRSTVWXZ"  # no vowels (no words), no look-alikes
start_limiter = RateLimiter(limit=20, window_seconds=600, message="Too many sign-in requests: try again in {retry}s")


def _digest(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _user_code() -> str:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def normal_code(code: str) -> str:
    """'kqxw mrtd', 'KQXWMRTD' and 'KQXW-MRTD' are the same code."""
    letters = "".join(c for c in code.upper() if c.isalpha())
    return f"{letters[:4]}-{letters[4:8]}" if len(letters) == 8 else ""


class StartIn(BaseModel):
    client: str = Field("VS Code", max_length=60)


@router.post("")
def start(body: StartIn, request: Request, db: Session = Depends(get_db)):
    """An editor asks to sign in. Returns its secret device code (keep it) and the code to show."""
    ip = client_ip(request)
    start_limiter.check(ip)
    device_code = secrets.token_urlsafe(32)
    for _ in range(5):
        user_code = _user_code()
        if db.scalar(select(DeviceSignIn.id).where(DeviceSignIn.user_code == user_code)) is None:
            break
    now = utcnow()
    db.add(
        DeviceSignIn(
            id=_digest(device_code),
            user_code=user_code,
            client=body.client.strip() or "VS Code",
            ip=ip,
            created_at=now,
            expires_at=now + LIFETIME,
            status="pending",
        )
    )
    db.commit()
    page = get_settings().link("/connect") if get_settings().public_url else str(request.base_url).rstrip("/") + "/connect"
    return {
        "device_code": device_code,
        "user_code": user_code,
        "verification_uri": page,
        "verification_uri_complete": f"{page}?code={user_code}",
        "expires_in": int(LIFETIME.total_seconds()),
        "interval": INTERVAL,
    }


def _pending(db: Session, code: str) -> DeviceSignIn:
    row = db.scalar(select(DeviceSignIn).where(DeviceSignIn.user_code == normal_code(code))) if normal_code(code) else None
    if row is None or aware(row.expires_at) <= utcnow():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That code isn't valid any more. Start signing in again in your editor.")
    if row.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, "That code has already been used.")
    return row


@router.get("/{code}")
def describe(code: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """What's asking, for the approval page (only for someone signed in)."""
    row = _pending(db, code)
    return {
        "user_code": row.user_code,
        "client": row.client,
        "ip": row.ip,
        "requested_at": aware(row.created_at).isoformat(),
        "expires_at": aware(row.expires_at).isoformat(),
    }


class DecideIn(BaseModel):
    allow: bool


@router.post("/{code}/decide")
def decide(code: str, body: DecideIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = _pending(db, code)
    row.status = "approved" if body.allow else "denied"
    row.user_id = user.id
    row.decided_at = utcnow()
    audit.record(
        db, user, "auth.device_approved" if body.allow else "auth.device_denied", "user", user.id, client=row.client, ip=row.ip
    )
    db.commit()
    return {"status": row.status}


class TokenIn(BaseModel):
    device_code: str = Field(min_length=20, max_length=100)


@router.post("/token", response_model=TokenOut)
def token(body: TokenIn, request: Request, db: Session = Depends(get_db)):
    """The editor asks whether it has been approved. 202 while waiting; the session once."""
    row = db.get(DeviceSignIn, _digest(body.device_code))
    if row is None or aware(row.expires_at) <= utcnow() or row.status == "used":
        raise HTTPException(status.HTTP_410_GONE, "This sign-in request expired. Start again.")
    if row.status == "pending":
        return JSONResponse({"status": "pending", "interval": INTERVAL}, status_code=status.HTTP_202_ACCEPTED)
    if row.status == "denied":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Signing in was refused in the browser.")
    user = db.get(User, row.user_id) if row.user_id else None
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account is disabled.")
    row.status = "used"
    access_token = sessions.start(db, user, request, "editor")
    audit.record(db, user, "auth.login", "user", user.id, method="editor", client=row.client, ip=client_ip(request))
    metrics.inc("vaultmind_sign_ins_total", method="editor", result="ok")
    db.commit()
    return TokenOut(access_token=access_token, user=UserOut.of(user))
