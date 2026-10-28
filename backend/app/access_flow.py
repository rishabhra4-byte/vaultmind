"""Asking for and granting access, shared by the Access requests page and the assistant's actions."""

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import audit
from .models import AccessGrant, AccessRequest, AclMode, Document, RequestStatus, User
from .notify import notify
from .policy import can_approve, is_discoverable_to
from .services import viewer


def create_access_request(db: Session, user: User, doc: Document | None, reason: str, duration_days: int) -> AccessRequest:
    """Checks, creates and announces a request. The caller commits."""
    if doc is None or not is_discoverable_to(user, doc):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    if viewer(db, user).can_view(doc):
        raise HTTPException(status.HTTP_409_CONFLICT, "You already have access to this document")
    reason = reason.strip()
    if len(reason) < 10:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Say a little more about why you need it (10 characters or more)"
        )
    if not 1 <= duration_days <= 90:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Access can be requested for 1 to 90 days")
    existing = db.scalar(
        select(AccessRequest).where(
            AccessRequest.document_id == doc.id,
            AccessRequest.requester_id == user.id,
            AccessRequest.status == RequestStatus.PENDING,
        )
    )
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "You already have a pending request for this document")

    req = AccessRequest(document_id=doc.id, requester_id=user.id, reason=reason[:1000], duration_days=duration_days)
    db.add(req)
    try:
        db.flush()  # the unique index on pending requests catches a concurrent duplicate here
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "You already have a pending request for this document") from None
    audit.record(db, user, "access.request", "document", doc.id, title=doc.title, request_id=req.id)
    approvers = [u.id for u in db.scalars(select(User).where(User.is_active.is_(True))) if can_approve(u, doc, user.id)]
    notify(
        db,
        approvers,
        kind="access.requested",
        title=f"{user.full_name} asked for access to “{doc.title}”",
        body=req.reason,
        link="/requests",
        dedupe_key=f"access.requested:{req.id}",
    )
    return req


def grant_access(db: Session, granter: User, doc: Document, person: User, days: int) -> AccessGrant:
    """Give one person time-limited access, as an approver would. The caller commits."""
    if doc.acl_mode == AclMode.MIRROR and not granter.is_admin:
        raise HTTPException(status.HTTP_409_CONFLICT, "This file's sharing is managed in its source")
    if not can_approve(granter, doc, person.id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can't give people access to this document")
    if not person.is_active:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{person.full_name}'s account is switched off")
    if viewer(db, person).can_view(doc):
        raise HTTPException(status.HTTP_409_CONFLICT, f"{person.full_name} can already open this document")
    if not 1 <= days <= 90:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Access can be given for 1 to 90 days")
    grant = AccessGrant(
        document_id=doc.id, user_id=person.id, granted_by=granter.id, expires_at=datetime.now(UTC) + timedelta(days=days)
    )
    db.add(grant)
    db.flush()
    audit.record(db, granter, "access.grant", "document", doc.id, title=doc.title, user_id=person.id, days=days)
    notify(
        db,
        [person.id],
        kind="access.granted",
        title=f"{granter.full_name} gave you access to “{doc.title}” for {days} day{'s' if days != 1 else ''}",
        link=f"/library/{doc.id}",
        dedupe_key=f"access.granted:{grant.id}",
    )
    return grant
