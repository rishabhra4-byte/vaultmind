"""The fix-it queue: real questions the assistant got wrong, and whether it gets them right now.

Two kinds of question land here: answers people marked unhelpful, and questions where nothing
matched or the documents didn't hold enough to answer. They're grouped by question, without
who asked. An administrator says what the right answer comes from (one or more documents, and
optionally words it must say) or that nothing should answer it; that becomes a case.

Each case is checked by asking the question again as the person who first asked it, so the same
documents are in scope, and seeing whether the reply uses the right documents (or says it
doesn't know, when that's right). Checks use offline answers: the same search, evidence check
and verified answers people get, without spending AI calls; the AI's wording isn't checked.
Together the cases are a benchmark made of the company's own questions.
"""

import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .feedback import REASONS, _question_for
from .models import AnswerFeedback, AuditEvent, Conversation, Document, Message, ReviewCase, User, utcnow
from .policy import is_discoverable_to

WHY = {
    "unhelpful": "Marked unhelpful",
    "no_match": "Nothing matched",
    "not_enough": "Not enough evidence",
}


def normal(question: str) -> str:
    return " ".join(re.sub(r"[^\w\s'-]", " ", question.lower()).split())[:300]


def _aware(when: datetime) -> datetime:
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def _flag(detail: dict) -> str | None:
    """Why an asked question belongs in the queue, from its activity-log entry, or None."""
    if detail.get("intent", "ask") != "ask" or detail.get("document_id"):
        return None  # summaries, drafts, questions about one document: not search questions
    if detail.get("hits") == 0:
        return "no_match"
    if detail.get("enough") is False:
        return "not_enough"
    return None


def queue(db: Session, days: int = 90) -> dict:
    """Open items (grouped by question, newest problems first), the cases already decided, and
    how many of those the assistant gets right at their last check."""
    since = datetime.now(UTC) - timedelta(days=days)
    titles = dict(db.execute(select(Document.id, Document.title)).all())
    items: dict[str, dict] = {}

    def item(key: str, question: str, when: datetime) -> dict:
        row = items.setdefault(
            key,
            {"key": key, "question": question, "asked": 0, "why": {}, "notes": [], "last": when, "answer": None},
        )
        if when >= row["last"]:
            row["last"], row["question"] = when, question
        return row

    asked: dict[str, int] = {}
    for e in db.scalars(select(AuditEvent).where(AuditEvent.action == "assistant.query", AuditEvent.created_at >= since)):
        d = e.detail or {}
        question = str(d.get("question", "")).strip()
        if not question:
            continue
        key = normal(question)
        asked[key] = asked.get(key, 0) + 1
        why = _flag(d)
        if why is None:
            continue
        when = _aware(e.created_at)
        row = item(key, question, when)
        row["why"][why] = row["why"].get(why, 0) + 1
        if row["answer"] is None or when >= row["last"]:
            cited = [i for i in d.get("cited") or [] if isinstance(i, int)]
            row["answer"] = {
                "mode": d.get("mode", ""),
                "text": "",
                "cited": [{"id": i, "title": titles.get(i, f"#{i}")} for i in cited],
            }

    stmt = (
        select(AnswerFeedback, Message)
        .join(Message, Message.id == AnswerFeedback.message_id)
        .where(AnswerFeedback.rating == "down", AnswerFeedback.updated_at >= since)
        .order_by(AnswerFeedback.updated_at)
    )
    for fb, msg in db.execute(stmt).all():
        question = _question_for(db, msg).strip()
        if not question:
            continue
        key = normal(question)
        when = _aware(fb.updated_at)
        row = item(key, question, when)
        row["why"]["unhelpful"] = row["why"].get("unhelpful", 0) + 1
        if fb.reason or fb.note:
            row["notes"].append({"reason": REASONS.get(fb.reason, ""), "note": fb.note})
        meta = msg.meta or {}
        cited = [c.get("id") for c in meta.get("citations", []) if isinstance(c, dict) and isinstance(c.get("id"), int)]
        row["answer"] = {
            "mode": meta.get("mode", ""),
            "text": msg.content[:700],
            "cited": [{"id": i, "title": titles.get(i, f"#{i}")} for i in cited],
        }

    cases = list(db.scalars(select(ReviewCase).order_by(ReviewCase.resolved_at.desc())))
    decided = {c.key for c in cases}
    open_items = []
    for key, row in items.items():
        if key in decided:
            continue
        row["asked"] = max(asked.get(key, 0), sum(row["why"].values()))
        row["why"] = [{"kind": k, "label": WHY[k], "count": n} for k, n in row["why"].items()]
        row["notes"] = row["notes"][-3:]
        row["last"] = row["last"].isoformat()
        open_items.append(row)
    open_items.sort(key=lambda r: (r["asked"], r["last"]), reverse=True)  # most asked, then most recent
    resolved = [as_dict(c, titles) for c in cases if c.status == "resolved"]
    checked = [c for c in resolved if c["check"].get("passed") is not None]
    return {
        "days": days,
        "summary": {
            "open": len(open_items),
            "resolved": len(resolved),
            "dismissed": sum(1 for c in cases if c.status == "dismissed"),
            "checked": len(checked),
            "passing": sum(1 for c in checked if c["check"]["passed"]),
        },
        "open": open_items[:100],
        "resolved": resolved,
    }


def as_dict(case: ReviewCase, titles: dict[int, str]) -> dict:
    return {
        "id": case.id,
        "key": case.key,
        "question": case.question,
        "status": case.status,
        "expect": case.expect,
        "docs": [{"id": d, "title": titles.get(d, "(deleted)")} for d in case.doc_ids],
        "says": case.says,
        "note": case.note,
        "resolved_at": _aware(case.resolved_at).isoformat(),
        "check": case.last_check or {},
    }


def _latest_asker(db: Session, key: str) -> int | None:
    """Who asked this question most recently (to check it as them; never shown)."""
    for e in db.scalars(
        select(AuditEvent).where(AuditEvent.action == "assistant.query").order_by(AuditEvent.created_at.desc()).limit(5000)
    ):
        if normal(str((e.detail or {}).get("question", ""))) == key:
            return e.actor_id
    rows = db.execute(
        select(Message, Conversation.user_id)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(Message.role == "user")
        .order_by(Message.id.desc())
        .limit(5000)
    ).all()
    return next((uid for msg, uid in rows if normal(msg.content) == key), None)


class Invalid(ValueError):
    pass


def decide(
    db: Session,
    admin: User,
    *,
    question: str,
    status: str,
    expect: str = "answer",
    doc_ids: list[int] | None = None,
    says: list[str] | None = None,
    note: str = "",
) -> ReviewCase:
    """Record what the right answer to a question is (status "resolved"), or that it needs no
    action (status "dismissed"). Deciding again replaces the earlier decision."""
    key = normal(question)
    if not key:
        raise Invalid("There's no question to decide on.")
    doc_ids = list(dict.fromkeys(int(d) for d in doc_ids or []))[:5]
    says = [s.strip()[:120] for s in says or [] if s.strip()][:5]
    if status == "resolved" and expect == "answer" and not doc_ids:
        raise Invalid("Pick the document that answers it, or choose “Nothing answers this”.")
    if doc_ids and len(set(db.scalars(select(Document.id).where(Document.id.in_(doc_ids))))) != len(doc_ids):
        raise Invalid("One of the chosen documents doesn't exist any more.")
    case = db.scalar(select(ReviewCase).where(ReviewCase.key == key))
    if case is None:
        case = ReviewCase(key=key, asked_by_id=_latest_asker(db, key))
        db.add(case)
    case.question = question.strip()[:300]
    case.status = status
    case.expect = expect
    case.doc_ids = doc_ids if expect == "answer" else []
    case.says = says if expect == "answer" else []
    case.note = note.strip()[:500]
    case.resolved_by_id = admin.id
    case.resolved_at = utcnow()
    case.last_check = {}
    return case


def _norm_text(text: str) -> str:
    return " ".join(re.sub(r"[*_`\[\]]", " ", text.lower()).split())


def check(db: Session, case: ReviewCase) -> dict:
    """Ask the question again as the person who asked it, offline, and compare."""
    from .assistant import _respond
    from .services import access_view

    asker = db.get(User, case.asked_by_id) if case.asked_by_id else None
    now = datetime.now(UTC).isoformat()
    if asker is None or not asker.is_active:
        result = {
            "at": now,
            "passed": None,
            "problems": ["The person who asked no longer has an active account, so it can't be checked as them."],
        }
        case.last_check = result
        return result
    reply = _respond(db, asker, case.question, [], None, document_id=None, intent=None, compare_with=None, filters=None)
    view = access_view(db, asker)
    cited = [c["id"] for c in reply.citations]
    locked = [l["id"] for l in reply.locked]
    enough = (reply.evidence or {}).get("enough")
    titles = {d.id: d.title for d in view.docs.values()}
    problems: list[str] = []
    if case.expect == "abstain":
        if enough:
            problems.append("It answered, but nothing should answer this.")
    else:
        openable = [d for d in case.doc_ids if d in view.allowed]
        if openable:
            if not set(openable) & set(cited):
                names = " or ".join(f"“{titles.get(d, d)}”" for d in openable)
                problems.append(f"It didn't use {names}.")
            if enough is False:
                problems.append("It said there wasn't enough evidence.")
            text = _norm_text(reply.content)
            problems += [f"It didn't say “{s}”." for s in case.says if _norm_text(s) not in text]
        else:
            # The right document is one this person can't open: the right reply doesn't use it,
            # and (if they may know it exists) offers it as something to request.
            for d in case.doc_ids:
                doc = view.docs.get(d)
                if d in cited:
                    problems.append(f"It used “{titles.get(d, d)}”, which this person can't open.")
                elif doc is not None and is_discoverable_to(asker, doc) and d not in locked:
                    problems.append(f"They can't open “{doc.title}”, and it isn't offered as something to request.")
    result = {
        "at": now,
        "passed": not problems,
        "problems": problems,
        "got": {
            "mode": reply.mode,
            "enough": enough,
            "cited": [{"id": i, "title": titles.get(i, f"#{i}")} for i in cited],
            "locked": [{"id": l["id"], "title": l["title"]} for l in reply.locked],
            "text": reply.content[:500],
        },
    }
    case.last_check = result
    return result


def check_all(db: Session) -> dict:
    cases = list(db.scalars(select(ReviewCase).where(ReviewCase.status == "resolved")))
    results = [check(db, c) for c in cases]
    checked = [r for r in results if r["passed"] is not None]
    return {"checked": len(checked), "passing": sum(1 for r in checked if r["passed"])}


def export(db: Session) -> list[dict]:
    """The decided cases as benchmark entries (JSON lines), for keeping or comparing. Like the
    queue, they say what level and team asked, not who."""
    from .models import Position

    titles = dict(db.execute(select(Document.id, Document.title)).all())
    out = []
    for c in db.scalars(select(ReviewCase).where(ReviewCase.status == "resolved").order_by(ReviewCase.id)):
        asker = db.get(User, c.asked_by_id) if c.asked_by_id else None
        out.append(
            {
                "id": f"case-{c.id}",
                # Their level and team (whose documents were in scope), never who they are.
                "asked_by": f"{Position(asker.position).label}, {asker.team.name}" if asker else "",
                "question": c.question,
                "expect": c.expect,
                "docs": [titles.get(d, "") for d in c.doc_ids],
                "says": c.says,
                "last_check": (c.last_check or {}).get("passed"),
            }
        )
    return out
