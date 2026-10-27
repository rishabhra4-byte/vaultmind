"""Explainable, role-aware recommendations.

Each document a user can open gets a score from a handful of signals, and every signal
that fires adds a human-readable reason ("Popular with Engineering this month"). No
black box: the UI shows exactly why something was suggested.
"""

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import AuditEvent, Document, Position, User
from .services import AccessView
from .text import tokenize

VIEW_ACTIONS = ("document.view", "document.download")


@dataclass
class Recommendation:
    document: Document
    score: float
    reasons: list[str]


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def recommend(db: Session, view: AccessView, limit: int = 6) -> list[Recommendation]:
    user: User = view.user
    now = datetime.now(UTC)
    month_ago = now - timedelta(days=30)

    peer_views = dict(
        db.execute(
            select(AuditEvent.target_id, func.count())
            .join(User, User.id == AuditEvent.actor_id)
            .where(
                AuditEvent.action.in_(VIEW_ACTIONS),
                AuditEvent.created_at >= month_ago,
                User.team_id == user.team_id,
                User.id != user.id,
            )
            .group_by(AuditEvent.target_id)
        ).all()
    )
    my_last_view = dict(
        db.execute(
            select(AuditEvent.target_id, func.max(AuditEvent.created_at))
            .where(AuditEvent.action.in_(VIEW_ACTIONS), AuditEvent.actor_id == user.id)
            .group_by(AuditEvent.target_id)
        ).all()
    )
    role_terms = set(tokenize(user.title))
    if user.position <= Position.ASSOCIATE:
        role_terms |= {"onboard", "guide", "starter", "handbook"}
    if user.position >= Position.MANAGER:
        role_terms |= {"review", "plan", "budget", "hiring", "approval"}

    recs: list[Recommendation] = []
    for doc_id in view.allowed:
        doc = view.docs[doc_id]
        score, reasons = 0.0, []

        if doc.owner_team_id == user.team_id:
            score += 3
            reasons.append(f"From your team ({doc.owner_team.name})")
        elif user.team_id in doc.shared_team_ids:
            score += 1.5
            reasons.append("Shared with your team")

        doc_terms = set(tokenize(" ".join([doc.title, *(doc.tags or [])])))
        overlap = role_terms & doc_terms
        if overlap:
            score += 2 + 0.5 * (len(overlap) - 1)
            reasons.append(f"Relevant to your role as {user.title or Position(user.position).label}")

        peers = peer_views.get(doc_id, 0)
        if peers:
            score += 1.5 * math.log1p(peers)
            reasons.append(f"Popular with {user.team.name} this month")

        updated = _aware(doc.updated_at)
        if updated >= now - timedelta(days=14):
            score += 1
            reasons.append("Recently updated")

        seen = my_last_view.get(doc_id)
        if seen is not None:
            if updated > _aware(seen):
                score += 0.5
                reasons.append("Changed since you last opened it")
            else:
                score -= 2.5  # already read and unchanged: make room for something new

        if doc_id in view.grants:
            score += 1
            reasons.append("You were recently granted access")

        if score > 0 and reasons:
            recs.append(Recommendation(doc, round(score, 2), reasons))

    recs.sort(key=lambda r: (r.score, _aware(r.document.updated_at)), reverse=True)
    return recs[:limit]
