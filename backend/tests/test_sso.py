"""Sign in with Google / Microsoft, against a fake OpenID Connect provider."""

import base64
import hashlib
import json
import secrets
import time
from urllib.parse import parse_qs, unquote, urlparse

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fakeserver import FakeServer, Req, Resp
from sqlalchemy import select

from app import sso
from app.config import get_settings
from app.database import SessionLocal
from app.models import User, UserIdentity

TENANT = "11111111-2222-3333-4444-555555555555"


class FakeOIDC:
    def __init__(self, issuer_path: str = "") -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.codes: dict[str, dict] = {}
        self.client_id, self.secret = "client-abc", "client-secret"
        self.server = FakeServer(self.handle)
        self.url = self.server.url
        self.issuer = self.url + issuer_path
        self.sign_with = self.key

    def handle(self, req: Req) -> Resp:
        if req.path.endswith("/.well-known/openid-configuration"):
            return Resp(
                200,
                {
                    "issuer": self.issuer,
                    "authorization_endpoint": self.url + "/authorize",
                    "token_endpoint": self.url + "/token",
                    "jwks_uri": self.url + "/jwks",
                },
            )
        if req.path == "/jwks":
            jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
            return Resp(200, {"keys": [jwk | {"kid": "k1", "use": "sig", "alg": "RS256"}]})
        if req.path == "/token":
            f = req.form
            pending = self.codes.pop(f["code"], None)
            if pending is None or f["client_id"] != self.client_id or f["client_secret"] != self.secret:
                return Resp(400, {"error": "invalid_grant"})
            challenge = base64.urlsafe_b64encode(hashlib.sha256(f["code_verifier"].encode()).digest()).rstrip(b"=").decode()
            if challenge != pending["challenge"] or f["redirect_uri"] != pending["redirect_uri"]:
                return Resp(400, {"error": "invalid_grant", "error_description": "PKCE or redirect mismatch"})
            now = int(time.time())
            claims = {"iss": self.issuer, "aud": self.client_id, "iat": now, "exp": now + 300, "nonce": pending["nonce"]}
            claims |= pending["claims"]
            token = jwt.encode(claims, self.sign_with, algorithm="RS256", headers={"kid": "k1"})
            return Resp(200, {"id_token": token, "access_token": "at", "token_type": "Bearer"})
        return Resp(404, {})

    def authorize(self, params: dict, claims: dict) -> str:
        """What the provider does when the person signs in: remember the request, issue a code."""
        code = secrets.token_urlsafe(8)
        self.codes[code] = {
            "nonce": params["nonce"][0],
            "challenge": params["code_challenge"][0],
            "redirect_uri": params["redirect_uri"][0],
            "claims": claims,
        }
        return code


@pytest.fixture
def google_idp(monkeypatch):
    fake = FakeOIDC()
    s = get_settings()
    monkeypatch.setattr(s, "google_client_id", fake.client_id)
    monkeypatch.setattr(s, "google_client_secret", fake.secret)
    monkeypatch.setattr(s, "google_issuer", fake.url)
    monkeypatch.setattr(s, "google_allowed_domains", ["northwind.example"])
    sso._discovery.clear()
    sso._jwks.clear()
    yield fake
    fake.server.close()


@pytest.fixture
def microsoft_idp(monkeypatch):
    fake = FakeOIDC(issuer_path=f"/{TENANT}/v2.0")
    s = get_settings()
    monkeypatch.setattr(s, "microsoft_sso", True)
    monkeypatch.setattr(s, "microsoft_tenant_id", TENANT)
    monkeypatch.setattr(s, "microsoft_client_id", fake.client_id)
    monkeypatch.setattr(s, "microsoft_client_secret", fake.secret)
    monkeypatch.setattr(s, "microsoft_login_base", fake.url)
    sso._discovery.clear()
    sso._jwks.clear()
    yield fake
    fake.server.close()


def _sign_in(client, fake: FakeOIDC, provider: str, claims: dict, next_path: str | None = None, state=None):
    start = client.get(
        f"/api/auth/sso/{provider}/start", params={"next": next_path} if next_path else None, follow_redirects=False
    )
    assert start.status_code == 303, start.text
    params = parse_qs(urlparse(start.headers["location"]).query)
    assert params["code_challenge_method"] == ["S256"] and params["scope"] == ["openid email profile"]
    code = fake.authorize(params, claims)
    return client.get(
        f"/api/auth/sso/{provider}/callback",
        params={"code": code, "state": state or params["state"][0]},
        follow_redirects=False,
    )


def _fragment(resp) -> dict:
    loc = resp.headers["location"]
    assert loc.startswith("/signin#"), loc
    return {k: unquote(v) for k, v in (p.split("=", 1) for p in loc.split("#", 1)[1].split("&"))}


def _error(resp) -> str:
    loc = resp.headers["location"]
    assert "sso_error=" in loc, loc
    return loc.split("sso_error=", 1)[1]


GOOGLE_ZOYA = {
    "sub": "g-zoya",
    "email": "zoya@northwind.example",
    "email_verified": True,
    "hd": "northwind.example",
    "name": "Zoya Khan",
}


def test_google_sign_in_creates_an_account_for_the_company_domain(client, google_idp):
    r = _sign_in(client, google_idp, "google", GOOGLE_ZOYA, next_path="/library?q=leave&team=2")
    frag = _fragment(r)
    assert frag["next"] == "/library?q=leave&team=2"
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {frag['token']}"}).json()
    assert me["email"] == "zoya@northwind.example" and me["position_label"] == "Associate"
    with SessionLocal() as db:
        zoya = db.scalar(select(User).where(User.email == "zoya@northwind.example"))
        assert zoya.auth_provider == "google" and zoya.onboarding_until is not None  # new joiner checklist
    # She has no password, so password sign-in can't be used for her account.
    assert client.post("/api/auth/login", json={"email": "zoya@northwind.example", "password": "!"}).status_code == 401


def test_existing_people_are_matched_by_email_then_by_their_google_id(client, google_idp):
    claims = {"sub": "g-rohan", "email": "Rohan@northwind.example", "email_verified": True, "hd": "northwind.example"}
    me = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {_fragment(_sign_in(client, google_idp, 'google', claims))['token']}"}
    )
    assert me.json()["full_name"] == "Rohan Iyer"
    # Later the email changes at Google; the id still finds him.
    renamed = claims | {"email": "rohan.iyer@northwind.example"}
    me = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {_fragment(_sign_in(client, google_idp, 'google', renamed))['token']}"}
    )
    assert me.json()["full_name"] == "Rohan Iyer"
    with SessionLocal() as db:
        assert db.scalar(select(UserIdentity.subject).where(UserIdentity.provider == "google")) == "g-rohan"


def test_google_accounts_from_other_domains_are_refused(client, google_idp):
    outsider = {"sub": "g-x", "email": "someone@gmail.com", "email_verified": True, "name": "X"}
    assert _error(_sign_in(client, google_idp, "google", outsider)) == "domain"
    unverified = GOOGLE_ZOYA | {"email_verified": False}
    assert _error(_sign_in(client, google_idp, "google", unverified)) == "unverified"


def test_tampered_or_replayed_sign_ins_are_refused(client, google_idp):
    assert _error(_sign_in(client, google_idp, "google", GOOGLE_ZOYA, state="wrong-state")) == "expired"
    # A token signed by someone else's key.
    google_idp.sign_with = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert _error(_sign_in(client, google_idp, "google", GOOGLE_ZOYA)) == "failed"
    google_idp.sign_with = google_idp.key
    # The ID token's nonce must match this browser's sign-in.
    assert _error(_sign_in(client, google_idp, "google", GOOGLE_ZOYA | {"nonce": "someone-elses"})) == "expired"
    # No state cookie at all (a forged callback link).
    client.cookies.clear()
    r = client.get("/api/auth/sso/google/callback", params={"code": "x", "state": "y"}, follow_redirects=False)
    assert _error(r) == "expired"
    # The person pressed Cancel at Google.
    r = client.get("/api/auth/sso/google/callback", params={"error": "access_denied"}, follow_redirects=False)
    assert _error(r) == "denied"


def test_deactivated_people_cannot_sign_in_with_google(client, google_idp):
    with SessionLocal() as db:
        db.query(User).filter(User.email == "neha@northwind.example").update({"is_active": False})
        db.commit()
    claims = {"sub": "g-neha", "email": "neha@northwind.example", "email_verified": True, "hd": "northwind.example"}
    assert _error(_sign_in(client, google_idp, "google", claims)) == "disabled"


def test_the_return_path_cannot_leave_the_site(client, google_idp):
    for evil in ["https://evil.example/", "//evil.example", "/\\evil.example"]:
        assert _fragment(_sign_in(client, google_idp, "google", GOOGLE_ZOYA, next_path=evil))["next"] == "/"


def test_microsoft_sign_in_for_the_tenant(client, microsoft_idp):
    claims = {
        "sub": "pairwise-1",
        "oid": "aad-ravi",
        "tid": TENANT,
        "preferred_username": "Ravi@northwind.example",
        "name": "Ravi Kumar",
    }
    frag = _fragment(_sign_in(client, microsoft_idp, "microsoft", claims))
    assert (
        client.get("/api/auth/me", headers={"Authorization": f"Bearer {frag['token']}"}).json()["email"]
        == "ravi@northwind.example"
    )
    other_tenant = claims | {"tid": "99999999-0000-0000-0000-000000000000"}
    assert _error(_sign_in(client, microsoft_idp, "microsoft", other_tenant)) == "domain"


def test_sign_in_options_are_listed_only_when_set_up(client, google_idp):
    meta = client.get("/api/meta").json()
    assert meta["sso_providers"] == [{"id": "google", "label": "Google"}] and meta["password_login"] is True
    assert client.get("/api/auth/sso/microsoft/start", follow_redirects=False).status_code == 404


def test_password_sign_in_can_be_turned_off_except_for_admins(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "password_login", False)
    r = client.post("/api/auth/login", json={"email": "rohan@northwind.example", "password": "demo1234"})
    assert r.status_code == 403 and "company account" in r.json()["detail"]
    assert client.post("/api/auth/login", json={"email": "dev@northwind.example", "password": "demo1234"}).status_code == 200
    assert client.get("/api/meta").json()["password_login"] is False
