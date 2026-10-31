from typing import TYPE_CHECKING

from . import ai_policy
from .models import AccessGrant, AccessRequest, AuditEvent, Document, Position, User
from .policy import can_manage, can_view
from .schemas import AccessInfo, AccessRequestOut, AuditOut, DocumentOut, GrantOut, SourceRef, UserOut

if TYPE_CHECKING:
    from .services import Viewer


def document_out(doc: Document, viewer: "Viewer", pending: set[int] = frozenset()) -> DocumentOut:
    user = viewer.user
    decision = can_view(user, doc, viewer.grants, viewer.groups)
    source = doc.source
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
        shared_with_groups=sorted(g.group.name for g in doc.group_shares),
        # Summary, tags and scanner flags are all derived from the content, so people who
        # can't open the document don't get them either.
        summary=doc.summary if decision.allowed else "",
        tags=(doc.tags or []) if decision.allowed else [],
        sensitivity_flags=(doc.sensitivity_flags or []) if decision.allowed else [],
        uploaded_by=doc.uploader.full_name if doc.uploader else None,
        uploaded_by_id=doc.uploaded_by,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
        version=doc.version or 1,
        ai_enabled=doc.ai_enabled,
        ai_allowed=ai_policy.may_read(doc),
        ai_blocked_reason=ai_policy.blocked_reason(doc),
        acl_mode=doc.acl_mode,
        source=SourceRef(id=source.id, kind=source.kind, label=source.label, name=source.name) if source else None,
        # Where the file lives is only shown to people who can open it.
        source_path=doc.source_path if decision.allowed else "",
        external_url=doc.external_url if decision.allowed else None,
        access=AccessInfo(
            allowed=decision.allowed,
            code=decision.code,
            reason=decision.reason,
            can_manage=can_manage(user, doc, viewer.grants, viewer.groups),
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
