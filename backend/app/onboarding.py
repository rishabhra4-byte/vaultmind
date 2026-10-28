"""The first-week checklist for new joiners.

People get it for 30 days after their account is created by sign-in or by an admin (who can
also turn it on again). Steps tick themselves off from what the person actually does, read from
the audit log: no extra tracking.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AuditEvent, Classification, Document, User
from .services import AccessView, access_view
from .text import tokenize

STARTER_WORDS = set(tokenize("onboarding starter handbook guide welcome orientation conduct security basics new joiner"))


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def starter_documents(view: AccessView, limit: int = 3) -> list[Document]:
    """Documents a new joiner should read first: onboarding-type documents they can open, their
    own team's first. Stable, so the checklist doesn't reshuffle as they read."""
    picks = []
    for doc_id in view.allowed:
        doc = view.docs[doc_id]
        words = set(tokenize(" ".join([doc.title, *(doc.tags or [])])))
        overlap = len(words & STARTER_WORDS)
        if not overlap or "saved answer" in (doc.tags or []):
            continue
        rank = 2 * (doc.owner_team_id == view.user.team_id) + overlap + (doc.classification == Classification.PUBLIC)
        picks.append((-rank, doc.id, doc))
    return [d for *_, d in sorted(picks)[:limit]]


def checklist(db: Session, user: User) -> dict:
    until = _aware(user.onboarding_until) if user.onboarding_until else None
    active = until is not None and until > datetime.now(UTC)
    if not active:
        return {"active": False, "until": None, "steps": [], "done": 0, "total": 0}

    def did(*actions: str) -> set:
        return set(db.scalars(select(AuditEvent.target_id).where(AuditEvent.actor_id == user.id, AuditEvent.action.in_(actions))))

    viewed = did("document.view", "document.download")
    asked = bool(did("assistant.query"))
    explored_access = bool(did("document.view_denied", "access.request"))
    view = access_view(db, user)

    steps = [
        {
            "id": "ask",
            "label": "Ask VaultMind a question",
            "hint": "For example: “What should I read first?”",
            "done": asked,
            "link": "/",
        }
    ]
    for doc in starter_documents(view):
        steps.append(
            {
                "id": f"read-{doc.id}",
                "label": f"Read “{doc.title}”",
                "hint": f"From {doc.owner_team.name}",
                "done": doc.id in viewed,
                "link": f"/library/{doc.id}",
            }
        )
    own_team = {i for i in view.allowed if view.docs[i].owner_team_id == user.team_id}
    steps.append(
        {
            "id": "team",
            "label": f"Open a document from {user.team.name}",
            "hint": "Your team's documents are in the Library",
            "done": bool(viewed & own_team),
            "link": f"/library?team={user.team_id}",
        }
    )
    steps.append(
        {
            "id": "access",
            "label": "See how access works",
            "hint": "Open a locked document to see why it's locked, or ask for access",
            "done": explored_access,
            "link": "/library",
        }
    )
    return {"active": True, "until": until, "steps": steps, "done": sum(s["done"] for s in steps), "total": len(steps)}
