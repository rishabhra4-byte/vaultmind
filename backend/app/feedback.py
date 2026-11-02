"""Feedback on answers: did this help?

People mark an answer helpful or not; for unhelpful ones they can say why (wrong, missing
something, the wrong source, or it should have said it doesn't know) and add a note.
Administrators see the unhelpful ones on the overview (without who gave them, like the other
insights), and `python -m app.cli export-feedback` writes them out as drafts for the
answer-quality benchmark, which is how real questions get into it.

Feedback is deleted with its conversation.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import audit
from .models import AnswerFeedback, Conversation, Document, Message, User

REASONS = {
    "wrong": "The answer was wrong",
    "missing": "Something was missing",
    "source": "It used the wrong document",
    "should_abstain": "It should have said it doesn't know",
    "other": "Something else",
}


def set_feedback(db: Session, user: User, message: Message, rating: str | None, reason: str = "", note: str = "") -> dict | None:
    """Record (or change, or with rating None remove) this person's feedback on an answer."""
    row = db.scalar(select(AnswerFeedback).where(AnswerFeedback.message_id == message.id, AnswerFeedback.user_id == user.id))
    if rating is None:
        if row is not None:
            db.delete(row)
        return None
    if row is None:
        row = AnswerFeedback(message_id=message.id, user_id=user.id, rating=rating)
        db.add(row)
    row.rating = rating
    row.reason = reason if rating == "down" else ""
    row.note = note.strip()[:500] if rating == "down" else ""
    audit.record(
        db,
        user,
        "assistant.feedback",
        "message",
        message.id,
        rating=rating,
        reason=row.reason,
        mode=(message.meta or {}).get("mode", ""),
    )
    return as_dict(row)


def as_dict(row: AnswerFeedback) -> dict:
    return {"rating": row.rating, "reason": row.reason, "note": row.note}


def for_messages(db: Session, user: User, message_ids: list[int]) -> dict[int, dict]:
    if not message_ids:
        return {}
    rows = db.scalars(select(AnswerFeedback).where(AnswerFeedback.message_id.in_(message_ids), AnswerFeedback.user_id == user.id))
    return {r.message_id: as_dict(r) for r in rows}


def _question_for(db: Session, message: Message) -> str:
    return (
        db.scalar(
            select(Message.content)
            .where(Message.conversation_id == message.conversation_id, Message.role == "user", Message.id < message.id)
            .order_by(Message.id.desc())
            .limit(1)
        )
        or ""
    )


def review(db: Session, *, rating: str | None = "down", days: int = 30, limit: int = 50) -> dict:
    """Recent feedback for administrators, without who gave it."""
    since = datetime.now(UTC) - timedelta(days=days)
    counts = dict(
        db.execute(
            select(AnswerFeedback.rating, func.count()).where(AnswerFeedback.updated_at >= since).group_by(AnswerFeedback.rating)
        ).all()
    )
    stmt = (
        select(AnswerFeedback, Message)
        .join(Message, Message.id == AnswerFeedback.message_id)
        .where(AnswerFeedback.updated_at >= since)
        .order_by(AnswerFeedback.updated_at.desc())
        .limit(limit)
    )
    if rating:
        stmt = stmt.where(AnswerFeedback.rating == rating)
    titles = {}
    items = []
    for fb, msg in db.execute(stmt).all():
        meta = msg.meta or {}
        cited = [c.get("id") for c in meta.get("citations", []) if isinstance(c, dict) and isinstance(c.get("id"), int)]
        missing = [i for i in cited if i not in titles]
        if missing:
            titles.update(dict(db.execute(select(Document.id, Document.title).where(Document.id.in_(missing))).all()))
        items.append(
            {
                "id": fb.id,
                "rating": fb.rating,
                "reason": fb.reason,
                "reason_label": REASONS.get(fb.reason, ""),
                "note": fb.note,
                "question": _question_for(db, msg),
                "answer": msg.content[:600],
                "mode": meta.get("mode", ""),
                "enough": (meta.get("evidence") or {}).get("enough"),
                "cited": [{"id": i, "title": titles.get(i, f"#{i}")} for i in cited],
                "at": fb.updated_at.isoformat() if fb.updated_at.tzinfo else fb.updated_at.replace(tzinfo=UTC).isoformat(),
            }
        )
    return {"counts": {"up": counts.get("up", 0), "down": counts.get("down", 0)}, "days": days, "items": items}


def export(db: Session, out: Path, *, rating: str | None = "down", days: int = 365) -> int:
    """Write feedback as JSON lines: drafts for the benchmark. Each needs a person to fill in
    the right documents and fact (or mark it as one nothing should answer) before it's a case."""
    since = datetime.now(UTC) - timedelta(days=days)
    stmt = (
        select(AnswerFeedback, Message, Conversation.user_id)
        .join(Message, Message.id == AnswerFeedback.message_id)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(AnswerFeedback.updated_at >= since)
        .order_by(AnswerFeedback.id)
    )
    if rating:
        stmt = stmt.where(AnswerFeedback.rating == rating)
    emails = dict(db.execute(select(User.id, User.email)).all())
    n = 0
    with out.open("w") as f:
        for fb, msg, asker in db.execute(stmt).all():
            meta = msg.meta or {}
            draft = {
                "id": f"feedback-{fb.id}",
                # Answer as the same person, so the same documents are in scope.
                "who": emails.get(asker, ""),
                "question": _question_for(db, msg),
                "expect": None,  # to fill in: "answer", "abstain" or "deny"
                "docs": [],  # to fill in: the documents that answer it
                "says": [],  # to fill in: the fact the answer must state
                "feedback": {"rating": fb.rating, "reason": fb.reason, "note": fb.note},
                "got": {
                    "mode": meta.get("mode", ""),
                    "answer": msg.content,
                    "cited": [c.get("title") for c in meta.get("citations", []) if isinstance(c, dict)],
                },
            }
            f.write(json.dumps(draft, ensure_ascii=False) + "\n")
            n += 1
    return n
