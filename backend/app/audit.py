from sqlalchemy.orm import Session

from .models import AuditEvent, User


def record(
    db: Session,
    actor: User | None,
    action: str,
    target_type: str = "",
    target_id: int | None = None,
    **detail,
) -> AuditEvent:
    """Append an audit event. The caller's transaction commits it together with the action itself."""
    event = AuditEvent(
        actor_id=actor.id if actor else None,
        action=action,
        target_type=target_type,
        target_id=target_id,
        detail=detail,
    )
    db.add(event)
    return event
