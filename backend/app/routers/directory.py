"""People, teams and groups: kept by admins here, or synced from Google / Microsoft directories.

People and groups that come from a directory sync are marked as such; edits to them here are
overwritten at the next sync (the page says so), and synced groups can't be edited at all.
"""

import csv
import io
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit, sessions
from ..database import get_db
from ..deps import admin_user, auditor_user, current_user
from ..models import AuditEvent, Document, Group, GroupMember, Position, Source, Team, User, UserSession
from ..schemas import SessionOut, TeamOut, UserOut, UTCDatetime
from ..security import hash_password

router = APIRouter(prefix="/api", tags=["directory"])

ONBOARDING_DAYS = 30
MAX_IMPORT_BYTES = 1024 * 1024
MAX_IMPORT_ROWS = 5000
NO_PASSWORD = "!"  # can't sign in with a password; uses Google / Microsoft


# ---------------------------------------------------------------- people


class AdminUserOut(UserOut):
    auth_provider: str
    directory_managed: bool
    has_password: bool
    onboarding: bool
    notify_email: bool
    groups: list[str]
    created_at: UTCDatetime
    open_sessions: int = 0
    last_sign_in_at: UTCDatetime | None = None


def _activity(db: Session, user_ids: list[int] | None = None) -> tuple[dict[int, int], dict[int, datetime]]:
    """Open sessions and last sign-in per person."""
    open_by_user: dict[int, int] = {}
    stmt = select(UserSession.user_id, UserSession.expires_at, UserSession.last_seen_at).where(UserSession.ended_at.is_(None))
    if user_ids is not None:
        stmt = stmt.where(UserSession.user_id.in_(user_ids))
    now = datetime.now(UTC)
    for uid, expires, _seen in db.execute(stmt):
        if sessions.aware(expires) > now:
            open_by_user[uid] = open_by_user.get(uid, 0) + 1
    last_stmt = select(AuditEvent.actor_id, func.max(AuditEvent.created_at)).where(AuditEvent.action == "auth.login")
    if user_ids is not None:
        last_stmt = last_stmt.where(AuditEvent.actor_id.in_(user_ids))
    last = {uid: when for uid, when in db.execute(last_stmt.group_by(AuditEvent.actor_id)) if uid is not None}
    return open_by_user, last


def _admin_out(u: User, open_sessions: int = 0, last_sign_in: datetime | None = None) -> AdminUserOut:
    until = u.onboarding_until
    if until is not None and until.tzinfo is None:
        until = until.replace(tzinfo=UTC)
    return AdminUserOut(
        **UserOut.of(u).model_dump(),
        auth_provider=u.auth_provider,
        directory_managed=u.directory_managed,
        has_password=u.password_hash != NO_PASSWORD,
        onboarding=until is not None and until > datetime.now(UTC),
        notify_email=u.notify_email,
        groups=sorted(m.group.name for m in u.memberships),
        created_at=u.created_at,
        open_sessions=open_sessions,
        last_sign_in_at=last_sign_in,
    )


def _one_out(db: Session, u: User) -> AdminUserOut:
    open_by_user, last = _activity(db, [u.id])
    return _admin_out(u, open_by_user.get(u.id, 0), last.get(u.id))


class UserIn(BaseModel):
    email: str = Field(min_length=3, max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    full_name: str = Field(min_length=1, max_length=120)
    title: str = Field("", max_length=120)
    team_id: int
    position: int = Field(Position.ASSOCIATE, ge=Position.INTERN, le=Position.EXECUTIVE)
    is_admin: bool = False
    is_auditor: bool = False
    password: str | None = Field(None, min_length=8, max_length=256)  # leave out for company sign-in only
    onboarding: bool = True


class UserUpdate(BaseModel):
    full_name: str | None = Field(None, min_length=1, max_length=120)
    team_id: int | None = None
    position: int | None = Field(None, ge=Position.INTERN, le=Position.EXECUTIVE)
    title: str | None = Field(None, max_length=120)
    is_active: bool | None = None
    is_admin: bool | None = None
    is_auditor: bool | None = None
    onboarding: bool | None = None
    password: str | None = Field(None, min_length=8, max_length=256)


@router.get("/admin/users", response_model=list[AdminUserOut])
def users(_: User = Depends(auditor_user), db: Session = Depends(get_db)):
    open_by_user, last = _activity(db)
    return [
        _admin_out(u, open_by_user.get(u.id, 0), last.get(u.id))
        for u in db.scalars(select(User).order_by(User.team_id, User.position.desc(), User.full_name))
    ]


@router.post("/admin/users", response_model=AdminUserOut, status_code=status.HTTP_201_CREATED)
def create_user(body: UserIn, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    if db.scalar(select(User.id).where(func.lower(User.email) == email)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Someone with that email already has an account")
    if db.get(Team, body.team_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown team")
    user = User(
        email=email,
        full_name=body.full_name.strip(),
        title=body.title.strip(),
        team_id=body.team_id,
        position=body.position,
        is_admin=body.is_admin,
        is_auditor=body.is_auditor,
        password_hash=hash_password(body.password) if body.password else NO_PASSWORD,
        auth_provider="password" if body.password else "sso",
        onboarding_until=datetime.now(UTC) + timedelta(days=ONBOARDING_DAYS) if body.onboarding else None,
    )
    db.add(user)
    db.flush()
    audit.record(
        db,
        admin,
        "user.create",
        "user",
        user.id,
        name=user.full_name,
        email=email,
        team=db.get(Team, body.team_id).name,
        position=Position(body.position).label,
        is_admin=body.is_admin,
        is_auditor=body.is_auditor,
    )
    db.commit()
    db.refresh(user)
    return _one_out(db, user)


@router.patch("/admin/users/{user_id}", response_model=AdminUserOut)
def update_user(user_id: int, body: UserUpdate, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    changes = body.model_dump(exclude_unset=True)
    if target.id == admin.id and (changes.get("is_admin") is False or changes.get("is_active") is False):
        raise HTTPException(status.HTTP_409_CONFLICT, "You can't remove your own admin access")
    if "team_id" in changes and db.get(Team, changes["team_id"]) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown team")
    logged = {k: v for k, v in changes.items() if k != "password"}
    before = {k: getattr(target, k) for k in logged if hasattr(target, k) and k != "onboarding"}
    end_reason = None
    for key, value in changes.items():
        if key == "onboarding":
            target.onboarding_until = datetime.now(UTC) + timedelta(days=ONBOARDING_DAYS) if value else None
        elif key == "password":
            if value:
                target.password_hash = hash_password(value)
                logged["password"] = "reset"
                end_reason = end_reason or "password_changed"
        elif key == "full_name":
            target.full_name = value.strip()
        else:
            if key == "is_active" and value is False and target.is_active:
                end_reason = "deactivated"
            elif key in ("is_admin", "is_auditor") and value is False and getattr(target, key):
                end_reason = end_reason or "role_changed"
            setattr(target, key, value)
    audit.record(db, admin, "user.update", "user", target.id, name=target.full_name, before=before, after=logged)
    if end_reason:
        # A reset password, a deactivation or a removed role ends every session at once.
        sessions.end_all(db, target.id, end_reason, actor=admin)
    db.commit()
    db.refresh(target)
    return _one_out(db, target)


@router.get("/admin/users/{user_id}/sessions", response_model=list[SessionOut])
def user_sessions(user_id: int, _: User = Depends(auditor_user), db: Session = Depends(get_db)):
    if db.get(User, user_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return [SessionOut.of(s) for s in sessions.open_sessions(db, user_id)]


@router.post("/admin/users/{user_id}/sessions/end")
def end_user_sessions(user_id: int, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    """Sign someone out everywhere (for example a lost laptop)."""
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    ended = sessions.end_all(db, target.id, "ended_by_admin", actor=admin)
    db.commit()
    return {"ended": ended}


class ImportRow(BaseModel):
    row: int
    email: str
    action: str  # create | update | unchanged | error
    detail: str = ""


class ImportOut(BaseModel):
    dry_run: bool
    created: int
    updated: int
    unchanged: int
    errors: int
    new_teams: list[str]
    rows: list[ImportRow]


def _position(value: str) -> int | None:
    value = value.strip()
    if not value:
        return None
    if value.isdigit() and Position.INTERN <= int(value) <= Position.EXECUTIVE:
        return int(value)
    for p in Position:
        if p.label.lower() == value.lower():
            return int(p)
    raise ValueError(f"unknown position “{value}” (use Intern … Executive, or 1-6)")


def _yes(value: str) -> bool | None:
    v = value.strip().lower()
    if not v:
        return None
    if v in ("yes", "y", "true", "1"):
        return True
    if v in ("no", "n", "false", "0"):
        return False
    raise ValueError(f"“{value}” isn't yes or no")


@router.post("/admin/users/import", response_model=ImportOut)
async def import_users(
    file: UploadFile = File(...),
    dry_run: bool = Form(True),
    create_teams: bool = Form(False),
    admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    """Add or update people from a CSV with the columns email, name, title, team, position, admin.

    Run it with dry_run first to see what would change; nothing is saved until dry_run is false.
    """
    data = await file.read(MAX_IMPORT_BYTES + 1)
    if len(data) > MAX_IMPORT_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "The file is limited to 1 MB")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Save the file as UTF-8 CSV") from None
    reader = csv.DictReader(io.StringIO(text))
    columns = {(c or "").strip().lower() for c in reader.fieldnames or []}
    if "email" not in columns:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The first row needs column names, including “email”")

    teams = {t.name.lower(): t for t in db.scalars(select(Team))}
    people = {u.email.lower(): u for u in db.scalars(select(User))}
    new_teams: list[str] = []
    rows: list[ImportRow] = []
    seen: set[str] = set()
    changed: list[dict] = []  # for the audit log: who got which new team, position or role
    for n, raw in enumerate(reader, start=2):
        if n - 1 > MAX_IMPORT_ROWS:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"Up to {MAX_IMPORT_ROWS} people per file")
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        email = row.get("email", "").lower()
        if not email and not any(row.values()):
            continue  # blank line
        try:
            if "@" not in email or " " in email:
                raise ValueError("not an email address")
            if email in seen:
                raise ValueError("appears twice in the file")
            seen.add(email)
            name = row.get("name") or row.get("full_name") or ""
            position = _position(row.get("position", ""))
            admin_flag = _yes(row.get("admin", ""))
            team_name = row.get("team", "")
            team = teams.get(team_name.lower()) if team_name else None
            if team_name and team is None:
                if not create_teams:
                    raise ValueError(f"no team called “{team_name}” (tick “create missing teams”)")
                team = Team(name=team_name[:80], description="")
                teams[team_name.lower()] = team
                new_teams.append(team.name)
                if not dry_run:
                    db.add(team)
                    db.flush()
            existing = people.get(email)
            if existing is None:
                if not name:
                    raise ValueError("new people need a name")
                if team is None:
                    raise ValueError("new people need a team")
                rows.append(ImportRow(row=n, email=email, action="create", detail=f"{name}, {team.name}"))
                if not dry_run:
                    user = User(
                        email=email,
                        full_name=name[:120],
                        title=row.get("title", "")[:120],
                        team_id=team.id,
                        position=position or int(Position.ASSOCIATE),
                        is_admin=bool(admin_flag),
                        password_hash=NO_PASSWORD,
                        auth_provider="sso",
                        onboarding_until=datetime.now(UTC) + timedelta(days=ONBOARDING_DAYS),
                    )
                    db.add(user)
                    people[email] = user
                continue
            changes: dict = {}
            if name and name != existing.full_name:
                changes["full_name"] = name[:120]
            if "title" in row and row["title"] != existing.title:
                changes["title"] = row["title"][:120]
            if team is not None and team.id != existing.team_id:
                changes["team_id"] = team.id
            if position is not None and position != existing.position:
                changes["position"] = position
            if admin_flag is not None and admin_flag != existing.is_admin:
                if existing.id == admin.id and not admin_flag:
                    raise ValueError("you can't remove your own admin access")
                changes["is_admin"] = admin_flag
            if not changes:
                rows.append(ImportRow(row=n, email=email, action="unchanged"))
                continue
            rows.append(ImportRow(row=n, email=email, action="update", detail=", ".join(sorted(changes))))
            if not dry_run:
                changed.append({"email": email, **{k: v for k, v in changes.items() if k != "full_name"}})
                for k, v in changes.items():
                    setattr(existing, k, v)
                if changes.get("is_admin") is False:
                    sessions.end_all(db, existing.id, "role_changed", actor=admin)
        except ValueError as exc:
            rows.append(ImportRow(row=n, email=email or "(none)", action="error", detail=str(exc)))

    count = {a: sum(r.action == a for r in rows) for a in ("create", "update", "unchanged", "error")}
    if not dry_run:
        if count["error"]:
            db.rollback()
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Fix the rows with errors first (run a preview)")
        audit.record(
            db,
            admin,
            "user.import",
            "user",
            None,
            created=count["create"],
            updated=count["update"],
            teams=new_teams,
            new_people=[r.email for r in rows if r.action == "create"][:200],
            changes=changed[:200],
        )
        db.commit()
    return ImportOut(
        dry_run=dry_run,
        created=count["create"],
        updated=count["update"],
        unchanged=count["unchanged"],
        errors=count["error"],
        new_teams=new_teams,
        rows=rows,
    )


# ---------------------------------------------------------------- teams


class AdminTeamOut(TeamOut):
    members: int
    documents: int


class TeamIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field("", max_length=255)


class TeamUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=80)
    description: str | None = Field(None, max_length=255)


def _team_out(db: Session, t: Team) -> AdminTeamOut:
    return AdminTeamOut(
        id=t.id,
        name=t.name,
        description=t.description,
        members=db.scalar(select(func.count(User.id)).where(User.team_id == t.id, User.is_active.is_(True))) or 0,
        documents=db.scalar(select(func.count(Document.id)).where(Document.owner_team_id == t.id)) or 0,
    )


def _unique_team(db: Session, name: str, exclude: int | None = None) -> str:
    name = " ".join(name.split())
    stmt = select(Team.id).where(func.lower(Team.name) == name.lower())
    if exclude is not None:
        stmt = stmt.where(Team.id != exclude)
    if db.scalar(stmt) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"There's already a team called “{name}”")
    return name


@router.get("/admin/teams", response_model=list[AdminTeamOut])
def admin_teams(_: User = Depends(admin_user), db: Session = Depends(get_db)):
    return [_team_out(db, t) for t in db.scalars(select(Team).order_by(Team.name))]


@router.post("/admin/teams", response_model=AdminTeamOut, status_code=status.HTTP_201_CREATED)
def create_team(body: TeamIn, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    team = Team(name=_unique_team(db, body.name), description=body.description.strip())
    db.add(team)
    db.flush()
    audit.record(db, admin, "team.create", "team", team.id, name=team.name)
    db.commit()
    return _team_out(db, team)


@router.patch("/admin/teams/{team_id}", response_model=AdminTeamOut)
def update_team(team_id: int, body: TeamUpdate, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    team = db.get(Team, team_id)
    if team is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Team not found")
    before = {"name": team.name, "description": team.description}
    if body.name is not None:
        team.name = _unique_team(db, body.name, exclude=team.id)
    if body.description is not None:
        team.description = body.description.strip()
    audit.record(db, admin, "team.update", "team", team.id, before=before, after={"name": team.name})
    db.commit()
    return _team_out(db, team)


@router.delete("/admin/teams/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_team(team_id: int, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    team = db.get(Team, team_id)
    if team is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Team not found")
    in_use = (
        db.scalar(select(func.count(User.id)).where(User.team_id == team.id))
        or db.scalar(select(func.count(Document.id)).where(Document.owner_team_id == team.id))
        or db.scalar(select(func.count(Source.id)).where(Source.owner_team_id == team.id))
    )
    if in_use:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Move this team's people, documents and sources to another team before deleting it"
        )
    audit.record(db, admin, "team.delete", "team", team.id, name=team.name)
    db.delete(team)
    db.commit()


# ---------------------------------------------------------------- groups


class GroupOut(BaseModel):
    id: int
    name: str
    description: str
    provider: str  # manual | google | microsoft
    members: int


class GroupDetail(GroupOut):
    member_ids: list[int]


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field("", max_length=255)
    member_ids: list[int] = Field(default_factory=list, max_length=5000)


class GroupUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    description: str | None = Field(None, max_length=255)
    member_ids: list[int] | None = Field(None, max_length=5000)


def _group_out(g: Group, detail: bool = False) -> GroupOut | GroupDetail:
    base = {"id": g.id, "name": g.name, "description": g.description, "provider": g.provider, "members": len(g.members)}
    return GroupDetail(**base, member_ids=sorted(m.user_id for m in g.members)) if detail else GroupOut(**base)


@router.get("/groups", response_model=list[GroupOut])
def groups(_: User = Depends(current_user), db: Session = Depends(get_db)):
    """Group names, for choosing who to share a document with."""
    return [_group_out(g) for g in db.scalars(select(Group).order_by(Group.name))]


@router.get("/admin/groups", response_model=list[GroupDetail])
def admin_groups(_: User = Depends(admin_user), db: Session = Depends(get_db)):
    return [_group_out(g, detail=True) for g in db.scalars(select(Group).order_by(Group.name))]


def _set_members(db: Session, group: Group, member_ids: list[int]) -> None:
    wanted = set(member_ids)
    found = set(db.scalars(select(User.id).where(User.id.in_(wanted)))) if wanted else set()
    if wanted - found:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown person in the member list")
    group.members = [m for m in group.members if m.user_id in wanted]
    have = {m.user_id for m in group.members}
    group.members += [GroupMember(user_id=uid) for uid in sorted(wanted - have)]


def _manual(group: Group | None) -> Group:
    if group is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Group not found")
    if group.provider != "manual":
        raise HTTPException(status.HTTP_409_CONFLICT, f"This group is synced from {group.provider.title()}; change it there")
    return group


@router.post("/admin/groups", response_model=GroupDetail, status_code=status.HTTP_201_CREATED)
def create_group(body: GroupIn, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    name = " ".join(body.name.split())
    if db.scalar(select(Group.id).where(func.lower(Group.name) == name.lower(), Group.provider == "manual")) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"There's already a group called “{name}”")
    group = Group(name=name, description=body.description.strip(), provider="manual")
    db.add(group)
    _set_members(db, group, body.member_ids)
    db.flush()
    audit.record(db, admin, "group.create", "group", group.id, name=name, members=len(group.members))
    db.commit()
    db.refresh(group)
    return _group_out(group, detail=True)


@router.patch("/admin/groups/{group_id}", response_model=GroupDetail)
def update_group(group_id: int, body: GroupUpdate, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    group = _manual(db.get(Group, group_id))
    if body.name is not None:
        group.name = " ".join(body.name.split())
    if body.description is not None:
        group.description = body.description.strip()
    if body.member_ids is not None:
        _set_members(db, group, body.member_ids)
    audit.record(db, admin, "group.update", "group", group.id, name=group.name, members=len(group.members))
    db.commit()
    db.refresh(group)
    return _group_out(group, detail=True)


@router.delete("/admin/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_group(group_id: int, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    group = _manual(db.get(Group, group_id))
    audit.record(db, admin, "group.delete", "group", group.id, name=group.name)
    db.delete(group)  # its document shares go with it
    db.commit()
