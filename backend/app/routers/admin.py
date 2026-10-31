import re
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import sso
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
    Source,
    Team,
    User,
)
from ..schemas import AuditOut, MetaOut, TeamOut
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
        sso_providers=[{"id": p.id, "label": p.label} for p in sso.providers().values()],
        password_login=get_settings().password_login,
    )


@router.get("/teams", response_model=list[TeamOut])
def teams(_: User = Depends(current_user), db: Session = Depends(get_db)):
    return db.scalars(select(Team).order_by(Team.name)).all()


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


@router.get("/admin/feedback")
def answer_feedback(
    rating: str | None = Query("down", pattern="^(up|down)$"),
    days: int = Query(30, ge=1, le=365),
    _: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    """Answers people marked helpful or not, without who marked them."""
    from .. import feedback

    return feedback.review(db, rating=rating, days=days)


@router.get("/admin/operations")
def operations(_: User = Depends(admin_user)):
    """Backups and other operational checks, for the admin overview."""
    from pathlib import Path

    from .. import backup, malware

    s = get_settings()
    return {
        "backups": {
            "enabled": bool(s.backup_dir),
            "interval_hours": s.backup_interval_hours,
            "keep": s.backup_keep,
            "recent": backup.status(Path(s.backup_dir)) if s.backup_dir else [],
        },
        "ai": {
            "max_classification": s.ai_max_classification,
            "daily_limit": s.ai_daily_limit,
            "daily_limit_per_person": s.ai_daily_limit_per_person,
            "used_today": getattr(getattr(get_llm(), "budget", None), "used", None),
        },
        "scanner": {
            "enabled": bool(s.clamav_address),
            "reachable": malware.ping() if s.clamav_address else False,
            "required": s.clamav_required,
        },
    }


def _normal_question(q: str) -> str:
    return " ".join(re.sub(r"[^\w\s'-]", " ", q.lower()).split())


@router.get("/admin/insights")
def insights(days: int = Query(30, ge=1, le=365), _: User = Depends(admin_user), db: Session = Depends(get_db)):
    """What people ask, what goes unanswered, documents that may be out of date, and whether
    sources are syncing. Questions are shown without who asked them."""
    now = datetime.now(UTC)
    since = now - timedelta(days=days)
    asked: dict[str, dict] = {}
    channels: dict[str, int] = {}
    total = unanswered_total = 0
    for e in db.scalars(select(AuditEvent).where(AuditEvent.action == "assistant.query", AuditEvent.created_at >= since)):
        d = e.detail or {}
        question = str(d.get("question", "")).strip()
        if not question:
            continue
        total += 1
        channels[d.get("channel", "web")] = channels.get(d.get("channel", "web"), 0) + 1
        hits = d.get("hits")
        unanswered = (hits == 0) if hits is not None else not (d.get("cited") or d.get("locked"))
        unanswered_total += unanswered
        key = _normal_question(question)
        when = e.created_at if e.created_at.tzinfo else e.created_at.replace(tzinfo=UTC)
        row = asked.setdefault(key, {"question": question, "count": 0, "unanswered": 0, "last_asked": when})
        row["count"] += 1
        row["unanswered"] += unanswered
        row["last_asked"] = max(row["last_asked"], when)
    ranked = sorted(asked.values(), key=lambda r: (-r["count"], r["question"].lower()))

    views = dict(
        db.execute(
            select(AuditEvent.target_id, func.count())
            .where(AuditEvent.action.in_(("document.view", "document.download")), AuditEvent.created_at >= since)
            .group_by(AuditEvent.target_id)
        ).all()
    )
    stale_before = now - timedelta(days=180)
    stale = []
    for doc in db.scalars(select(Document)):
        updated = doc.updated_at if doc.updated_at.tzinfo else doc.updated_at.replace(tzinfo=UTC)
        if updated < stale_before:
            stale.append(
                {
                    "id": doc.id,
                    "title": doc.title,
                    "team": doc.owner_team.name,
                    "updated_at": updated,
                    "views": views.get(doc.id, 0),
                }
            )
    stale.sort(key=lambda r: (-r["views"], r["updated_at"]))

    sources = []
    for src in db.scalars(select(Source).order_by(Source.name)):
        last = src.last_synced_at
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        overdue = src.enabled and (last is None or now - last > timedelta(minutes=3 * max(src.sync_interval_minutes, 5)))
        sources.append(
            {
                "id": src.id,
                "name": src.name,
                "kind": src.kind,
                "label": src.label,
                "status": src.status,
                "enabled": src.enabled,
                "last_synced_at": last,
                "last_error": src.last_error,
                "overdue": bool(overdue),
                "documents": db.scalar(select(func.count(Document.id)).where(Document.source_id == src.id)) or 0,
            }
        )

    return {
        "days": days,
        "questions": total,
        "unanswered": unanswered_total,
        "channels": channels,
        "top_questions": [{k: r[k] for k in ("question", "count", "unanswered", "last_asked")} for r in ranked[:10]],
        "unanswered_questions": [
            {k: r[k] for k in ("question", "count", "last_asked")}
            for r in sorted((r for r in ranked if r["unanswered"]), key=lambda r: (-r["unanswered"], r["question"].lower()))[:10]
        ],
        "stale_documents": stale[:10],
        "sources": sources,
        "ai": {
            "mode": "ai" if get_llm() else "offline",
            "documents_ai_off": db.scalar(select(func.count(Document.id)).where(Document.ai_enabled.is_(False))) or 0,
            "sources_ai_off": db.scalar(select(func.count(Source.id)).where(Source.ai_enabled.is_(False))) or 0,
        },
    }
