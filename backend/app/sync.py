"""The sync engine: bring a connected source's files (and optionally its directory) into VaultMind.

For every file in the source:
- new: download, extract text, scan, store, index;
- changed (its change marker moved): store a new version, tell recent readers what changed;
- unchanged: only refresh its sharing and labels, no download;
- gone from the source: removed here too.

Access is either mirrored (each file's sharing in Drive / Microsoft 365, copied every sync)
or rules-based (team and classification per folder). In rules mode the sensitivity scanner can
only raise a file's classification, never lower it.

Each file is committed on its own, so one bad file can't undo the rest, and a failure while
listing the source stops the sync before anything is removed.
"""

import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import PurePath

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import audit
from .config import get_settings
from .connectors import ConnectorError, DirectoryGroup, DirectoryUser, Grant, build_connector
from .database import SessionLocal
from .extract import UnsupportedFile, extract_text
from .files import media_type_for
from .models import (
    DEFAULT_MIN_POSITION,
    AclMode,
    Classification,
    Document,
    Group,
    GroupMember,
    Position,
    Source,
    SourceKind,
    Team,
    User,
    UserIdentity,
    utcnow,
)
from .notify import notify
from .sensitivity import floor_for_flags, rank, scan
from .services import ingest_document, remove_document, replace_content, safe_external_url, set_principals
from .text import extractive_summary

log = logging.getLogger(__name__)

MAX_ERRORS = 20
_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()


@dataclass
class SyncReport:
    files: int = 0
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0
    skipped: int = 0
    raised: int = 0  # rules mode: classification raised because the scanner found sensitive data
    sharing_changed: int = 0
    unresolved: int = 0  # sharing entries that matched nobody in VaultMind
    directory: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def problem(self, message: str) -> None:
        if len(self.errors) < MAX_ERRORS:
            self.errors.append(message)

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


# --------------------------------------------------------------------------- positions and teams


# "Manager" titles that are usually individual-contributor roles, not people managers.
_IC_MANAGER = re.compile(
    r"\b(product|project|program|programme|account|key account|customer success|community|partner|social media)\s+manager\b"
)


def position_from_title(title: str) -> Position:
    """A starting position from a job title; admins can change it afterwards.

    When in doubt it errs low: a position too low can be raised by an admin, while one too
    high would quietly open confidential documents.
    """
    t = title.lower()
    if re.search(r"\b(ceo|cto|cfo|coo|cpo|chief|vice president|vp|president|founder)\b", t):
        return Position.EXECUTIVE
    if re.search(r"\b(director|head of)\b", t):
        return Position.DIRECTOR
    if re.search(r"\bmanager\b", t) and not _IC_MANAGER.search(t):
        return Position.MANAGER
    if re.search(r"\b(senior|sr|lead|principal|staff)\b", t):
        return Position.SENIOR
    if re.search(r"\b(intern|trainee|apprentice)\b", t):
        return Position.INTERN
    return Position.ASSOCIATE


class Rules:
    """Rules mode: which team owns files in a folder, and how they're classified."""

    def __init__(self, db: Session, source: Source) -> None:
        teams = {t.name.lower(): t.id for t in db.scalars(select(Team))}
        ids = set(teams.values())
        self.default = (source.owner_team_id, Classification(source.classification), None)
        self.rules: list[tuple[str, int, Classification, int | None]] = []
        for r in (source.config or {}).get("rules", []):
            prefix = str(r.get("prefix", "")).strip().strip("/").lower()
            team = r.get("team", source.owner_team_id)
            team_id = team if isinstance(team, int) and team in ids else teams.get(str(team).lower())
            if team_id is None:
                raise ConnectorError(f"The rule for “{prefix or 'everything'}” names a team that doesn't exist: {team}")
            cls = Classification(r.get("classification", source.classification))
            min_pos = r.get("min_position")
            self.rules.append((prefix, team_id, cls, int(min_pos) if min_pos else None))
        self.rules.sort(key=lambda r: len(r[0]), reverse=True)

    def for_path(self, path: str) -> tuple[int, Classification, int | None]:
        p = path.strip("/").lower()
        for prefix, team_id, cls, min_pos in self.rules:
            if not prefix or p == prefix or p.startswith(prefix + "/"):
                return team_id, cls, min_pos
        return self.default


class PrincipalMapper:
    """Turns a file's sharing in the source into VaultMind principals."""

    def __init__(self, db: Session, provider: str) -> None:
        self.db, self.provider = db, provider
        self.emails_by_subject = dict(
            db.execute(
                select(UserIdentity.subject, User.email)
                .join(User, User.id == UserIdentity.user_id)
                .where(UserIdentity.provider == provider)
            ).all()
        )
        self.groups = {g.external_id: g.id for g in db.scalars(select(Group).where(Group.provider == provider))}

    def group_id(self, external_id: str, name: str) -> int:
        if external_id not in self.groups:
            group = Group(name=(name or external_id)[:120], provider=self.provider, external_id=external_id)
            self.db.add(group)
            self.db.flush()
            self.groups[external_id] = group.id
        return self.groups[external_id]

    def map(self, grants: list[Grant]) -> tuple[set[tuple[str, str]], int]:
        out: set[tuple[str, str]] = set()
        unresolved = 0
        for g in grants:
            if g.kind == "user":
                email = g.value or self.emails_by_subject.get(g.subject, "")
                if email:
                    out.add(("user", email.lower()))
                else:
                    unresolved += 1
            elif g.kind == "group" and g.value:
                out.add(("group", str(self.group_id(g.value, g.name))))
            elif g.kind == "domain" and g.value:
                out.add(("domain", g.value.lower()))
            elif g.kind == "company":
                out.add(("company", "*"))
            else:
                unresolved += 1
        return out, unresolved


# --------------------------------------------------------------------------- directory


def sync_directory(db: Session, provider: str, source: Source, users: list[DirectoryUser], groups: list[DirectoryGroup]) -> dict:
    """Create and update people and groups from the company directory.

    New people get a starting position from their job title and a team from their department.
    People who leave (or are suspended) are deactivated, except administrators, so a broken
    directory sync can never lock everyone out.
    """
    stats = {"created": 0, "updated": 0, "deactivated": 0, "groups": 0}
    now = utcnow()
    teams = {t.name.lower(): t for t in db.scalars(select(Team))}
    people = {u.email.lower(): u for u in db.scalars(select(User))}
    by_subject = {i.subject: i.user_id for i in db.scalars(select(UserIdentity).where(UserIdentity.provider == provider))}
    first_sync = source.last_synced_at is None
    seen: set[int] = set()
    user_by_external: dict[str, int] = {}

    def team_for(department: str) -> Team:
        name = department.strip()[:80]
        if not name:
            return source.owner_team
        if name.lower() not in teams:
            team = Team(name=name, description="From the company directory")
            db.add(team)
            db.flush()
            teams[name.lower()] = team
        return teams[name.lower()]

    for du in users:
        user = db.get(User, by_subject[du.external_id]) if du.external_id in by_subject else people.get(du.email)
        if user is None:
            if not du.active:
                continue
            user = User(
                email=du.email,
                full_name=du.name[:120],
                title=du.title[:120],
                team_id=team_for(du.department).id,
                position=int(position_from_title(du.title)),
                password_hash="!",  # no password: they sign in with their company account
                auth_provider=provider,
                directory_managed=True,
                # Everyone in the first import isn't new; people who appear later are.
                onboarding_until=None if first_sync else now + timedelta(days=30),
            )
            db.add(user)
            db.flush()
            people[user.email] = user
            stats["created"] += 1
        elif user.directory_managed:
            changes = {"full_name": du.name[:120], "title": du.title[:120]}
            if du.department.strip():
                changes["team_id"] = team_for(du.department).id
            if not du.active and not user.is_admin:
                changes["is_active"] = False
            elif du.active:
                changes["is_active"] = True
            if any(getattr(user, k) != v for k, v in changes.items()):
                if changes.get("is_active") is False and user.is_active:
                    stats["deactivated"] += 1
                for k, v in changes.items():
                    setattr(user, k, v)
                stats["updated"] += 1
        if du.external_id not in by_subject:
            db.add(UserIdentity(user_id=user.id, provider=provider, subject=du.external_id))
            by_subject[du.external_id] = user.id
        user_by_external[du.external_id] = user.id
        seen.add(user.id)

    # People this directory used to manage who aren't in it any more.
    for user in db.scalars(
        select(User).where(User.directory_managed.is_(True), User.auth_provider == provider, User.is_active.is_(True))
    ):
        if user.id not in seen and not user.is_admin:
            user.is_active = False
            stats["deactivated"] += 1

    by_email = {u.email.lower(): u.id for u in people.values()}
    for dg in groups:
        group = db.scalar(select(Group).where(Group.provider == provider, Group.external_id == dg.external_id))
        if group is None:
            group = Group(name=dg.name[:120], provider=provider, external_id=dg.external_id)
            db.add(group)
            db.flush()
        group.name = dg.name[:120]
        wanted = {user_by_external[m] for m in dg.member_ids if m in user_by_external}
        wanted |= {by_email[e] for e in dg.member_emails if e in by_email}
        have = {m.user_id: m for m in group.members}
        group.members = [m for uid, m in have.items() if uid in wanted] + [
            GroupMember(user_id=uid) for uid in sorted(wanted - set(have))
        ]
        stats["groups"] += 1
    db.flush()
    return stats


# --------------------------------------------------------------------------- files


def _title(name: str) -> str:
    stem = PurePath(name).stem if PurePath(name).suffix.lower() in {".txt", ".md", ".pdf", ".docx", ".csv", ".json"} else name
    return (stem or name).strip()[:255]


def sync_source(db: Session, source: Source) -> SyncReport:
    report = SyncReport()
    connector = build_connector(source)
    provider = connector.provider
    mirror = source.permission_mode == "mirror"
    if mirror and provider == "folder":
        raise ConnectorError("Shared folders can't mirror sharing; use folder rules instead")

    if source.sync_directory and provider != "folder":
        users, groups = connector.directory()
        report.directory = sync_directory(db, provider, source, users, groups)
        db.commit()

    rules = Rules(db, source)
    mapper = PrincipalMapper(db, provider) if mirror else None
    max_bytes = get_settings().max_upload_mb * 1024 * 1024
    existing = {d.external_id: d.id for d in db.scalars(select(Document).where(Document.source_id == source.id))}
    seen: set[str] = set()

    for f in connector.list_files():  # errors while listing stop the sync before anything is removed
        report.files += 1
        seen.add(f.external_id)
        doc = db.get(Document, existing[f.external_id]) if f.external_id in existing else None
        renamed = doc is not None and _title(doc.filename) != _title(f.name)
        try:
            team_id, classification, min_pos = rules.for_path(f.path)
            principals = None
            if mapper is not None:
                grants = connector.grants(f) or []
                principals, unresolved = mapper.map(grants)
                report.unresolved += unresolved

            if doc is not None and doc.source_version == f.version_key:
                _refresh_labels(doc, f, team_id, classification, min_pos, mirror, renamed)
                if principals is not None and set_principals(doc, principals):
                    report.sharing_changed += 1
                report.unchanged += 1
                db.commit()
                continue

            if f.size and f.size > max_bytes:
                raise ConnectorError(f"larger than {get_settings().max_upload_mb} MB")
            data, filename = connector.fetch(f)
            if len(data) > max_bytes:
                raise ConnectorError(f"larger than {get_settings().max_upload_mb} MB")
            text = extract_text(filename, data)
            if not text.strip():
                raise ConnectorError("no readable text")
            if not mirror:
                suggested = scan(text).suggested
                if rank(suggested) > rank(classification):
                    classification, min_pos = suggested, None
                    report.raised += 1

            if doc is None:
                doc = ingest_document(
                    db,
                    uploader=None,
                    filename=filename,
                    data=data,
                    mime_type=media_type_for(filename),
                    title=_title(f.name),
                    owner_team_id=team_id,
                    classification=classification,
                    min_position=min_pos,
                    text=text,
                    summary=extractive_summary(text),  # fast; AI summaries for a whole drive would be slow
                    source_id=source.id,
                    external_id=f.external_id,
                    external_url=f.url,
                    source_path=f.path,
                    acl_mode=AclMode.MIRROR if mirror else AclMode.NATIVE,
                    principals=sorted(principals or []),
                    ai_enabled=source.ai_enabled,
                ).document
                report.added += 1
            else:
                if not renamed:
                    filename = doc.filename if PurePath(doc.filename).suffix == PurePath(filename).suffix else filename
                replace_content(db, doc, data=data, filename=filename, actor=None, text=text, ai_summary=False)
                _refresh_labels(doc, f, team_id, classification, min_pos, mirror, renamed)
                if principals is not None:
                    set_principals(doc, principals)
                report.updated += 1
            doc.source_version = f.version_key
            db.commit()
        except (ConnectorError, UnsupportedFile, OSError) as exc:
            db.rollback()
            report.skipped += 1
            report.problem(f"{f.path + '/' if f.path else ''}{f.name}: {exc}")
        except Exception:
            db.rollback()
            log.exception("Sync of %s failed for %s", source.name, f.name)
            report.skipped += 1
            report.problem(f"{f.name}: unexpected error (see the server log)")

    for external_id, doc_id in existing.items():
        if external_id not in seen:
            doc = db.get(Document, doc_id)
            if doc is not None:
                remove_document(db, doc)
                report.removed += 1
    db.commit()
    return report


def _refresh_labels(
    doc: Document, f, team_id: int, classification: Classification, min_pos: int | None, mirror: bool, renamed: bool
) -> None:
    if renamed:  # renamed in the source; otherwise keep any title an admin set here
        doc.title = _title(f.name)
    doc.source_path = f.path[:1000]
    doc.external_url = safe_external_url(f.url)
    if not mirror:
        # Rules can change a file's classification, but never below what the scanner found in it.
        floor = floor_for_flags(doc.sensitivity_flags)
        target = classification if rank(classification) >= rank(floor) else floor
        if target != doc.classification:
            doc.classification = target.value
            doc.min_position = int(min_pos or DEFAULT_MIN_POSITION[target])
        doc.owner_team_id = team_id
        doc.discoverable = doc.classification != Classification.RESTRICTED


# --------------------------------------------------------------------------- running syncs


def _lock(source_id: int) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(source_id, threading.Lock())


def run_sync(source_id: int, actor_id: int | None = None) -> SyncReport | None:
    """Sync one source in its own database session. Returns None if it's already syncing."""
    lock = _lock(source_id)
    if not lock.acquire(blocking=False):
        return None
    try:
        with SessionLocal() as db:
            source = db.get(Source, source_id)
            if source is None:
                return None
            source.status = "syncing"
            db.commit()
            report = SyncReport()
            try:
                report = sync_source(db, source)
                source.status, source.last_error = "ok", ""
            except ConnectorError as exc:
                db.rollback()
                source.status, source.last_error = "error", str(exc)
            except Exception:
                db.rollback()
                log.exception("Sync of source %s failed", source_id)
                source.status, source.last_error = "error", "The sync failed unexpectedly; see the server log."
            source = db.get(Source, source_id)
            source.status = "error" if source.last_error else "ok"
            source.last_synced_at = datetime.now(UTC)
            source.stats = report.as_dict()
            actor = db.get(User, actor_id) if actor_id else None
            audit.record(
                db,
                actor,
                "source.sync",
                "source",
                source.id,
                name=source.name,
                status=source.status,
                **{k: getattr(report, k) for k in ("added", "updated", "removed", "skipped")},
            )
            if source.status == "error":
                admins = db.scalars(select(User.id).where(User.is_admin.is_(True), User.is_active.is_(True))).all()
                notify(
                    db,
                    admins,
                    kind="source.error",
                    title=f"Syncing “{source.name}” failed",
                    body=source.last_error,
                    link="/admin?tab=sources",
                    dedupe_key=f"source.error:{source.id}:{datetime.now(UTC):%Y-%m-%d}",
                )
            db.commit()
            return report
    finally:
        lock.release()


def is_syncing(source_id: int) -> bool:
    return _lock(source_id).locked()


def start_sync(source_id: int, actor_id: int | None = None) -> bool:
    """Run a sync in the background. Returns False if this source is already syncing."""
    if is_syncing(source_id):
        return False
    threading.Thread(target=run_sync, args=(source_id, actor_id), name=f"sync-{source_id}", daemon=True).start()
    return True


def due_sources(db: Session, now: datetime | None = None) -> list[int]:
    now = now or datetime.now(UTC)
    due = []
    for s in db.scalars(select(Source).where(Source.enabled.is_(True))):
        last = s.last_synced_at
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        if last is None or last + timedelta(minutes=max(5, s.sync_interval_minutes)) <= now:
            due.append(s.id)
    return due


def reset_stuck_syncs(db: Session) -> None:
    """At start-up nothing is syncing yet; a "syncing" status is left over from a stopped server."""
    for s in db.scalars(select(Source).where(Source.status == "syncing")):
        s.status = "idle"
    db.commit()


def kind_available(kind: SourceKind) -> bool:
    s = get_settings()
    if kind is SourceKind.FOLDER:
        from .connectors.folder import allowed_roots

        return bool(allowed_roots())
    return s.google_configured if kind is SourceKind.GOOGLE else s.microsoft_configured
