"""Connected sources (admin only): add, change, sync and remove them."""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..connectors import ConnectorError, build_connector
from ..connectors.folder import allowed_roots
from ..database import get_db
from ..deps import admin_user
from ..models import Document, Source, SourceKind, Team, User
from ..schemas import SourceIn, SourceOut, SourcesOut, SourceUpdate
from ..services import remove_document
from ..sync import Rules, is_syncing, kind_available, start_sync

router = APIRouter(prefix="/api/admin/sources", tags=["sources"])

CONFIG_KEYS = {
    SourceKind.FOLDER: {"path", "rules", "exclude"},
    SourceKind.GOOGLE: {"folders", "rules"},
    SourceKind.MICROSOFT: {"drive_id", "site_url", "library", "user_email", "folder_path", "rules"},
}


def _out(db: Session, s: Source) -> SourceOut:
    count = db.scalar(select(func.count(Document.id)).where(Document.source_id == s.id)) or 0
    return SourceOut(
        id=s.id,
        kind=s.kind,
        label=s.label,
        name=s.name,
        config=s.config or {},
        owner_team_id=s.owner_team_id,
        owner_team=s.owner_team.name,
        classification=s.classification,
        permission_mode=s.permission_mode,
        ai_enabled=s.ai_enabled,
        sync_directory=s.sync_directory,
        enabled=s.enabled,
        sync_interval_minutes=s.sync_interval_minutes,
        status="syncing" if is_syncing(s.id) else s.status,
        last_synced_at=s.last_synced_at,
        last_error=s.last_error,
        stats=s.stats or {},
        document_count=count,
        created_at=s.created_at,
    )


def _validate(db: Session, source: Source) -> None:
    kind = SourceKind(source.kind)
    if not kind_available(kind):
        hint = {
            SourceKind.FOLDER: "No shared-folder locations are allowed on this server (VAULTMIND_FOLDER_ROOTS).",
            SourceKind.GOOGLE: "Google Workspace isn't set up on this server yet (see docs/integrations.md).",
            SourceKind.MICROSOFT: "Microsoft 365 isn't set up on this server yet (see docs/integrations.md).",
        }[kind]
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, hint)
    unknown = set(source.config or {}) - CONFIG_KEYS[kind]
    if unknown:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown settings: {', '.join(sorted(unknown))}")
    if kind is SourceKind.FOLDER and (source.permission_mode != "rules" or source.sync_directory):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Shared folders use folder rules for access")
    if db.get(Team, source.owner_team_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown team")
    try:
        Rules(db, source)
        build_connector(source)  # checks the settings; problems reaching the source show up on the first sync
    except ConnectorError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.get("", response_model=SourcesOut)
def list_sources(_: User = Depends(admin_user), db: Session = Depends(get_db)):
    sources = db.scalars(select(Source).order_by(Source.created_at)).all()
    return SourcesOut(
        sources=[_out(db, s) for s in sources],
        available={k.value: kind_available(k) for k in SourceKind},
        folder_roots=[str(r) for r in allowed_roots()],
    )


@router.post("", response_model=SourceOut, status_code=status.HTTP_201_CREATED)
def create_source(body: SourceIn, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    source = Source(**body.model_dump(), created_by=admin.id, stats={})
    source.kind = body.kind.value
    source.classification = body.classification.value
    _validate(db, source)
    db.add(source)
    db.flush()
    audit.record(db, admin, "source.create", "source", source.id, name=source.name, kind=source.kind)
    db.commit()
    db.refresh(source)
    if source.enabled:
        start_sync(source.id, admin.id)
    return _out(db, source)


@router.get("/{source_id}", response_model=SourceOut)
def get_source(source_id: int, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Source not found")
    return _out(db, source)


@router.patch("/{source_id}", response_model=SourceOut)
def update_source(source_id: int, body: SourceUpdate, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Source not found")
    changes = body.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(source, key, value.value if hasattr(value, "value") else value)
    _validate(db, source)
    if "ai_enabled" in changes:
        for doc in db.scalars(select(Document).where(Document.source_id == source.id)):
            doc.ai_enabled = source.ai_enabled
    audit.record(db, admin, "source.update", "source", source.id, name=source.name, changes=sorted(changes))
    db.commit()
    db.refresh(source)
    return _out(db, source)


@router.post("/{source_id}/sync", status_code=status.HTTP_202_ACCEPTED)
def sync_now(source_id: int, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Source not found")
    if not start_sync(source.id, admin.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "This source is already syncing")
    return {"status": "syncing"}


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(source_id: int, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    """Disconnect a source. Its files are removed from VaultMind (they stay in the source)."""
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Source not found")
    if is_syncing(source.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Wait for the current sync to finish")
    removed = 0
    for doc in db.scalars(select(Document).where(Document.source_id == source.id)).all():
        remove_document(db, doc)
        removed += 1
    audit.record(db, admin, "source.delete", "source", source.id, name=source.name, documents_removed=removed)
    db.delete(source)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
