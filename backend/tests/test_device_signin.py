"""Signing in the VS Code extension: it shows a code, the person approves it in the browser."""

from datetime import timedelta

from sqlalchemy import select

from app.database import SessionLocal
from app.models import AuditEvent, DeviceSignIn, utcnow
from app.routers.device import ALPHABET, normal_code

EDITOR = {"User-Agent": "VaultMind-VSCode/0.1.0 (Windows)"}


def _start(client, **body):
    r = client.post("/api/auth/device", json=body or {"client": "VS Code"}, headers=EDITOR)
    assert r.status_code == 200, r.text
    return r.json()


def _poll(client, device_code):
    return client.post("/api/auth/device/token", json={"device_code": device_code}, headers=EDITOR)


def test_approving_the_code_in_the_browser_signs_the_editor_in(client, login):
    s = _start(client)
    code = s["user_code"]
    assert len(code) == 9 and code[4] == "-" and all(c in ALPHABET for c in code.replace("-", ""))
    assert s["verification_uri_complete"].endswith(f"/connect?code={code}") and s["interval"] >= 1

    # Waiting: nothing yet.
    r = _poll(client, s["device_code"])
    assert r.status_code == 202 and r.json()["status"] == "pending"

    priya = login("priya")
    info = client.get(f"/api/auth/device/{code.lower().replace('-', ' ')}", headers=priya)
    assert info.status_code == 200 and info.json()["client"] == "VS Code" and info.json()["ip"]
    assert client.post(f"/api/auth/device/{code}/decide", json={"allow": True}, headers=priya).json() == {"status": "approved"}

    r = _poll(client, s["device_code"])
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    assert r.json()["user"]["email"] == "priya@northwind.example"
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200 and me.json()["email"] == "priya@northwind.example"

    # It's an ordinary session: listed as the extension, and it can be ended.
    listed = client.get("/api/me/sessions", headers=priya).json()
    assert any("VS Code extension" in str(x) for x in listed), listed

    # Used once: asking again fails, and the code can't be approved again.
    assert _poll(client, s["device_code"]).status_code == 410
    assert client.post(f"/api/auth/device/{code}/decide", json={"allow": True}, headers=priya).status_code == 409
    with SessionLocal() as db:
        actions = [e.action for e in db.scalars(select(AuditEvent).where(AuditEvent.actor_id == me.json()["id"]))]
    assert "auth.device_approved" in actions and "auth.login" in actions


def test_denying_or_letting_it_expire_never_signs_in(client, login):
    s = _start(client)
    client.post(f"/api/auth/device/{s['user_code']}/decide", json={"allow": False}, headers=login("rohan"))
    assert _poll(client, s["device_code"]).status_code == 403

    s = _start(client)
    with SessionLocal() as db:
        row = db.scalar(select(DeviceSignIn).where(DeviceSignIn.user_code == s["user_code"]))
        row.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert _poll(client, s["device_code"]).status_code == 410
    assert client.get(f"/api/auth/device/{s['user_code']}", headers=login("rohan")).status_code == 404


def test_only_someone_signed_in_can_see_or_approve_a_code(client):
    s = _start(client)
    assert client.get(f"/api/auth/device/{s['user_code']}").status_code == 401
    assert client.post(f"/api/auth/device/{s['user_code']}/decide", json={"allow": True}).status_code == 401
    assert _poll(client, "x" * 43).status_code == 410  # made-up device codes get nothing


def test_the_secret_is_stored_only_as_a_hash(client):
    s = _start(client)
    with SessionLocal() as db:
        ids = list(db.scalars(select(DeviceSignIn.id)))
    assert s["device_code"] not in ids and all(len(i) == 64 for i in ids)


def test_starting_is_rate_limited(client):
    for _ in range(20):
        _start(client)
    assert client.post("/api/auth/device", json={}, headers=EDITOR).status_code == 429


def test_codes_are_read_forgivingly():
    assert normal_code("kqxw mrtd") == normal_code("KQXWMRTD") == normal_code("KQXW-MRTD") == "KQXW-MRTD"
    assert normal_code("abc") == ""
