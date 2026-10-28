"""VaultMind in Slack: a /vaultmind slash command, @mentions, and direct messages.

Answers are permission-aware exactly like the web app: the Slack user is matched to their
VaultMind account by the email on their Slack profile, and the assistant answers as that
person. Answers in channels are *ephemeral* (only the person who asked sees them), because an
answer may quote documents the rest of the channel can't open. Direct messages are private
already. Every answer is also saved as a conversation in VaultMind, so the person can carry on
there and confirm any proposed action (VaultMind never acts from Slack on its own).

Requests are checked with Slack's signing secret (HMAC-SHA256 over the timestamp and body,
rejected when older than five minutes). Slack expects an answer within three seconds, so the
work happens in a background thread and the answer is sent when it's ready.
"""

import hashlib
import hmac
import logging
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable

import httpx
from fastapi import HTTPException
from sqlalchemy import func, select

from . import slack
from .assistant import CITATION, Reply, respond
from .chat_log import record_exchange, short_title
from .config import get_settings
from .database import SessionLocal
from .deps import chat_limiter
from .models import Conversation, User, UserIdentity

log = logging.getLogger(__name__)

HELP = (
    "Ask me about company documents, for example `/vaultmind how many days of leave do I get?`. "
    "I only use documents you can open in VaultMind, and only you see my answers."
)
MAX_AGE_SECONDS = 300
_seen: OrderedDict[str, None] = OrderedDict()
_seen_lock = threading.Lock()


def verify(secret: str, timestamp: str, body: bytes, signature: str, now: float | None = None) -> bool:
    try:
        age = abs((now or time.time()) - int(timestamp))
    except ValueError:
        return False
    if age > MAX_AGE_SECONDS or not secret:
        return False
    expected = "v0=" + hmac.new(secret.encode(), f"v0:{timestamp}:".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def first_time(event_id: str) -> bool:
    """Slack may deliver an event more than once; handle each id once."""
    if not event_id:
        return True
    with _seen_lock:
        if event_id in _seen:
            return False
        _seen[event_id] = None
        while len(_seen) > 2000:
            _seen.popitem(last=False)
    return True


def run_in_background(fn: Callable[[], None]) -> None:
    """Swapped for a direct call in tests."""
    threading.Thread(target=fn, name="vaultmind-slack", daemon=True).start()


def allowed_response_url(url: str) -> bool:
    """Only post back to Slack itself (the URL arrives in a signed request, but check anyway)."""
    base = get_settings().slack_api_base.rstrip("/")
    origin = re.match(r"^https?://[^/]+", base)
    return url.startswith("https://hooks.slack.com/") or bool(origin and url.startswith(origin.group(0) + "/"))


def vaultmind_user(db, slack_user_id: str) -> User | None:
    """The VaultMind account for a Slack user: remembered, or matched by profile email."""
    if not slack_user_id:
        return None
    known = db.scalar(
        select(User)
        .join(UserIdentity, UserIdentity.user_id == User.id)
        .where(UserIdentity.provider == "slack", UserIdentity.subject == slack_user_id)
    )
    if known is not None:
        return known if known.is_active else None
    email = slack.user_email(slack_user_id)
    if not email:
        return None
    user = db.scalar(select(User).where(func.lower(User.email) == email))
    if user is None or not user.is_active:
        return None
    db.add(UserIdentity(user_id=user.id, provider="slack", subject=slack_user_id))
    db.flush()
    return user


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def to_slack(reply: Reply, conversation_id: int) -> str:
    """The reply as Slack text: numbered links for citations, sources, locked hints, and a link
    to carry on in VaultMind."""
    s = get_settings()
    titles = {c["id"]: c["title"] for c in reply.citations}
    numbers: dict[int, int] = {}

    def link(path: str, label: str) -> str:
        url = s.link(path)
        return f"<{url}|{label}>" if url.startswith("http") else label

    def cite(m: re.Match) -> str:
        doc_id, ordinal = int(m.group(1)), m.group(2)
        n = numbers.setdefault(doc_id, len(numbers) + 1)
        return link(f"/library/{doc_id}" + (f"#p{ordinal}" if ordinal else ""), f"[{n}]")

    text = CITATION.sub(cite, _escape(reply.content))
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    lines = [text]
    if numbers:
        lines.append("")
        lines += [
            f"{n}. {link(f'/library/{doc_id}', _escape(titles.get(doc_id, f'Document {doc_id}')))}"
            for doc_id, n in numbers.items()
        ]
    if reply.locked:
        names = ", ".join(f"{_escape(l['title'])} ({_escape(l['team'])})" for l in reply.locked)
        lines += ["", f"_More matches need extra access: {names}. You can ask for it in VaultMind._"]
    label = {
        "ai": "Written by Claude from documents you can open",
        "offline": "Offline answer: matching passages, no AI",
        "direct": "Prepared by VaultMind without AI",
    }.get(reply.mode, "")
    carry_on = link(f"/?c={conversation_id}", "Continue in VaultMind")
    if reply.actions:
        carry_on = link(f"/?c={conversation_id}", "Open VaultMind to confirm")
    lines += ["", f"_{label}_ · {carry_on}"]
    return "\n".join(lines)


def answer(slack_user_id: str, text: str) -> str:
    """Answer a question from Slack as the matching VaultMind user."""
    question = re.sub(r"<@[A-Z0-9]+>", "", text or "").strip()[:2000]
    if not question or question.lower() in ("help", "?"):
        return HELP
    with SessionLocal() as db:
        user = vaultmind_user(db, slack_user_id)
        db.commit()  # remember the match
        if user is None:
            return (
                "I couldn't match your Slack account to a VaultMind account. Your Slack profile email needs to be the "
                "same as your work email in VaultMind; an administrator can help."
            )
        try:
            chat_limiter.check(user.id)
        except HTTPException:
            return "You're asking faster than I can keep up. Try again in a minute."
        reply = respond(db, user, question, [])
        convo = Conversation(user_id=user.id, title=short_title(question))
        db.add(convo)
        db.flush()
        record_exchange(db, user, convo, question, reply, channel="slack")
        db.commit()
        return to_slack(reply, convo.id)


def answer_command(slack_user_id: str, text: str, response_url: str) -> None:
    try:
        message = answer(slack_user_id, text)
    except Exception:
        log.exception("Slack command failed")
        message = "Something went wrong while answering. Please try again, or ask in VaultMind."
    try:
        httpx.post(response_url, json={"response_type": "ephemeral", "replace_original": True, "text": message}, timeout=10.0)
    except httpx.HTTPError as exc:
        log.warning("Couldn't send the Slack answer: %s", exc.__class__.__name__)


def answer_event(event: dict) -> None:
    """An @mention in a channel (answered so only the asker sees it) or a direct message."""
    try:
        message = answer(event.get("user", ""), event.get("text", ""))
        if event.get("type") == "app_mention":
            payload = {"channel": event.get("channel"), "user": event.get("user"), "text": message}
            if event.get("thread_ts"):
                payload["thread_ts"] = event["thread_ts"]
            slack.call("chat.postEphemeral", **payload)
        else:
            slack.call("chat.postMessage", channel=event.get("channel"), text=message, unfurl_links=False)
    except slack.SlackError as exc:
        log.warning("Couldn't answer in Slack: %s", exc)
    except Exception:
        log.exception("Slack event failed")
