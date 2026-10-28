"""Storing a question and the assistant's reply, from the web app or from Slack."""

from sqlalchemy.orm import Session

from . import actions, audit
from .assistant import Reply
from .models import Conversation, Message, User, utcnow


def record_exchange(
    db: Session,
    user: User,
    convo: Conversation,
    question: str,
    reply: Reply,
    *,
    intent: str | None = None,
    document_id: int | None = None,
    channel: str = "web",
) -> tuple[Message, Message]:
    """Save both messages (and any proposed actions) and audit the query. The caller commits."""
    meta = reply.meta
    if reply.actions:
        meta["actions"] = actions.persist(db, user, reply.actions)
    user_msg = Message(conversation_id=convo.id, role="user", content=question, meta={"intent": intent} if intent else {})
    bot_msg = Message(conversation_id=convo.id, role="assistant", content=reply.content, meta=meta)
    db.add_all([user_msg, bot_msg])
    convo.updated_at = utcnow()
    audit.record(
        db,
        user,
        "assistant.query",
        "conversation",
        convo.id,
        question=question[:300],
        mode=reply.mode,
        intent=reply.intent,
        hits=reply.hits,
        document_id=document_id,
        fallback=reply.fallback_reason,
        cited=[c["id"] for c in reply.citations],
        locked=[l["id"] for l in reply.locked],
        channel=channel,
    )
    return user_msg, bot_msg


def short_title(text: str) -> str:
    return text if len(text) <= 60 else text[:57].rsplit(" ", 1)[0] + "…"
