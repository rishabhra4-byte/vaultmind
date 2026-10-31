import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit, metrics, sessions, sso
from ..config import get_settings
from ..database import get_db
from ..deps import current_auth, current_user, login_limiter_account, login_limiter_ip
from ..models import User
from ..schemas import LoginIn, TokenOut, UserOut
from ..security import DUMMY_HASH, verify_password
from ..sessions import Auth

router = APIRouter(prefix="/api/auth", tags=["auth"])
log = logging.getLogger(__name__)
SSO_COOKIE = "vaultmind_sso"


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    ip = request.client.host if request.client else "unknown"
    login_limiter_ip.ensure_allowed(ip)
    login_limiter_account.ensure_allowed(email)
    user = db.scalar(select(User).where(func.lower(User.email) == email))
    # Always run bcrypt, so the response time doesn't reveal whether the account exists.
    password_ok = verify_password(body.password, user.password_hash if user else DUMMY_HASH)
    if user is None or not password_ok:
        login_limiter_ip.record(ip)
        login_limiter_account.record(email)
        metrics.inc("vaultmind_sign_ins_total", method="password", result="failed")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account is disabled")
    if not get_settings().password_login and not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Sign in with your company account instead")
    token = sessions.start(db, user, request, "password")
    audit.record(db, user, "auth.login", "user", user.id, method="password", ip=sessions.client_ip(request))
    db.commit()
    return TokenOut(access_token=token, user=UserOut.of(user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(auth: Auth = Depends(current_auth), db: Session = Depends(get_db)):
    """End this session on the server, so the token stops working even if it was copied."""
    sessions.end(db, auth.session, "signed_out")
    audit.record(db, auth.user, "auth.logout", "user", auth.user.id)
    db.commit()


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return UserOut.of(user)


def _provider(provider: str) -> sso.Provider:
    p = sso.providers().get(provider)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That sign-in option isn't set up")
    return p


@router.get("/sso/{provider}/start", include_in_schema=False)
def sso_start(provider: str, request: Request, next: str | None = None):
    p = _provider(provider)
    try:
        url, cookie = sso.start(p, str(request.base_url), next)
    except sso.SSOError as exc:
        log.warning("Sign-in with %s couldn't start: %s", provider, exc)
        return RedirectResponse(f"/signin?sso_error={exc.code}", status_code=303)
    resp = RedirectResponse(url, status_code=303)
    resp.set_cookie(
        SSO_COOKIE,
        cookie,
        max_age=sso.STATE_TTL,
        httponly=True,
        samesite="lax",  # sent on the provider's redirect back, never on cross-site posts
        secure=get_settings().public_url.startswith("https://"),
        path="/api/auth/sso",
    )
    return resp


@router.get("/sso/{provider}/callback", include_in_schema=False)
def sso_callback(
    provider: str,
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    p = _provider(provider)
    try:
        if error:
            raise sso.SSOError("denied", error)
        saved = sso.read_cookie(request.cookies.get(SSO_COOKIE), p.id, state)
        if not code:
            raise sso.SSOError("failed", "no code")
        claims = sso.exchange(p, code, saved["verifier"], str(request.base_url))
        ident = sso.identity_from_claims(p, claims, saved["nonce"])
        user = sso.sign_in(db, p.id, ident)
    except sso.SSOError as exc:
        db.rollback()
        metrics.inc("vaultmind_sign_ins_total", method=p.id, result="failed")
        log.info("Sign-in with %s refused: %s", provider, exc)
        resp = RedirectResponse(f"/signin?sso_error={exc.code}", status_code=303)
        resp.delete_cookie(SSO_COOKIE, path="/api/auth/sso")
        return resp
    token = sessions.start(db, user, request, p.id)
    audit.record(db, user, "auth.login", "user", user.id, method=p.id, ip=sessions.client_ip(request))
    db.commit()
    # In the fragment: never sent to a server, never in logs or Referer headers.
    resp = RedirectResponse(f"/signin#token={token}&next={quote(saved['next'], safe='')}", status_code=303)
    resp.delete_cookie(SSO_COOKIE, path="/api/auth/sso")
    return resp
