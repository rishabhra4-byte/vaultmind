from .models import AccessGrant, AccessRequest, AuditEvent, Document, Position, User
from .policy import can_manage, can_view
from .schemas import AccessInfo, AccessRequestOut, AuditOut, DocumentOut, GrantOut, UserOut


def document_out(doc: Document, user: User, grants: set[int], pending: set[int] = frozenset()) -> DocumentOut:
    decision = can_view(user, doc, grants)
    return DocumentOut(
        id=doc.id,
        title=doc.title,
        filename=doc.filename,
        mime_type=doc.mime_type,
        size=doc.size,
        owner_team_id=doc.owner_team_id,
        owner_team=doc.owner_team.name,
        classification=doc.classification,
        min_position=doc.min_position,
        min_position_label=Position(doc.min_position).label,
        discoverable=doc.discoverable,
        shared_with=sorted(s.team.name for s in doc.shares),
        summary=doc.summary if decision.allowed else "",  # don't leak content through the summary
        tags=doc.tags or [],
        sensitivity_flags=doc.sensitivity_flags or [],
        uploaded_by=doc.uploader.full_name if doc.uploader else None,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
        access=AccessInfo(
            allowed=decision.allowed,
            code=decision.code,
            reason=decision.reason,
            can_manage=can_manage(user, doc, grants),
            request_pending=doc.id in pending,
        ),
    )


def request_out(r: AccessRequest) -> AccessRequestOut:
    return AccessRequestOut(
        id=r.id,
        document_id=r.document_id,
        document_title=r.document.title,
        document_team=r.document.owner_team.name,
        classification=r.document.classification,
        requester=UserOut.of(r.requester),
        reason=r.reason,
        duration_days=r.duration_days,
        status=r.status,
        decided_by=r.decider.full_name if r.decider else None,
        decision_note=r.decision_note,
        created_at=r.created_at,
        decided_at=r.decided_at,
    )


def grant_out(g: AccessGrant, doc: Document, user: User, granter: User | None) -> GrantOut:
    return GrantOut(
        id=g.id,
        document_id=g.document_id,
        document_title=doc.title,
        user_id=g.user_id,
        user_name=user.full_name,
        granted_by=granter.full_name if granter else None,
        expires_at=g.expires_at,
        created_at=g.created_at,
    )


def audit_out(e: AuditEvent) -> AuditOut:
    return AuditOut(
        id=e.id,
        actor=e.actor.full_name if e.actor else None,
        actor_id=e.actor_id,
        action=e.action,
        target_type=e.target_type,
        target_id=e.target_id,
        detail=e.detail or {},
        created_at=e.created_at,
    )
