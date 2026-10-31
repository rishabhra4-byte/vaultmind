"""Sessions that can be ended: sign-out, password changes, deactivation, administrators, idle
timeouts, and tokens from before sessions existed."""

from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import AuditEvent, User, UserSession
from app.seed import DEMO_PASSWORD


def _uid(key: str) -> int:
    with SessionLocal() as db:
        return db.scalar(select(User.id).where(User.email == f"{key}@northwind.example"))


def _ok(client, headers) -> bool:
    return client.get("/api/auth/me", headers=headers).status_code == 200


def test_sign_out_ends_the_session_on_the_server(client, login):
    headers = login("priya")
    assert client.post("/api/auth/logout", headers=headers).status_code == 204
    r = client.get("/api/auth/me", headers=headers)
    assert r.status_code == 401
    assert r.json()["detail"] == "You signed out. Sign in again to continue."


def test_only_a_hash_of_the_session_id_is_stored(client, login):
    token = login("priya")["Authorization"].removeprefix("Bearer ")
    sid = jwt.decode(token, options={"verify_signature": False})["sid"]
    with SessionLocal() as db:
        stored = db.scalars(select(UserSession.id).where(UserSession.user_id == _uid("priya"))).all()
    assert stored and sid not in stored and all(len(s) == 64 for s in stored)


def test_tokens_without_a_session_are_refused(client):
    s = get_settings()
    old = jwt.encode(
        {"sub": str(_uid("priya")), "exp": datetime.now(UTC) + timedelta(hours=1)}, s.jwt_secret, algorithm=s.jwt_algorithm
    )
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {old}"}).status_code == 401


def test_list_and_end_your_own_sessions(client, login):
    laptop, phone = login("priya"), login("priya")
    mine = client.get("/api/me/sessions", headers=laptop).json()
    assert len(mine) == 2 and sum(s["current"] for s in mine) == 1
    other = next(s for s in mine if not s["current"])
    assert client.delete(f"/api/me/sessions/{other['id']}", headers=laptop).status_code == 204
    assert not _ok(client, phone) and _ok(client, laptop)
    assert client.get("/api/auth/me", headers=phone).json()["detail"] == "You were signed out from another browser."

    # Someone else's session can't be ended (or even found).
    rohan = login("rohan")
    mine = client.get("/api/me/sessions", headers=laptop).json()
    assert client.delete(f"/api/me/sessions/{mine[0]['id']}", headers=rohan).status_code == 404
    assert _ok(client, laptop)


def test_sign_out_of_other_sessions(client, login):
    here, there, elsewhere = login("neha"), login("neha"), login("neha")
    assert client.post("/api/me/sessions/end-others", headers=here).json() == {"ended": 2}
    assert _ok(client, here) and not _ok(client, there) and not _ok(client, elsewhere)


def test_changing_your_password_ends_your_other_sessions(client, login):
    here, there = login("neha"), login("neha")
    body = {"current_password": "wrong", "new_password": "a much longer passphrase"}
    assert client.post("/api/me/password", headers=here, json=body).status_code == 403
    body["current_password"] = DEMO_PASSWORD
    assert client.post("/api/me/password", headers=here, json={**body, "new_password": "short"}).status_code == 422
    assert client.post("/api/me/password", headers=here, json=body).json() == {"ended": 1}
    assert _ok(client, here) and not _ok(client, there)
    r = client.post("/api/auth/login", json={"email": "neha@northwind.example", "password": "a much longer passphrase"})
    assert r.status_code == 200
    r = client.post("/api/auth/login", json={"email": "neha@northwind.example", "password": DEMO_PASSWORD})
    assert r.status_code == 401
    with SessionLocal() as db:
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "auth.password_change")) is not None


def test_deactivating_someone_signs_them_out_at_once(client, login):
    neha, admin = login("neha"), login("dev")
    assert client.patch(f"/api/admin/users/{_uid('neha')}", headers=admin, json={"is_active": False}).status_code == 200
    r = client.get("/api/documents", headers=neha)
    assert r.status_code == 401 and "deactivated" in r.json()["detail"]


def test_a_password_reset_by_an_administrator_ends_every_session(client, login):
    neha, admin = login("neha"), login("dev")
    r = client.patch(f"/api/admin/users/{_uid('neha')}", headers=admin, json={"password": "reset-by-it-desk-2026"})
    assert r.status_code == 200 and r.json()["open_sessions"] == 0
    assert not _ok(client, neha)


def test_removing_admin_rights_ends_their_sessions(client, login):
    admin = login("dev")
    assert client.patch(f"/api/admin/users/{_uid('rohan')}", headers=admin, json={"is_admin": True}).status_code == 200
    rohan = login("rohan")
    assert client.get("/api/admin/users", headers=rohan).status_code == 200
    assert client.patch(f"/api/admin/users/{_uid('rohan')}", headers=admin, json={"is_admin": False}).status_code == 200
    assert not _ok(client, rohan)


def test_administrators_see_and_end_someones_sessions(client, login):
    priya, admin = login("priya"), login("dev")
    people = {u["email"]: u for u in client.get("/api/admin/users", headers=admin).json()}
    assert people["priya@northwind.example"]["open_sessions"] == 1
    listed = client.get(f"/api/admin/users/{_uid('priya')}/sessions", headers=admin).json()
    assert len(listed) == 1 and listed[0]["device"]
    assert client.post(f"/api/admin/users/{_uid('priya')}/sessions/end", headers=admin).json() == {"ended": 1}
    assert not _ok(client, priya)
    r = client.get("/api/auth/me", headers=priya)
    assert r.json()["detail"] == "An administrator signed you out."

    # Not for non-admins, and an admin uses their own account page for their own sessions.
    assert client.post(f"/api/admin/users/{_uid('neha')}/sessions/end", headers=login("rohan")).status_code == 403
    assert client.post(f"/api/admin/users/{_uid('dev')}/sessions/end", headers=admin).status_code == 409


def test_sessions_end_after_the_idle_timeout(client, login, monkeypatch):
    headers = login("priya")
    with SessionLocal() as db:
        row = db.scalar(select(UserSession).where(UserSession.user_id == _uid("priya")))
        row.last_seen_at = datetime.now(UTC) - timedelta(minutes=get_settings().session_idle_minutes + 1)
        db.commit()
    r = client.get("/api/auth/me", headers=headers)
    assert r.status_code == 401 and "without activity" in r.json()["detail"]

    monkeypatch.setattr(get_settings(), "session_idle_minutes", 0)  # 0 turns the timeout off
    headers = login("priya")
    with SessionLocal() as db:
        for row in db.scalars(select(UserSession).where(UserSession.ended_at.is_(None))):
            row.last_seen_at = datetime.now(UTC) - timedelta(days=2)
        db.commit()
    assert _ok(client, headers)


def test_describe_agent():
    from app.sessions import describe_agent

    chrome_windows = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
    assert describe_agent(chrome_windows) == "Chrome on Windows"
    assert describe_agent("") == "Unknown browser"
    assert describe_agent("python-httpx/0.27") == "python-httpx"
