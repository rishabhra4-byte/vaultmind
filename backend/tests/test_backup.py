"""Backups: a round trip into an empty database (SQLite, or PostgreSQL when the suite runs on
it), the restore check, damaged archives, refusing to overwrite, and scheduled backups."""

import io
import json
import tarfile
from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from app import backup
from app.config import get_settings
from app.database import SessionLocal
from app.models import AuditEvent, Document, DocumentChunk, Notification, User, UserSession


def _counts(engine) -> dict[str, int]:
    with engine.connect() as conn:
        return {
            name: conn.execute(text(f'SELECT COUNT(*) FROM "{name}"')).scalar()
            for name in ("users", "teams", "documents", "document_chunks", "document_versions", "audit_events")
        }


def test_a_backup_restores_into_an_empty_database(tmp_path, empty_database, client, login):
    from app.database import engine

    login("priya")  # a session, which backups leave out
    archive = backup.create(tmp_path / "backups")
    assert backup.NAME.match(archive.name) and oct(archive.stat().st_mode)[-3:] == "600"

    storage = tmp_path / "restored-files"
    manifest = backup.restore(archive, engine=empty_database, storage_root=storage)
    assert manifest["database"] == engine.dialect.name
    assert _counts(empty_database) == _counts(engine)
    with empty_database.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM user_sessions")).scalar() == 0
        title, key, digest = conn.execute(text("SELECT title, storage_key, sha256 FROM documents ORDER BY id")).first()
        assert title == "Employee Handbook 2026"
        assert (storage / key).exists()
        # Ids carry on from the restored rows (PostgreSQL sequences are moved past them).
        conn.execute(text("INSERT INTO teams (name, description) VALUES ('After restore', '')"))
        new_id = conn.execute(text("SELECT id FROM teams WHERE name = 'After restore'")).scalar()
        assert new_id > conn.execute(text("SELECT MAX(id) FROM teams WHERE name <> 'After restore'")).scalar()


def test_the_restore_check_passes_for_a_good_backup(tmp_path):
    archive = backup.create(tmp_path)
    report = backup.verify(archive)
    assert report.ok, report.text()
    names = [c.name for c in report.checks]
    assert names == [
        "Restores into an empty database",
        "Every row came back",
        "Every document's file is present and intact",
        "Restored text can be searched",
        "Someone can administer the restored copy",
    ]
    with SessionLocal() as db:
        docs = db.scalar(select(func.count(Document.id)))
    assert f"{docs} documents" in report.checks[2].detail


def _rewrite(archive: Path, change) -> Path:
    """A copy of `archive` with one member changed by `change(name, data) -> data | None`."""
    out = archive.with_name("tampered-" + archive.name)
    with tarfile.open(archive, "r:gz") as src, tarfile.open(out, "w:gz") as dst:
        for m in src.getmembers():
            data = src.extractfile(m).read()
            data = change(m.name, data)
            if data is None:
                continue
            info = tarfile.TarInfo(m.name)
            info.size = len(data)
            dst.addfile(info, io.BytesIO(data))
    return out


def test_a_damaged_backup_fails_the_check(tmp_path):
    archive = backup.create(tmp_path)

    def flip(name, data):
        return data.replace(b"Employee Handbook 2026", b"Employee Handbook 2025") if name == "db/documents.jsonl" else data

    report = backup.verify(_rewrite(archive, flip))
    assert not report.ok
    assert "damaged" in report.checks[0].detail

    def drop_file(name, data):
        return None if name.startswith("files/") else data

    assert not backup.verify(_rewrite(archive, drop_file)).ok


def test_a_backup_with_a_path_in_its_file_names_is_refused(tmp_path, empty_database):
    archive = backup.create(tmp_path)

    def escape(name, data):
        if name != "manifest.json":
            return data
        manifest = json.loads(data)
        manifest["files"]["../../etc/cron.d/x"] = "0" * 64
        return json.dumps(manifest).encode()

    with pytest.raises(backup.BackupError, match="unexpected file name"):
        backup.restore(_rewrite(archive, escape), engine=empty_database, storage_root=tmp_path / "out")
    assert not (tmp_path / "etc").exists()


def test_restore_never_overwrites_a_database_with_people_in_it(tmp_path):
    archive = backup.create(tmp_path)
    with pytest.raises(backup.BackupError, match="already has people"):
        backup.restore(archive)
    with SessionLocal() as db:  # nothing changed
        assert db.scalar(select(func.count(User.id))) == 9


def test_a_backup_from_another_schema_version_is_refused(tmp_path, empty_database):
    archive = backup.create(tmp_path)

    def older(name, data):
        if name != "manifest.json":
            return data
        manifest = json.loads(data)
        manifest["schema_revision"] = "0002"
        return json.dumps(manifest).encode()

    with pytest.raises(backup.BackupError, match="Restore it with the version that made it"):
        backup.restore(_rewrite(archive, older), engine=empty_database, storage_root=tmp_path / "out")


def test_a_missing_file_stops_the_backup(tmp_path):
    with SessionLocal() as db:
        key = db.scalar(select(Document.storage_key).order_by(Document.id))
    (Path(get_settings().storage_dir) / key).unlink()
    with pytest.raises(backup.BackupError, match="missing from storage"):
        backup.create(tmp_path)
    assert list(tmp_path.iterdir()) == []  # no half-written archive left behind


def test_scheduled_backups_are_checked_recorded_and_pruned(tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "backup_dir", str(tmp_path))
    monkeypatch.setattr(s, "backup_keep", 2)
    for stamp in ("20260101T000000Z", "20260102T000000Z"):  # two old ones
        (tmp_path / f"vaultmind-backup-{stamp}.tar.gz").write_bytes(b"old")

    made = backup.run_scheduled()
    assert made is not None and made.exists()
    check = json.loads(backup.report_path(made).read_text())
    assert check["ok"] is True
    assert [p.name for p in backup.archives(tmp_path)] == [made.name, "vaultmind-backup-20260102T000000Z.tar.gz"]
    assert backup.run_scheduled() is None, "not due again until the interval has passed"

    with SessionLocal() as db:
        event = db.scalar(select(AuditEvent).where(AuditEvent.action == "backup.created"))
        assert event is not None and event.detail["verified"] is True
    status = backup.status(tmp_path)
    assert status[0]["verified"] is True and status[0]["checks"]


def test_a_failed_scheduled_backup_tells_the_administrators(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "backup_dir", str(tmp_path))
    with SessionLocal() as db:
        key = db.scalar(select(Document.storage_key).order_by(Document.id))
    (Path(get_settings().storage_dir) / key).unlink()
    assert backup.run_scheduled() is None
    with SessionLocal() as db:
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "backup.failed")) is not None
        admins = db.scalars(select(User.id).where(User.is_admin.is_(True))).all()
        notes = db.scalars(select(Notification).where(Notification.title == "The scheduled backup failed")).all()
        assert {n.user_id for n in notes} == set(admins)


def test_backup_cli(tmp_path, capsys):
    from app.cli import main

    assert main(["backup", "--out", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "Backup written" in out and "PASSED" in out
    assert main(["verify-backup", str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert main(["restore", str(backup.archives(tmp_path)[0])]) == 1  # needs --yes
    assert main(["restore", str(backup.archives(tmp_path)[0]), "--yes"]) == 1  # not an empty database
    assert "already has people" in capsys.readouterr().err


def test_sessions_and_chunks_are_counted_right(tmp_path, login):
    login("neha")
    archive = backup.create(tmp_path)
    manifest = backup.read_manifest(archive)
    assert "user_sessions" not in manifest["tables"]
    with SessionLocal() as db:
        assert manifest["tables"]["document_chunks"] == db.scalar(select(func.count(DocumentChunk.id)))
        assert db.scalar(select(func.count()).select_from(UserSession)) >= 1
