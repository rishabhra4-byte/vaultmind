"""Slack bot (slash command, mentions, direct messages), the first-week checklist, and
notification preferences."""

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from fakeserver import FakeServer, Req, Resp
from sqlalchemy import select

from app import slack_bot
from app.config import get_settings
from app.database import SessionLocal
from app.models import AuditEvent, Conversation, User, UserIdentity

SECRET = "slack-signing-secret"
SLACK_PEOPLE = {"U-KARAN": "karan@northwind.example", "U-ROHAN": "rohan@northwind.example"}


class FakeSlack:
    def __init__(self) -> None:
        self.posted: list[tuple[str, dict]] = []
        self.responses: list[dict] = []
        self.lookups = 0
        self.server = FakeServer(self.handle)

    def handle(self, req: Req) -> Resp:
        if req.path == "/respond":
            self.responses.append(req.json)
            return Resp(200, {"ok": True})
        assert req.bearer == "xoxb-test"
        if req.path == "/users.info":
            self.lookups += 1
            email = SLACK_PEOPLE.get(req.query["user"], "stranger@elsewhere.example")
            return Resp(200, {"ok": True, "user": {"id": req.query["user"], "profile": {"email": email}}})
        if req.path in ("/chat.postEphemeral", "/chat.postMessage"):
            self.posted.append((req.path.strip("/"), req.json))
            return Resp(200, {"ok": True})
        return Resp(404, {"ok": False, "error": "unknown_method"})


@pytest.fixture
def slack_fake(monkeypatch):
    fake = FakeSlack()
    s = get_settings()
    monkeypatch.setattr(s, "slack_bot_token", "xoxb-test")
    monkeypatch.setattr(s, "slack_signing_secret", SECRET)
    monkeypatch.setattr(s, "slack_api_base", fake.server.url)
    monkeypatch.setattr(s, "public_url", "https://vaultmind.example")
    monkeypatch.setattr(slack_bot, "run_in_background", lambda fn: fn())  # answer before the request returns
    slack_bot._seen.clear()
    yield fake
    fake.server.close()


def _signed(body: bytes, secret: str = SECRET, at: float | None = None) -> dict:
    ts = str(int(at or time.time()))
    sig = "v0=" + hmac.new(secret.encode(), f"v0:{ts}:".encode() + body, hashlib.sha256).hexdigest()
    return {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig}


def _command(client, fake, user: str, text: str, response_url: str | None = None):
    body = urlencode(
        {"command": "/vaultmind", "user_id": user, "text": text, "response_url": response_url or f"{fake.server.url}/respond"}
    ).encode()
    headers = {"Content-Type": "application/x-www-form-urlencoded", **_signed(body)}
    return client.post("/api/slack/commands", content=body, headers=headers)


def _event(client, payload: dict, extra_headers: dict | None = None):
    body = json.dumps(payload).encode()
    return client.post(
        "/api/slack/events", content=body, headers={"Content-Type": "application/json", **_signed(body), **(extra_headers or {})}
    )


def test_slack_endpoints_need_slack_set_up_and_a_valid_signature(client, monkeypatch):
    body = b"text=hi&user_id=U-KARAN"
    assert client.post("/api/slack/commands", content=body).status_code == 404  # not set up
    s = get_settings()
    monkeypatch.setattr(s, "slack_bot_token", "xoxb-test")
    monkeypatch.setattr(s, "slack_signing_secret", SECRET)
    assert client.post("/api/slack/commands", content=body).status_code == 401
    assert client.post("/api/slack/commands", content=body, headers=_signed(body, secret="wrong")).status_code == 401
    assert client.post("/api/slack/commands", content=body, headers=_signed(body, at=time.time() - 600)).status_code == 401
    assert client.post("/api/slack/commands", content=body + b"x", headers=_signed(body)).status_code == 401


def test_slack_url_verification(client, slack_fake):
    assert _event(client, {"type": "url_verification", "challenge": "abc123"}).json() == {"challenge": "abc123"}


def test_slash_command_answers_privately_with_the_askers_permissions(client, slack_fake):
    r = _command(client, slack_fake, "U-KARAN", "What are the salary bands for SDE-2?")
    assert r.status_code == 200 and r.json()["response_type"] == "ephemeral"
    answer = slack_fake.responses[-1]
    assert answer["response_type"] == "ephemeral"
    assert "22 to 34" not in answer["text"] and "Salary Bands 2026" in answer["text"]  # a locked hint, nothing more
    assert "<https://vaultmind.example/?c=" in answer["text"]

    _command(client, slack_fake, "U-ROHAN", "What is the SDE-2 salary band?")
    answer = slack_fake.responses[-1]["text"]
    assert "22 to 34" in answer
    assert "<https://vaultmind.example/library/" in answer and "Offline answer" in answer

    lookups = slack_fake.lookups
    _command(client, slack_fake, "U-KARAN", "How many days of leave do I get?")
    assert slack_fake.lookups == lookups  # the Slack account is remembered
    with SessionLocal() as db:
        karan = db.scalar(select(User).where(User.email == "karan@northwind.example"))
        assert db.scalar(select(UserIdentity.subject).where(UserIdentity.user_id == karan.id)) == "U-KARAN"
        assert db.scalar(select(Conversation).where(Conversation.user_id == karan.id)) is not None  # carry on in VaultMind
        event = db.scalars(select(AuditEvent).where(AuditEvent.action == "assistant.query").order_by(AuditEvent.id)).all()[-1]
        assert event.detail["channel"] == "slack"


def test_slash_command_help_and_unknown_people(client, slack_fake):
    assert "Ask me about company documents" in _command(client, slack_fake, "U-KARAN", "help").json()["text"]
    _command(client, slack_fake, "U-STRANGER", "leave policy")
    assert "couldn't match your Slack account" in slack_fake.responses[-1]["text"]
    assert _command(client, slack_fake, "U-KARAN", "leave", response_url="https://evil.example/hook").status_code == 400


def test_mentions_are_answered_only_to_the_person_who_asked(client, slack_fake):
    event = {
        "type": "event_callback",
        "event_id": "Ev1",
        "event": {"type": "app_mention", "user": "U-ROHAN", "text": "<@UBOT> SDE-2 salary band?", "channel": "C1", "ts": "1.0"},
    }
    assert _event(client, event).status_code == 200
    assert [(m, p["channel"], p["user"]) for m, p in slack_fake.posted] == [("chat.postEphemeral", "C1", "U-ROHAN")]
    assert "22 to 34" in slack_fake.posted[0][1]["text"]

    _event(client, event)  # the same event again
    _event(client, {**event, "event_id": "Ev2"}, {"X-Slack-Retry-Num": "1"})  # a retry
    _event(client, {**event, "event_id": "Ev3", "event": {**event["event"], "bot_id": "B1"}})  # a bot
    assert len(slack_fake.posted) == 1


def test_direct_messages_get_a_direct_reply(client, slack_fake):
    event = {
        "type": "event_callback",
        "event_id": "Ev9",
        "event": {"type": "message", "channel_type": "im", "user": "U-KARAN", "text": "How many days of leave?", "channel": "D1"},
    }
    _event(client, event)
    [(method, payload)] = slack_fake.posted
    assert method == "chat.postMessage" and payload["channel"] == "D1" and "leave" in payload["text"].lower()


# ---------------------------------------------------------------- first week and preferences


def test_first_week_checklist_ticks_itself_off(client, login):
    aarav = login("aarav")  # the seed made Aarav a new joiner
    checklist = client.get("/api/onboarding", headers=aarav).json()
    assert checklist["active"] and checklist["steps"][0]["id"] == "ask" and not checklist["steps"][0]["done"]
    read = next(s for s in checklist["steps"] if s["id"].startswith("read-"))
    assert not read["done"]

    client.get(read["link"].replace("/library/", "/api/documents/"), headers=aarav)
    client.post("/api/assistant/chat", headers=aarav, json={"message": "What should I read first?"})
    after = {s["id"]: s["done"] for s in client.get("/api/onboarding", headers=aarav).json()["steps"]}
    assert after["ask"] and after[read["id"]]

    assert client.post("/api/onboarding/dismiss", headers=aarav).json()["active"] is False
    assert client.get("/api/onboarding", headers=aarav).json()["active"] is False
    assert client.get("/api/onboarding", headers=login("rohan")).json()["active"] is False


def test_people_choose_whether_to_get_email(client, login):
    neha = login("neha")
    prefs = client.get("/api/me/preferences", headers=neha).json()
    assert prefs == {"notify_email": True, "email_available": False, "slack_available": False, "slack_linked": False}
    assert client.patch("/api/me/preferences", headers=neha, json={"notify_email": False}).json()["notify_email"] is False
    with SessionLocal() as db:
        assert db.scalar(select(User.notify_email).where(User.email == "neha@northwind.example")) is False
