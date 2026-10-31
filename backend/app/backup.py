"""Backups, and proof that they restore.

A backup is one archive (vaultmind-backup-<UTC time>.tar.gz) holding:

    manifest.json        what's inside: schema revision, row counts, files, SHA-256 of every part
    db/<table>.jsonl     every row of every table, one JSON object per line
    files/<key>          the original uploaded and synced files, by their storage key

Rows are written as JSON rather than as SQL so a backup taken from PostgreSQL restores into
PostgreSQL or SQLite alike. The database is read in one transaction (REPEATABLE READ on
PostgreSQL), so the rows are a consistent snapshot. Signed-in sessions are left out: after a
restore everyone signs in again.

A restore check (`verify`) rebuilds the archive in a scratch SQLite database and folder, then
compares row counts with the manifest, checks every document's file against its SHA-256,
searches the restored text, and checks someone can still administer it. Scheduled backups
(VAULTMIND_BACKUP_DIR) run that check after every backup and record the result.

    python -m app.cli backup [--out DIR]
    python -m app.cli verify-backup ARCHIVE_OR_DIR
    python -m app.cli restore ARCHIVE --yes        (into an empty database only)

The archive contains every document and the password hashes, so store it encrypted (an
encrypted volume or bucket) and with the same access controls as the database itself.
"""

import hashlib
import json
import logging
import os
import re
import tarfile
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import IO

from alembic import command
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import DateTime, Engine, Integer, func, inspect, select, text

from . import database
from .config import get_settings
from .database import Base
from .migrations import alembic_config

log = logging.getLogger(__name__)

FORMAT = 1
PREFIX = "vaultmind-backup-"
NAME = re.compile(r"^vaultmind-backup-(\d{8}T\d{6}Z)\.tar\.gz$")
STORAGE_KEY = re.compile(r"^[0-9a-f]{2}/[0-9a-f]{64}$")
SKIP_TABLES = {"user_sessions"}  # after a restore everyone signs in again
BATCH = 500
SPOOL_BYTES = 32 * 1024 * 1024  # table dumps above this go to a temporary file, not memory


class BackupError(Exception):
    """The backup or restore can't go ahead. The message says why, for the operator."""


# ---------------------------------------------------------------- writing


def _encode(value):
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


def _add(tar: tarfile.TarFile, name: str, data: IO[bytes], size: int, manifest: dict, digest: str) -> None:
    info = tarfile.TarInfo(name)
    info.size, info.mtime, info.mode = size, int(time.time()), 0o600
    data.seek(0)
    tar.addfile(info, data)
    manifest["checksums"][name] = digest


def head_revision() -> str:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def create(out_dir: Path, *, engine: Engine | None = None, storage_root: Path | None = None) -> Path:
    """Write a backup archive into `out_dir` and return its path."""
    engine = engine or database.engine
    root = Path(storage_root or get_settings().storage_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    final = out_dir / f"{PREFIX}{now:%Y%m%dT%H%M%SZ}.tar.gz"
    partial = out_dir / f".{final.name}.partial"
    manifest: dict = {
        "format": FORMAT,
        "app": "vaultmind",
        "created_at": now.isoformat(),
        "database": engine.dialect.name,
        "schema_revision": None,
        "tables": {},
        "files": {},
        "checksums": {},
    }
    try:
        with tarfile.open(partial, "w:gz") as tar:
            with engine.connect() as conn:
                if conn.dialect.name == "postgresql":
                    conn = conn.execution_options(isolation_level="REPEATABLE READ")
                with conn.begin():
                    manifest["schema_revision"] = MigrationContext.configure(conn).get_current_revision()
                    for table in Base.metadata.sorted_tables:
                        if table.name in SKIP_TABLES:
                            continue
                        rows = 0
                        digest = hashlib.sha256()
                        with tempfile.SpooledTemporaryFile(max_size=SPOOL_BYTES) as buf:
                            for row in conn.execute(select(table).order_by(*table.primary_key.columns)):
                                line = json.dumps({k: _encode(v) for k, v in row._mapping.items()}, ensure_ascii=False)
                                data = line.encode() + b"\n"
                                buf.write(data)
                                digest.update(data)
                                rows += 1
                            size = buf.tell()
                            _add(tar, f"db/{table.name}.jsonl", buf, size, manifest, digest.hexdigest())
                        manifest["tables"][table.name] = rows
                    documents = Base.metadata.tables["documents"]
                    files = sorted(set(conn.execute(select(documents.c.storage_key, documents.c.sha256)).all()))
            for key, expected in files:
                if not STORAGE_KEY.match(key):
                    raise BackupError(f"Unexpected storage key {key!r}")
                try:
                    data = (root / key).read_bytes()
                except FileNotFoundError:
                    raise BackupError(f"A document's file is missing from storage: {key}") from None
                actual = hashlib.sha256(data).hexdigest()
                if actual != expected:
                    raise BackupError(f"A stored file doesn't match its checksum: {key}")
                with tempfile.SpooledTemporaryFile(max_size=SPOOL_BYTES) as buf:
                    buf.write(data)
                    _add(tar, f"files/{key}", buf, len(data), manifest, actual)
                manifest["files"][key] = actual
            body = json.dumps(manifest, indent=1).encode()
            with tempfile.SpooledTemporaryFile() as buf:
                buf.write(body)
                info = tarfile.TarInfo("manifest.json")
                info.size, info.mtime, info.mode = len(body), int(time.time()), 0o600
                buf.seek(0)
                tar.addfile(info, buf)
        os.chmod(partial, 0o600)
        partial.replace(final)
    finally:
        partial.unlink(missing_ok=True)
    log.info("Backup written: %s (%d tables, %d files)", final.name, len(manifest["tables"]), len(manifest["files"]))
    return final


# ---------------------------------------------------------------- reading


def _member(tar: tarfile.TarFile, name: str, manifest: dict | None = None) -> bytes:
    try:
        f = tar.extractfile(name)
    except KeyError:
        raise BackupError(f"The archive has no {name}") from None
    if f is None:
        raise BackupError(f"{name} in the archive isn't a file")
    data = f.read()
    if manifest is not None:
        expected = manifest["checksums"].get(name)
        if expected is None or hashlib.sha256(data).hexdigest() != expected:
            raise BackupError(f"{name} in the archive is damaged (its checksum doesn't match)")
    return data


def read_manifest(archive: Path) -> dict:
    try:
        with tarfile.open(archive, "r:gz") as tar:
            manifest = json.loads(_member(tar, "manifest.json"))
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise BackupError(f"Can't read {archive.name}: {exc}") from None
    if manifest.get("app") != "vaultmind" or manifest.get("format") != FORMAT:
        raise BackupError(f"{archive.name} isn't a VaultMind backup this version can read")
    return manifest


def _decode(table, row: dict) -> dict:
    out = {}
    for name, value in row.items():
        column = table.c.get(name)
        if column is None:
            raise BackupError(f"The backup has a column {table.name}.{name} this version doesn't know")
        if isinstance(value, str) and isinstance(column.type, DateTime):
            value = datetime.fromisoformat(value)
        out[name] = value
    return out


def restore(archive: Path, *, engine: Engine | None = None, storage_root: Path | None = None) -> dict:
    """Restore `archive` into an empty database and storage folder. Never overwrites: a
    database that already has people in it is refused. Returns the archive's manifest."""
    engine = engine or database.engine
    root = Path(storage_root or get_settings().storage_dir)
    manifest = read_manifest(archive)
    head = head_revision()
    if manifest["schema_revision"] != head:
        raise BackupError(
            f"This backup is from database revision {manifest['schema_revision']}, and this version of "
            f"VaultMind is at {head}. Restore it with the version that made it, then upgrade."
        )
    for key in manifest["files"]:
        if not STORAGE_KEY.match(key):
            raise BackupError(f"Refusing an unexpected file name in the archive: {key!r}")
    with tarfile.open(archive, "r:gz") as tar, engine.begin() as conn:
        tables = set(inspect(conn).get_table_names())
        if "users" in tables and conn.execute(text("SELECT COUNT(*) FROM users")).scalar():
            raise BackupError("The database already has people in it. Restore into a new, empty database.")
        cfg = alembic_config()
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
        for table in Base.metadata.sorted_tables:
            if table.name not in manifest["tables"]:
                continue
            lines = _member(tar, f"db/{table.name}.jsonl", manifest).splitlines()
            rows = [_decode(table, json.loads(line)) for line in lines if line]
            expected = manifest["tables"][table.name]
            if len(rows) != expected:
                raise BackupError(f"{table.name}: the archive has {len(rows)} rows, the manifest says {expected}")
            for i in range(0, len(rows), BATCH):
                conn.execute(table.insert(), rows[i : i + BATCH])
        if conn.dialect.name == "postgresql":
            # Rows came with their ids; move each id sequence past the highest one.
            for table in Base.metadata.sorted_tables:
                pk = list(table.primary_key.columns)
                if len(pk) == 1 and isinstance(pk[0].type, Integer):
                    t, c = table.name, pk[0].name
                    conn.execute(
                        text(
                            f"SELECT setval(pg_get_serial_sequence('{t}', '{c}'), "
                            f"COALESCE((SELECT MAX({c}) FROM {t}), 1), (SELECT MAX({c}) FROM {t}) IS NOT NULL)"
                        )
                    )
        for key, digest in manifest["files"].items():
            data = _member(tar, f"files/{key}", manifest)
            if hashlib.sha256(data).hexdigest() != digest:
                raise BackupError(f"files/{key} in the archive is damaged")
            path = root / key
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                tmp = path.with_suffix(".restoring")
                tmp.write_bytes(data)
                tmp.replace(path)
    return manifest


# ---------------------------------------------------------------- the restore check


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Report:
    archive: str
    ok: bool = False
    checked_at: str = ""
    seconds: float = 0.0
    checks: list[Check] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)

    def text(self) -> str:
        lines = [f"Restore check of {self.archive}: {'PASSED' if self.ok else 'FAILED'} ({self.seconds:.1f}s)"]
        lines += [f"  {'ok  ' if c.ok else 'FAIL'} {c.name}{f': {c.detail}' if c.detail else ''}" for c in self.checks]
        return "\n".join(lines)


def verify(archive: Path) -> Report:
    """Restore `archive` into a scratch SQLite database and folder, and check what came back."""
    from .search import BM25Index

    started = time.monotonic()
    report = Report(archive=archive.name, checked_at=datetime.now(UTC).isoformat())

    def done() -> Report:
        report.ok = bool(report.checks) and all(c.ok for c in report.checks)
        report.seconds = round(time.monotonic() - started, 2)
        return report

    with tempfile.TemporaryDirectory(prefix="vaultmind-verify-") as tmp:
        scratch = database.make_engine(f"sqlite:///{tmp}/restored.db")
        storage = Path(tmp) / "storage"
        try:
            manifest = restore(archive, engine=scratch, storage_root=storage)
            report.checks.append(Check("Restores into an empty database", True, f"schema {manifest['schema_revision']}"))
        except Exception as exc:  # any failure to restore is the finding
            report.checks.append(Check("Restores into an empty database", False, str(exc)))
            scratch.dispose()
            return done()
        try:
            with scratch.connect() as conn:
                wrong = []
                for name, expected in manifest["tables"].items():
                    actual = conn.execute(text(f'SELECT COUNT(*) FROM "{name}"')).scalar()
                    if actual != expected:
                        wrong.append(f"{name} {actual} of {expected}")
                total = f"{sum(manifest['tables'].values())} rows in {len(manifest['tables'])} tables"
                report.checks.append(Check("Every row came back", not wrong, ", ".join(wrong) or total))

                docs = Base.metadata.tables["documents"]
                rows = conn.execute(select(docs.c.id, docs.c.title, docs.c.storage_key, docs.c.sha256)).all()
                bad = []
                for doc_id, title, key, digest in rows:
                    path = storage / key
                    if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                        bad.append(f"{title} (#{doc_id})")
                report.checks.append(
                    Check("Every document's file is present and intact", not bad, ", ".join(bad[:5]) or f"{len(rows)} documents")
                )

                chunks = Base.metadata.tables["document_chunks"]
                index = BM25Index()
                by_doc: dict[int, list[tuple[int, str]]] = {}
                for cid, did, txt in conn.execute(select(chunks.c.id, chunks.c.document_id, chunks.c.text)):
                    by_doc.setdefault(did, []).append((cid, txt))
                titles = {doc_id: title for doc_id, title, _, _ in rows}
                for did, parts in by_doc.items():
                    index.add_document(did, titles.get(did, ""), [], parts)
                probe = next((r for r in rows if r[0] in by_doc), None)
                if probe is None:
                    detail = "no documents" if not rows else "no text"
                    report.checks.append(Check("Restored text can be searched", not rows, detail))
                else:
                    found = [h.doc_id for h in index.search(probe[1], top_k=5)]
                    report.checks.append(Check("Restored text can be searched", probe[0] in found, f"searched for “{probe[1]}”"))

                users = Base.metadata.tables["users"]
                people = conn.execute(select(func.count()).select_from(users)).scalar()
                admins = conn.execute(
                    select(func.count()).select_from(users).where(users.c.is_admin.is_(True), users.c.is_active.is_(True))
                ).scalar()
                report.checks.append(
                    Check(
                        "Someone can administer the restored copy",
                        admins > 0 or people == 0,
                        f"{admins} active administrator{'s' if admins != 1 else ''} of {people} people",
                    )
                )
        finally:
            scratch.dispose()
    return done()


# ---------------------------------------------------------------- scheduled backups


def archives(directory: Path) -> list[Path]:
    """Backups in `directory`, newest first."""
    if not directory.is_dir():
        return []
    return sorted((p for p in directory.iterdir() if NAME.match(p.name)), key=lambda p: p.name, reverse=True)


def report_path(archive: Path) -> Path:
    return archive.with_name(archive.name + ".check.json")


def status(directory: Path) -> list[dict]:
    """Recent backups with the result of their restore check, for the admin page."""
    out = []
    for path in archives(directory)[:10]:
        check = None
        try:
            check = json.loads(report_path(path).read_text())
        except (OSError, ValueError):
            pass
        created = datetime.strptime(NAME.match(path.name).group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        out.append(
            {
                "name": path.name,
                "created_at": created.isoformat(),
                "size": path.stat().st_size,
                "verified": None if check is None else bool(check.get("ok")),
                "checks": [] if check is None else check.get("checks", []),
            }
        )
    return out


def _lock(directory: Path) -> Path | None:
    """One backup at a time, even with several app processes. A lock older than 6 h is stale."""
    lock = directory / ".backup.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        return lock
    except FileExistsError:
        if time.time() - lock.stat().st_mtime > 6 * 3600:
            lock.unlink(missing_ok=True)
            return _lock(directory)
        return None


def run_scheduled(now: datetime | None = None) -> Path | None:
    """Back up when one is due (VAULTMIND_BACKUP_DIR set, the newest backup older than the
    interval), check it restores, record the result, and keep only the newest few."""
    from . import audit
    from .notify import notify_admins

    s = get_settings()
    if not s.backup_dir:
        return None
    directory = Path(s.backup_dir)
    directory.mkdir(parents=True, exist_ok=True)
    now = now or datetime.now(UTC)
    latest = archives(directory)
    if latest:
        made = datetime.strptime(NAME.match(latest[0].name).group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        if now - made < timedelta(hours=s.backup_interval_hours):
            return None
    lock = _lock(directory)
    if lock is None:
        return None
    try:
        try:
            archive = create(directory)
        except Exception as exc:
            log.exception("Scheduled backup failed")
            with database.SessionLocal() as db:
                audit.record(db, None, "backup.failed", "backup", None, error=str(exc)[:300])
                notify_admins(db, "The scheduled backup failed", str(exc)[:300], "/admin")
                db.commit()
            return None
        report = verify(archive)
        report_path(archive).write_text(json.dumps(report.as_dict(), indent=1))
        with database.SessionLocal() as db:
            audit.record(
                db,
                None,
                "backup.created" if report.ok else "backup.check_failed",
                "backup",
                None,
                archive=archive.name,
                size=archive.stat().st_size,
                verified=report.ok,
                failed=[c.name for c in report.checks if not c.ok],
            )
            if not report.ok:
                notify_admins(db, "A backup failed its restore check", report.text()[:500], "/admin")
            db.commit()
        for old in archives(directory)[s.backup_keep :]:
            old.unlink(missing_ok=True)
            report_path(old).unlink(missing_ok=True)
        return archive
    finally:
        lock.unlink(missing_ok=True)
