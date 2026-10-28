"""Sign in with Google or Microsoft (OpenID Connect, authorization code flow with PKCE).

1. /start sets a short-lived signed cookie holding a random state, nonce and PKCE verifier,
   then sends the browser to the provider.
2. /callback checks the state against the cookie, swaps the code for tokens (with the PKCE
   verifier and the client secret), and verifies the ID token: signature against the
   provider's published keys, issuer, audience, expiry and nonce.
3. The person is matched by their provider id, then by email; new people from an allowed
   Google domain or the Microsoft tenant get an account if auto-creation is on.

The VaultMind session token is handed to the page in the URL fragment, which browsers never
send to servers or put in Referer headers.
"""

import base64
import hashlib
import logging
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
import jwt
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import Position, Team, User, UserIdentity

log = logging.getLogger(__name__)
STATE_TTL = 600


class SSOError(Exception):
    """`code` is shown to the person as a friendly message on the sign-in page."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    issuer: str
    client_id: str
    client_secret: str


def providers() -> dict[str, Provider]:
    s = get_settings()
    out = {}
    if s.google_sso_configured:
        out["google"] = Provider("google", "Google", s.google_issuer.rstrip("/"), s.google_client_id, s.google_client_secret)
    if s.microsoft_sso_configured:
        issuer = f"{s.microsoft_login_base.rstrip('/')}/{s.microsoft_tenant_id}/v2.0"
        out["microsoft"] = Provider("microsoft", "Microsoft", issuer, s.microsoft_client_id, s.microsoft_client_secret)
    return out


_discovery: dict[str, tuple[float, dict]] = {}
_jwks: dict[str, jwt.PyJWKClient] = {}
_lock = threading.Lock()


def discovery(p: Provider) -> dict:
    with _lock:
        cached = _discovery.get(p.issuer)
        if cached and time.time() - cached[0] < 3600:
            return cached[1]
    try:
        r = httpx.get(f"{p.issuer}/.well-known/openid-configuration", timeout=10)
        r.raise_for_status()
        doc = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise SSOError("unavailable", f"discovery failed: {exc.__class__.__name__}") from exc
    with _lock:
        _discovery[p.issuer] = (time.time(), doc)
    return doc


def redirect_uri(p: Provider, base_url: str) -> str:
    base = get_settings().public_url.rstrip("/") or base_url.rstrip("/")
    return f"{base}/api/auth/sso/{p.id}/callback"


def safe_next(path: str | None) -> str:
    """Only same-site paths: '/library/3' yes; 'https://evil.example' or '//evil.example' no."""
    if not path or not path.startswith("/") or path.startswith("//") or "\\" in path or len(path) > 300:
        return "/"
    return path


def start(p: Provider, base_url: str, next_path: str | None) -> tuple[str, str]:
    """The provider's sign-in URL, and the signed state cookie to set."""
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    params = {
        "client_id": p.client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri(p, base_url),
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    domains = get_settings().google_allowed_domains
    if p.id == "google" and len(domains) == 1:
        params["hd"] = domains[0]  # Google shows only accounts from this domain
    url = f"{discovery(p)['authorization_endpoint']}?{urlencode(params)}"
    cookie = jwt.encode(
        {
            "state": state,
            "nonce": nonce,
            "verifier": verifier,
            "provider": p.id,
            "next": safe_next(next_path),
            "exp": int(time.time()) + STATE_TTL,
        },
        get_settings().jwt_secret,
        algorithm="HS256",
    )
    return url, cookie


def read_cookie(cookie: str | None, provider_id: str, state: str | None) -> dict:
    if not cookie or not state:
        raise SSOError("expired", "missing state")
    try:
        data = jwt.decode(cookie, get_settings().jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise SSOError("expired", "bad state cookie") from exc
    if data.get("provider") != provider_id or not secrets.compare_digest(str(data.get("state")), state):
        raise SSOError("expired", "state mismatch")
    return data


def exchange(p: Provider, code: str, verifier: str, base_url: str) -> dict:
    meta = discovery(p)
    try:
        r = httpx.post(
            meta["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri(p, base_url),
                "client_id": p.client_id,
                "client_secret": p.client_secret,
                "code_verifier": verifier,
            },
            headers={"Accept": "application/json"},
            timeout=15,
        )
    except httpx.HTTPError as exc:
        raise SSOError("unavailable", f"token request failed: {exc.__class__.__name__}") from exc
    if r.status_code >= 400:
        raise SSOError("failed", f"token endpoint said {r.status_code}")
    token = r.json().get("id_token")
    if not token:
        raise SSOError("failed", "no ID token")
    return verify_id_token(p, token, meta)


def verify_id_token(p: Provider, token: str, meta: dict) -> dict:
    with _lock:
        client = _jwks.get(meta["jwks_uri"])
        if client is None:
            client = _jwks[meta["jwks_uri"]] = jwt.PyJWKClient(meta["jwks_uri"], cache_keys=True)
    try:
        key = client.get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            key.key,
            algorithms=["RS256"],
            audience=p.client_id,
            # Google's tokens may say "accounts.google.com" without the scheme.
            issuer=[meta.get("issuer", p.issuer), "accounts.google.com"] if p.id == "google" else meta.get("issuer", p.issuer),
            leeway=60,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise SSOError("failed", f"ID token rejected: {exc.__class__.__name__}") from exc


@dataclass
class Identity:
    subject: str
    email: str
    name: str
    may_create: bool  # from an allowed domain / the tenant


def identity_from_claims(p: Provider, claims: dict, expected_nonce: str) -> Identity:
    if not secrets.compare_digest(str(claims.get("nonce", "")), expected_nonce):
        raise SSOError("expired", "nonce mismatch")
    s = get_settings()
    if p.id == "google":
        email = str(claims.get("email", "")).lower()
        if not email or claims.get("email_verified") is not True:
            raise SSOError("unverified", "email not verified")
        domain = email.rsplit("@", 1)[-1]
        allowed = {d.lower() for d in s.google_allowed_domains}
        hd = str(claims.get("hd", "")).lower()
        may_create = bool(allowed) and hd in allowed and domain in allowed
        return Identity(str(claims["sub"]), email, str(claims.get("name") or email), may_create)
    # Microsoft: only this tenant; email may only be in preferred_username.
    if str(claims.get("tid", "")) != s.microsoft_tenant_id:
        raise SSOError("domain", "another tenant")
    email = str(claims.get("email") or claims.get("preferred_username") or "").lower()
    if "@" not in email:
        raise SSOError("failed", "no email address on the account")
    subject = str(claims.get("oid") or claims["sub"])
    return Identity(subject, email, str(claims.get("name") or email), True)


def sign_in(db: Session, provider_id: str, ident: Identity) -> User:
    """Find (or create) the person and remember their provider id."""
    s = get_settings()
    user = db.scalar(
        select(User)
        .join(UserIdentity, UserIdentity.user_id == User.id)
        .where(UserIdentity.provider == provider_id, UserIdentity.subject == ident.subject)
    )
    if user is None:
        user = db.scalar(select(User).where(func.lower(User.email) == ident.email))
    if user is None:
        if not (s.sso_auto_create and ident.may_create):
            raise SSOError("domain" if not ident.may_create else "no_account", ident.email)
        team = None
        if s.sso_default_team:
            team = db.scalar(select(Team).where(func.lower(Team.name) == s.sso_default_team.lower()))
        team = team or db.scalar(select(Team).order_by(Team.name).limit(1))
        if team is None:
            raise SSOError("no_account", "no teams exist yet")
        user = User(
            email=ident.email,
            full_name=ident.name[:120],
            title="",
            team_id=team.id,
            position=int(Position.ASSOCIATE),
            password_hash="!",  # no password; they sign in with their company account
            auth_provider=provider_id,
            onboarding_until=datetime.now(UTC) + timedelta(days=30),
        )
        db.add(user)
        db.flush()
    if not user.is_active:
        raise SSOError("disabled", ident.email)
    linked = db.scalar(select(UserIdentity).where(UserIdentity.provider == provider_id, UserIdentity.subject == ident.subject))
    if linked is None:
        db.add(UserIdentity(user_id=user.id, provider=provider_id, subject=ident.subject))
    elif linked.user_id != user.id:
        raise SSOError("failed", "provider id linked to another account")
    return user
