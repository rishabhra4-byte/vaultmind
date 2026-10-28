"""Reminders and copies of notifications by email and Slack, as run by the background scheduler."""

from datetime import UTC, datetime, timedelta

import pytest
from fakeserver import FakeServer, Req, Resp
from sqlalchemy import select

from app import delivery
from app.config import get_settings
from app.database import SessionLocal
from app.models import AccessGrant, Notification, User, UserIdentity
from app.notify import notify
from app.scheduler import Scheduler, remind_expiring_grants


def _uid(key: str) -> int:
    with SessionLocal() as db:
        return db.scalar(select(User.id).where(User.email == f"{key}@northwind.example"))


def test_people_are_reminded_before_temporary_access_ends(client, login, doc_id):
    neha = _uid("neha")  # the seed gives Neha 5 days on the performance review guidelines
    assert remind_expiring_grants() == 0
    with SessionLocal() as db:
        grant = db.scalar(select(AccessGrant).where(AccessGrant.user_id == neha))
        grant.expires_at = datetime.now(UTC) + timedelta(hours=20)
        db.commit()
    assert remind_expiring_grants() == 1
    assert remind_expiring_grants() == 0  # once per grant
    notes = client.get("/api/notifications", headers=login("neha")).json()["items"]
    assert notes[0]["kind"] == "access.expiring" and "ends in 20 hours" in notes[0]["title"]


class FakeSMTP:
    sent: list = []

    def __init__(self, host, port, timeout=None):
        self.host = host

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        pass

    def login(self, user, password):
        pass

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


@pytest.fixture
def slack_api(monkeypatch):
    posted: list[dict] = []

    def handle(req: Req) -> Resp:
        assert req.bearer == "xoxb-test"
        if req.path == "/users.lookupByEmail":
            return Resp(
                200,
                {"ok": True, "user": {"id": "U-KARAN"}}
                if req.query["email"].startswith("karan")
                else {"ok": False, "error": "users_not_found"},
            )
        if req.path == "/chat.postMessage":
            posted.append(req.json)
            return Resp(200, {"ok": True})
        return Resp(404, {"ok": False})

    server = FakeServer(handle)
    s = get_settings()
    monkeypatch.setattr(s, "slack_bot_token", "xoxb-test")
    monkeypatch.setattr(s, "slack_signing_secret", "secret")
    monkeypatch.setattr(s, "slack_api_base", server.url)
    yield posted
    server.close()


def test_notifications_are_copied_by_email_and_slack(monkeypatch, slack_api):
    s = get_settings()
    monkeypatch.setattr(s, "smtp_host", "smtp.example")
    monkeypatch.setattr(s, "smtp_from", "vaultmind@northwind.example")
    monkeypatch.setattr(s, "public_url", "https://vaultmind.northwind.example")
    monkeypatch.setattr(delivery.smtplib, "SMTP", FakeSMTP)
    FakeSMTP.sent = []
    with SessionLocal() as db:
        db.query(Notification).update({"delivered_at": datetime.now(UTC)})  # only the new ones below
        notify(
            db, [_uid("karan"), _uid("neha")], kind="test", title="Your request was approved", body="7 days.", link="/library/3"
        )
        db.query(User).filter(User.email == "neha@northwind.example").update({"notify_email": False})
        db.commit()

    assert delivery.deliver_pending() == 2
    assert [m["To"] for m in FakeSMTP.sent] == ["karan@northwind.example"]  # Neha turned email off
    assert "https://vaultmind.northwind.example/library/3" in FakeSMTP.sent[0].get_content()
    assert [p["channel"] for p in slack_api] == ["U-KARAN"]  # Neha has no Slack account
    assert "<https://vaultmind.northwind.example/library/3|Open in VaultMind>" in slack_api[0]["text"]
    with SessionLocal() as db:
        assert db.scalar(select(UserIdentity.subject).where(UserIdentity.provider == "slack")) == "U-KARAN"
        rows = {n.user_id: n.delivery for n in db.scalars(select(Notification).where(Notification.kind == "test"))}
        assert rows[_uid("karan")] == {"email": "sent", "slack": "sent"}
        assert rows[_uid("neha")] == {"slack": "no Slack account"}
    assert delivery.deliver_pending() == 0  # each notification is delivered once


def test_nothing_is_sent_when_no_channel_is_set_up():
    with SessionLocal() as db:
        notify(db, [_uid("karan")], kind="test", title="Hi")
        db.commit()
    assert delivery.deliver_pending() == 0


def test_a_failing_mail_server_doesnt_stop_slack(monkeypatch, slack_api):
    class Broken(FakeSMTP):
        def send_message(self, msg):
            raise OSError("connection refused")

    s = get_settings()
    monkeypatch.setattr(s, "smtp_host", "smtp.example")
    monkeypatch.setattr(s, "smtp_from", "vaultmind@northwind.example")
    monkeypatch.setattr(delivery.smtplib, "SMTP", Broken)
    with SessionLocal() as db:
        db.query(Notification).update({"delivered_at": datetime.now(UTC)})
        notify(db, [_uid("karan")], kind="test", title="Hello")
        db.commit()
    delivery.deliver_pending()
    with SessionLocal() as db:
        n = db.scalar(select(Notification).where(Notification.kind == "test"))
        assert n.delivery == {"email": "failed: OSError", "slack": "sent"}


def test_the_scheduler_runs_every_job_even_when_one_fails(monkeypatch):
    calls = []
    monkeypatch.setattr("app.sync.due_sources", lambda db: [1, 2])
    monkeypatch.setattr(
        "app.sync.run_sync", lambda sid: calls.append(("sync", sid)) or (_ for _ in ()).throw(RuntimeError("boom"))
    )
    monkeypatch.setattr("app.scheduler.remind_expiring_grants", lambda: calls.append("remind"))
    monkeypatch.setattr("app.delivery.deliver_pending", lambda: calls.append("deliver"))
    Scheduler().tick()
    assert calls == [("sync", 1), ("sync", 2), "remind", "deliver"]
