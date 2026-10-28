"""In-app notifications, also delivered by email and Slack when those are set up.

`notify` only writes rows; it's cheap and runs inside the caller's transaction. Delivery to
email and Slack happens later, from the background scheduler (`deliver_pending`), so a slow
mail server never slows down a request.
"""

import logging
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AuditEvent, Document, Notification, User

log = logging.getLogger(__name__)


def notify(
    db: Session,
    user_ids: Iterable[int],
    *,
    kind: str,
    title: str,
    body: str = "",
    link: str = "",
    dedupe_key: str | None = None,
) -> list[Notification]:
    """Add a notification for each user. With a dedupe key, a user gets it at most once."""
    ids = sorted({i for i in user_ids if i is not None})
    if not ids:
        return []
    already: set[int] = set()
    if dedupe_key:
        db.flush()  # so notifications added earlier in this transaction count too
        already = set(
            db.scalars(select(Notification.user_id).where(Notification.dedupe_key == dedupe_key, Notification.user_id.in_(ids)))
        )
    created = []
    for uid in ids:
        if uid in already:
            continue
        n = Notification(
            user_id=uid, kind=kind, title=title[:255], body=body, link=link[:500], dedupe_key=dedupe_key, delivery={}
        )
        db.add(n)
        created.append(n)
    return created


def recent_readers(db: Session, doc_id: int, days: int = 90) -> set[int]:
    """People who opened or downloaded the document recently."""
    since = datetime.now(UTC) - timedelta(days=days)
    return set(
        db.scalars(
            select(AuditEvent.actor_id).where(
                AuditEvent.action.in_(("document.view", "document.download")),
                AuditEvent.target_id == doc_id,
                AuditEvent.created_at >= since,
                AuditEvent.actor_id.is_not(None),
            )
        )
    )


def notify_document_changed(db: Session, doc: Document, summary: str, actor_id: int | None = None) -> int:
    """Tell recent readers who can still open the document that it changed, and how."""
    from .policy import can_view
    from .services import viewer

    readers = recent_readers(db, doc.id) - {actor_id}
    if not readers:
        return 0
    targets = []
    for user in db.scalars(select(User).where(User.id.in_(readers), User.is_active.is_(True))):
        v = viewer(db, user)
        if can_view(user, doc, v.grants, v.groups):
            targets.append(user.id)
    created = notify(
        db,
        targets,
        kind="document.changed",
        title=f"“{doc.title}” was updated",
        body=summary,
        link=f"/library/{doc.id}?changes=1",
        dedupe_key=f"document.changed:{doc.id}:v{doc.version}",
    )
    return len(created)
