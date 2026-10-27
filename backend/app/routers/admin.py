from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..assistant import COMPANY
from ..config import get_settings
from ..database import get_db
from ..deps import admin_user, current_user, optional_user
from ..llm import get_llm
from ..models import (
    AccessRequest,
    AuditEvent,
    Classification,
    Document,
    Position,
    RequestStatus,
    Team,
    User,
)
from ..schemas import AuditOut, MetaOut, TeamOut, UserOut, UserUpdate
from ..seed import DEMO_PASSWORD, DOMAIN
from ..serializers import audit_out

router = APIRouter(prefix="/api", tags=["admin"])

CLASSIFICATION_HELP = {
    Classification.PUBLIC: "Everyone in the company",
    Classification.INTERNAL: "Owning team and teams it's shared with",
    Classification.CONFIDENTIAL: "As internal, and Manager level or above by default",
    Classification.RESTRICTED: "Named people only, via approved requests",
}


@router.get("/meta", response_model=MetaOut)
def meta(viewer: User | None = Depends(optional_user), db: Session = Depends(get_db)):
    """App metadata. Signed-out visitors get only what the public pages need; the team list
    is for signed-in people, and demo accounts are listed only when demo mode is on."""
    llm = get_llm()
    demo = []
    if get_settings().demo_enabled:
        demo_accounts = select(User).where(User.is_active.is_(True), User.email.endswith(f"@{DOMAIN}"))
        for u in db.scalars(demo_accounts.order_by(User.position, User.id)):
            demo.append(
                {
                    "email": u.email,
                    "password": DEMO_PASSWORD,
                    "full_name": u.full_name,
                    "title": u.title,
                    "team": u.team.name,
                    "position": Position(u.position).label,
                    "is_admin": u.is_admin,
                }
            )
    return MetaOut(
        company=COMPANY,
        ai_mode="ai" if llm else "offline",
        ai_model=getattr(llm, "name", None) if llm else None,
        positions=[{"value": p.value, "label": p.label} for p in Position],
        classifications=[{"value": c.value, "label": c.value.title(), "help": CLASSIFICATION_HELP[c]} for c in Classification],
        teams=[TeamOut.model_validate(t) for t in db.scalars(select(Team).order_by(Team.name))] if viewer else [],
        demo_users=demo,
    )


@router.get("/teams", response_model=list[TeamOut])
def teams(_: User = Depends(current_user), db: Session = Depends(get_db)):
    return db.scalars(select(Team).order_by(Team.name)).all()


@router.get("/admin/users", response_model=list[UserOut])
def users(_: User = Depends(admin_user), db: Session = Depends(get_db)):
    return [UserOut.of(u) for u in db.scalars(select(User).order_by(User.team_id, User.position.desc()))]


@router.patch("/admin/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdate, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    changes = body.model_dump(exclude_unset=True)
    if target.id == admin.id and (changes.get("is_admin") is False or changes.get("is_active") is False):
        raise HTTPException(status.HTTP_409_CONFLICT, "You can't remove your own admin access")
    if "team_id" in changes and db.get(Team, changes["team_id"]) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown team")
    before = {k: getattr(target, k) for k in changes}
    for key, value in changes.items():
        setattr(target, key, value)
    audit.record(db, admin, "user.update", "user", target.id, name=target.full_name, before=before, after=changes)
    db.commit()
    db.refresh(target)
    return UserOut.of(target)


@router.get("/admin/audit", response_model=list[AuditOut])
def audit_log(
    action: str | None = None,
    actor_id: int | None = None,
    limit: int = Query(100, ge=1, le=500),
    _: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    stmt = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit)
    if action:
        stmt = stmt.where(AuditEvent.action.startswith(action, autoescape=True))
    if actor_id:
        stmt = stmt.where(AuditEvent.actor_id == actor_id)
    return [audit_out(e) for e in db.scalars(stmt)]


@router.get("/admin/stats")
def stats(_: User = Depends(admin_user), db: Session = Depends(get_db)):
    week_ago = datetime.now(UTC) - timedelta(days=7)

    def count(stmt) -> int:
        return db.scalar(stmt) or 0

    by_class = dict(db.execute(select(Document.classification, func.count()).group_by(Document.classification)).all())
    top_docs = db.execute(
        select(AuditEvent.target_id, func.count().label("n"))
        .where(AuditEvent.action.in_(("document.view", "document.download")), AuditEvent.created_at >= week_ago)
        .group_by(AuditEvent.target_id)
        .order_by(func.count().desc())
        .limit(5)
    ).all()
    titles = {d.id: d.title for d in db.scalars(select(Document).where(Document.id.in_([t for t, _ in top_docs])))}
    flagged = [d for d in db.scalars(select(Document).order_by(Document.updated_at.desc())) if d.sensitivity_flags]
    return {
        "documents": count(select(func.count(Document.id))),
        "users": count(select(func.count(User.id)).where(User.is_active.is_(True))),
        "queries_7d": count(
            select(func.count(AuditEvent.id)).where(AuditEvent.action == "assistant.query", AuditEvent.created_at >= week_ago)
        ),
        "denied_7d": count(
            select(func.count(AuditEvent.id)).where(AuditEvent.action.endswith("_denied"), AuditEvent.created_at >= week_ago)
        ),
        "pending_requests": count(select(func.count(AccessRequest.id)).where(AccessRequest.status == RequestStatus.PENDING)),
        "by_classification": {c.value: by_class.get(c.value, 0) for c in Classification},
        "top_documents": [{"id": t, "title": titles.get(t, f"#{t}"), "views": n} for t, n in top_docs],
        "flagged_documents": [
            {"id": d.id, "title": d.title, "classification": d.classification, "flags": d.sensitivity_flags} for d in flagged
        ][:10],
    }
