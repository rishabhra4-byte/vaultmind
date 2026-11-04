"""Verified answers: approved answers to common questions, shown first with who verified them.

An owner (an administrator, for now) writes the answer once, picks the documents it comes
from, and says how often it must be reviewed. When someone asks the same question, they get
that answer, marked "Verified by <name> for <team>, <date>", instead of a fresh one, but only

- if they can open every one of its sources (otherwise it could reveal what a locked document
  says, so they get an ordinary answer from what they can open);
- while it's current: reviewed within its review period, and no source has a newer version
  than when it was verified (otherwise the owners are reminded, and people get an ordinary
  answer until someone reviews it);
- when the question really is the same one: words are weighed by how rare they are in the
  company's documents, so "sick leave" doesn't match a verified answer about "earned leave".
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .filters import DocFilter
from .models import Document, User, VerifiedAnswer, utcnow
from .search import index
from .services import AccessView
from .text import GENERIC, tokenize

MATCH = 0.72  # weighted overlap a question needs with a verified question (or phrasing)
FILLER = frozenset({"what", "which", "tell", "know", "want", "pleas", "me", "i", "my"})


def _aware(when: datetime) -> datetime:
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def _terms(text: str) -> set[str]:
    return {t for t in tokenize(text) if t not in GENERIC and t not in FILLER and len(t) > 1}


def similarity(question: str, verified_question: str) -> float:
    """How much two questions ask the same thing: overlap of their words, each weighed by how
    rare it is in the documents (a word no document uses weighs most), from 0 to 1."""
    a, b = _terms(question), _terms(verified_question)
    if len(a & b) < 2 and not (a and a == b):
        return 0.0
    weight = {t: index.idf(t) for t in a | b}
    union = sum(weight.values())
    return sum(weight[t] for t in a & b) / union if union else 0.0


# ---------------------------------------------------------------- whether one is current


@dataclass
class State:
    status: str  # current | due | changed | missing | off
    due_at: datetime
    changed: list[int]  # sources with a newer version than when verified
    missing: list[int]  # sources deleted since

    @property
    def label(self) -> str:
        return {
            "current": "Current",
            "due": "Due for review",
            "changed": "A source has changed",
            "missing": "A source was deleted",
            "off": "Switched off",
        }[self.status]


def state(va: VerifiedAnswer, docs: dict[int, Document], now: datetime | None = None) -> State:
    now = now or datetime.now(UTC)
    due_at = _aware(va.verified_at) + timedelta(days=va.review_days)
    missing = [d for d in va.doc_ids if d not in docs]
    changed = [d for d in va.doc_ids if d in docs and docs[d].version > int((va.doc_versions or {}).get(str(d), 0))]
    if not va.active:
        status = "off"
    elif missing:
        status = "missing"
    elif changed:
        status = "changed"
    elif now >= due_at:
        status = "due"
    else:
        status = "current"
    return State(status, due_at, changed, missing)


# ---------------------------------------------------------------- answering with one


def find(db: Session, view: AccessView, question: str, filters: DocFilter | None = None) -> VerifiedAnswer | None:
    """The verified answer to show this person for this question, if there is one."""
    best: tuple[float, VerifiedAnswer] | None = None
    for va in db.scalars(select(VerifiedAnswer).where(VerifiedAnswer.active.is_(True))):
        if not va.doc_ids or any(d not in view.allowed for d in va.doc_ids):
            continue  # someone who can't open a source never sees an answer built from it
        if state(va, view.docs).status != "current":
            continue
        if filters is not None and not all(filters.matches(view.docs[d]) for d in va.doc_ids):
            continue
        score = max(similarity(question, q) for q in [va.question, *(va.phrasings or [])])
        if score >= MATCH and (best is None or score > best[0]):
            best = (score, va)
    return best[1] if best else None


def card(va: VerifiedAnswer, st: State | None = None) -> dict:
    """What the answer shows about who stands behind it."""
    return {
        "id": va.id,
        "question": va.question,
        "by": va.verified_by.full_name if va.verified_by else "",
        "team": va.owner_team.name,
        "verified_at": _aware(va.verified_at).isoformat(),
        "review_by": (st.due_at if st else _aware(va.verified_at) + timedelta(days=va.review_days)).isoformat(),
    }


# ---------------------------------------------------------------- managing them


_CITE = re.compile(r"\[D(\d+)(?:\.\d+)?\]")


class Invalid(ValueError):
    pass


def _check(db: Session, question: str, answer: str, doc_ids: list[int], phrasings: list[str], review_days: int) -> None:
    if not question.strip() or not answer.strip():
        raise Invalid("A verified answer needs a question and an answer.")
    if not doc_ids:
        raise Invalid("Pick at least one document the answer comes from, so people can check it.")
    known = set(db.scalars(select(Document.id).where(Document.id.in_(doc_ids))))
    if set(doc_ids) - known:
        raise Invalid("One of the chosen documents doesn't exist any more.")
    cited = {int(m) for m in _CITE.findall(answer)}
    if cited - set(doc_ids):
        raise Invalid("The answer cites a document that isn't one of its sources.")
    if len(_terms(question)) < 2 and not any(len(_terms(p)) >= 2 for p in phrasings):
        raise Invalid("Write the question with at least two words that say what it's about, so it isn't matched by accident.")
    if not 7 <= review_days <= 365:
        raise Invalid("Review it every 7 to 365 days.")


def save(
    db: Session,
    admin: User,
    *,
    va: VerifiedAnswer | None = None,
    question: str,
    answer: str,
    doc_ids: list[int],
    phrasings: list[str],
    owner_team_id: int,
    review_days: int = 90,
    active: bool = True,
) -> VerifiedAnswer:
    """Create or change one. Saving counts as verifying it: today, by this person, against the
    sources' current versions."""
    phrasings = [p.strip()[:300] for p in phrasings if p.strip()][:10]
    doc_ids = list(dict.fromkeys(int(d) for d in doc_ids))[:10]
    _check(db, question, answer, doc_ids, phrasings, review_days)
    if va is None:
        va = VerifiedAnswer(owner_team_id=owner_team_id)
        db.add(va)
    va.question = question.strip()[:300]
    va.answer = answer.strip()[:4000]
    va.phrasings = phrasings
    va.doc_ids = doc_ids
    va.owner_team_id = owner_team_id
    va.review_days = review_days
    va.active = active
    mark_reviewed(db, va, admin)
    return va


def mark_reviewed(db: Session, va: VerifiedAnswer, admin: User) -> None:
    versions = dict(db.execute(select(Document.id, Document.version).where(Document.id.in_(va.doc_ids))).all())
    va.doc_versions = {str(d): versions.get(d, 0) for d in va.doc_ids}
    va.verified_by_id = admin.id
    va.verified_at = utcnow()


def as_dict(va: VerifiedAnswer, docs: dict[int, Document]) -> dict:
    st = state(va, docs)
    return {
        **card(va, st),
        "answer": va.answer,
        "phrasings": va.phrasings or [],
        "owner_team_id": va.owner_team_id,
        "review_days": va.review_days,
        "active": va.active,
        "status": st.status,
        "status_label": st.label,
        "sources": [
            {"id": d, "title": docs[d].title if d in docs else "(deleted)", "changed": d in st.changed} for d in va.doc_ids
        ],
    }


def remind(db: Session, now: datetime | None = None) -> int:
    """Tell whoever verified an answer, and the administrators, when it needs a review: its
    review date passed, or one of its sources changed or was deleted. Once per answer and state."""
    from .notify import notify

    docs = {d.id: d for d in db.scalars(select(Document))}
    admins = set(db.scalars(select(User.id).where(User.is_admin.is_(True), User.is_active.is_(True))))
    sent = 0
    for va in db.scalars(select(VerifiedAnswer).where(VerifiedAnswer.active.is_(True))):
        st = state(va, docs, now)
        if st.status == "current":
            continue
        why = {
            "due": f"Its review date was {st.due_at:%d %b %Y}.",
            "changed": "A document it comes from has a new version: "
            + ", ".join(docs[d].title for d in st.changed)
            + ". Until someone reviews it, people get an ordinary answer.",
            "missing": "A document it comes from was deleted. Until someone reviews it, people get an ordinary answer.",
        }[st.status]
        who = admins | ({va.verified_by_id} if va.verified_by_id else set())
        sent += len(
            notify(
                db,
                who,
                kind="verified_review",
                title=f"Review the verified answer to “{va.question[:120]}”",
                body=why,
                link="/admin/verified",
                dedupe_key=f"verified:{va.id}:{st.status}:{_aware(va.verified_at):%Y%m%d%H%M%S}",
            )
        )
    return sent
