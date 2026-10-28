"""Access requests and time-boxed grants.

Flow: a user who can see a document exists (discoverable) but can't open it asks for
access with a reason. People with authority over that document (owning-team managers
at or above its level, the uploader, admins) see it in their inbox and approve or deny.
Approval creates a grant that expires automatically.
"""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from .. import audit
from ..access_flow import create_access_request
from ..database import get_db
from ..deps import current_user
from ..models import AccessGrant, AccessRequest, Document, RequestStatus, User
from ..notify import notify
from ..policy import can_approve, grant_is_active
from ..schemas import AccessRequestIn, AccessRequestOut, DecisionIn, GrantOut
from ..serializers import grant_out, request_out

router = APIRouter(prefix="/api", tags=["access"])


def _load_request(db: Session, request_id: int) -> AccessRequest:
    req = db.get(AccessRequest, request_id)
    if req is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Request not found")
    return req


@router.post("/access-requests", response_model=AccessRequestOut, status_code=status.HTTP_201_CREATED)
def create_request(body: AccessRequestIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    req = create_access_request(db, user, db.get(Document, body.document_id), body.reason, body.duration_days)
    db.commit()
    db.refresh(req)
    return request_out(req)


@router.get("/access-requests/mine", response_model=list[AccessRequestOut])
def my_requests(user: User = Depends(current_user), db: Session = Depends(get_db)):
    reqs = db.scalars(
        select(AccessRequest).where(AccessRequest.requester_id == user.id).order_by(AccessRequest.created_at.desc())
    )
    return [request_out(r) for r in reqs]


@router.get("/access-requests/inbox", response_model=list[AccessRequestOut])
def inbox(include_decided: bool = False, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Requests this user has the authority to decide."""
    stmt = select(AccessRequest).options(selectinload(AccessRequest.document)).order_by(AccessRequest.created_at.desc())
    if not include_decided:
        stmt = stmt.where(AccessRequest.status == RequestStatus.PENDING)
    return [request_out(r) for r in db.scalars(stmt) if can_approve(user, r.document, r.requester_id)][:100]


def _decide(db: Session, user: User, request_id: int, approve: bool, body: DecisionIn) -> AccessRequest:
    req = _load_request(db, request_id)
    if not can_approve(user, req.document, req.requester_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You don't have authority over this document")
    if req.status != RequestStatus.PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, f"This request was already {req.status}")

    now = datetime.now(UTC)
    new_status = RequestStatus.APPROVED if approve else RequestStatus.DENIED
    # Compare-and-set: only one decision can win, even if two approvers click at once.
    claimed = db.execute(
        update(AccessRequest)
        .where(AccessRequest.id == req.id, AccessRequest.status == RequestStatus.PENDING)
        .values(status=new_status, decided_by=user.id, decided_at=now, decision_note=body.note.strip())
        .execution_options(synchronize_session=False)
    ).rowcount
    if claimed != 1:
        db.rollback()
        db.refresh(req)
        raise HTTPException(status.HTTP_409_CONFLICT, f"This request was already {req.status}")
    db.refresh(req)
    if approve:
        days = body.duration_days or req.duration_days
        req.duration_days = days
        db.add(
            AccessGrant(
                document_id=req.document_id, user_id=req.requester_id, granted_by=user.id, expires_at=now + timedelta(days=days)
            )
        )
    days_text = f" for {req.duration_days} day{'s' if req.duration_days != 1 else ''}" if approve else ""
    notify(
        db,
        [req.requester_id],
        kind="access.approved" if approve else "access.denied",
        title=f"{user.full_name} {'approved' if approve else 'declined'} your request for “{req.document.title}”{days_text}",
        body=req.decision_note,
        link=f"/library/{req.document_id}" if approve else "/requests",
        dedupe_key=f"access.decided:{req.id}",
    )
    audit.record(
        db,
        user,
        "access.approve" if approve else "access.deny",
        "document",
        req.document_id,
        title=req.document.title,
        request_id=req.id,
        requester=req.requester.full_name,
        duration_days=req.duration_days if approve else None,
    )
    db.commit()
    db.refresh(req)
    return req


@router.post("/access-requests/{request_id}/approve", response_model=AccessRequestOut)
def approve(request_id: int, body: DecisionIn = DecisionIn(), user: User = Depends(current_user), db: Session = Depends(get_db)):
    return request_out(_decide(db, user, request_id, True, body))


@router.post("/access-requests/{request_id}/deny", response_model=AccessRequestOut)
def deny(request_id: int, body: DecisionIn = DecisionIn(), user: User = Depends(current_user), db: Session = Depends(get_db)):
    return request_out(_decide(db, user, request_id, False, body))


@router.post("/access-requests/{request_id}/cancel", response_model=AccessRequestOut)
def cancel(request_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    req = _load_request(db, request_id)
    if req.requester_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Request not found")
    if req.status != RequestStatus.PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, f"This request was already {req.status}")
    req.status = RequestStatus.CANCELLED
    audit.record(db, user, "access.cancel", "document", req.document_id, request_id=req.id)
    db.commit()
    db.refresh(req)
    return request_out(req)


@router.get("/grants", response_model=list[GrantOut])
def grants(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Active grants the user holds, plus ones they can revoke (issued on documents they control)."""
    out = []
    for g in db.scalars(select(AccessGrant).where(AccessGrant.revoked.is_(False)).order_by(AccessGrant.created_at.desc())):
        if not grant_is_active(g.expires_at, g.revoked):
            continue
        doc = db.get(Document, g.document_id)
        if g.user_id == user.id or can_approve(user, doc, g.user_id):
            out.append(grant_out(g, doc, db.get(User, g.user_id), db.get(User, g.granted_by) if g.granted_by else None))
    return out


@router.delete("/grants/{grant_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke(grant_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    g = db.get(AccessGrant, grant_id)
    if g is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Grant not found")
    doc = db.get(Document, g.document_id)
    if not (g.user_id == user.id or can_approve(user, doc, g.user_id)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can't revoke this grant")
    g.revoked = True
    audit.record(db, user, "access.revoke", "document", g.document_id, title=doc.title, grant_id=g.id, user_id=g.user_id)
    db.commit()
