"""The library as a file explorer: folders, pins, recent documents and the recycle bin.

Folders only arrange documents. Who may open a document is decided by the document
(policy.py) and never by where it sits, so moving one changes nothing about access. A folder
is shown to a person only if they made it or something inside it is theirs to see, because a
folder's name can say as much as a title ("Layoffs 2027").

Deleting a document removes it everywhere exactly as it always did: from search, the
assistant, sharing and requests. What is needed to bring it back (settings, sharing, passages,
earlier versions, and the stored file) is kept in the recycle bin for `KEEP_DAYS`, then removed
by the scheduler. Access requests and temporary grants are not restored: someone who was
given a document for a week shouldn't get it back months later by a restore.
"""

import io
import re
import zipfile
from datetime import timedelta
from pathlib import PurePath

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from . import audit
from .models import (
    AuditEvent,
    DeletedDocument,
    Document,
    DocumentChunk,
    DocumentGroupShare,
    DocumentShare,
    DocumentVersion,
    Folder,
    Group,
    Pin,
    Position,
    Team,
    User,
    utcnow,
)
from .policy import is_discoverable_to
from .services import AccessView, collect_blob, index_document, unindex_document
from .txn import after_commit

KEEP_DAYS = 30  # how long a deleted document stays in the recycle bin
MAX_DEPTH = 8  # folders inside folders
MAX_FOLDERS = 2000
ZIP_MAX_FILES = 100
ZIP_MAX_BYTES = 200 * 1024 * 1024
RECENT = 12

_BAD_NAME = re.compile(r"[\x00-\x1f\x7f/\\]")


class ExplorerError(Exception):
    """Something the person asked for can't be done; `status` is the HTTP status to answer with."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# ===================================================================== folders


def all_folders(db: Session) -> dict[int, Folder]:
    return {f.id: f for f in db.scalars(select(Folder))}


def ancestors(folders: dict[int, Folder], folder_id: int | None) -> list[int]:
    """The folder and everything above it, nearest first. Stops if the data ever loops."""
    out: list[int] = []
    while folder_id is not None and folder_id in folders and folder_id not in out:
        out.append(folder_id)
        folder_id = folders[folder_id].parent_id
    return out


def subtree(folders: dict[int, Folder], folder_id: int) -> set[int]:
    """The folder and every folder inside it."""
    children: dict[int | None, list[int]] = {}
    for f in folders.values():
        children.setdefault(f.parent_id, []).append(f.id)
    out, todo = set(), [folder_id]
    while todo:
        current = todo.pop()
        if current not in out:
            out.add(current)
            todo += children.get(current, [])
    return out


def seen_documents(view: AccessView) -> set[int]:
    """Documents the library shows this person: the ones they can open and the ones they can ask for."""
    return view.allowed | {i for i, d in view.docs.items() if is_discoverable_to(view.user, d)}


def visible_folders(view: AccessView, folders: dict[int, Folder]) -> set[int]:
    """Folders this person sees: their own, any holding a document they see, and what's above those."""
    if view.user.is_admin:
        return set(folders)
    seen = seen_documents(view)
    start = {f.id for f in folders.values() if f.created_by == view.user.id}
    start |= {view.docs[i].folder_id for i in seen if view.docs[i].folder_id is not None}
    out: set[int] = set()
    for folder_id in start:
        out.update(ancestors(folders, folder_id))
    return out


def can_edit_folder(user: User, folder: Folder) -> bool:
    return user.is_admin or folder.created_by == user.id


def clean_name(name: str) -> str:
    name = " ".join(name.split())
    if not name or len(name) > 120 or _BAD_NAME.search(name) or name in (".", ".."):
        raise ExplorerError(422, "A folder name needs 1 to 120 characters and can't contain / or \\")
    return name


def _check_free(folders: dict[int, Folder], parent_id: int | None, name: str, ignore: int | None = None) -> None:
    for f in folders.values():
        if f.parent_id == parent_id and f.id != ignore and f.name.casefold() == name.casefold():
            raise ExplorerError(409, f"There is already a folder called “{f.name}” here")


def _parent_or_error(view: AccessView, folders: dict[int, Folder], parent_id: int | None) -> None:
    if parent_id is not None and parent_id not in visible_folders(view, folders):
        raise ExplorerError(404, "Folder not found")


def create_folder(db: Session, view: AccessView, name: str, parent_id: int | None) -> Folder:
    folders = all_folders(db)
    if len(folders) >= MAX_FOLDERS:
        raise ExplorerError(409, "The library has as many folders as it can hold")
    _parent_or_error(view, folders, parent_id)
    if len(ancestors(folders, parent_id)) >= MAX_DEPTH:
        raise ExplorerError(409, f"Folders can be nested {MAX_DEPTH} deep at most")
    name = clean_name(name)
    _check_free(folders, parent_id, name)
    folder = Folder(name=name, parent_id=parent_id, created_by=view.user.id)
    db.add(folder)
    db.flush()
    audit.record(db, view.user, "folder.create", "folder", folder.id, name=name, parent_id=parent_id)
    return folder


def _editable(view: AccessView, folders: dict[int, Folder], folder_id: int) -> Folder:
    folder = folders.get(folder_id)
    if folder is None or folder_id not in visible_folders(view, folders):
        raise ExplorerError(404, "Folder not found")
    if not can_edit_folder(view.user, folder):
        raise ExplorerError(403, "Only the person who made this folder, or an admin, can change it")
    return folder


def rename_folder(db: Session, view: AccessView, folder_id: int, name: str) -> Folder:
    folders = all_folders(db)
    folder = _editable(view, folders, folder_id)
    name = clean_name(name)
    _check_free(folders, folder.parent_id, name, ignore=folder.id)
    audit.record(db, view.user, "folder.rename", "folder", folder.id, name=name, was=folder.name)
    folder.name = name
    return folder


def move_folder(db: Session, view: AccessView, folder_id: int, parent_id: int | None) -> Folder:
    folders = all_folders(db)
    folder = _editable(view, folders, folder_id)
    _parent_or_error(view, folders, parent_id)
    inside = subtree(folders, folder.id)
    if parent_id in inside:
        raise ExplorerError(409, "A folder can't be moved into itself")
    depth_below = max(len(ancestors(folders, f)) for f in inside) - len(ancestors(folders, folder.id))
    if len(ancestors(folders, parent_id)) + 1 + depth_below > MAX_DEPTH:
        raise ExplorerError(409, f"Folders can be nested {MAX_DEPTH} deep at most")
    _check_free(folders, parent_id, folder.name, ignore=folder.id)
    audit.record(db, view.user, "folder.move", "folder", folder.id, name=folder.name, parent_id=parent_id)
    folder.parent_id = parent_id
    return folder


def remove_folder(db: Session, view: AccessView, folder_id: int) -> None:
    """Take a folder away. What was in it moves up to where the folder was: nothing is deleted,
    including documents the person removing it can't see."""
    folders = all_folders(db)
    folder = _editable(view, folders, folder_id)
    for child in folders.values():
        if child.parent_id == folder.id:
            taken = {f.name.casefold() for f in folders.values() if f.parent_id == folder.parent_id and f.id != folder.id}
            if child.name.casefold() in taken:
                child.name = f"{child.name} (from {folder.name})"[:120]
            child.parent_id = folder.parent_id
    for doc in db.scalars(select(Document).where(Document.folder_id == folder.id)):
        place(doc, folder.parent_id)
    db.flush()
    audit.record(db, view.user, "folder.remove", "folder", folder.id, name=folder.name)
    db.delete(folder)


def place(doc: Document, folder_id: int | None) -> None:
    """Put a document in a folder without touching its "updated" time: moving a document isn't
    changing it, and how old a document is drives the stale-document warnings."""
    doc.folder_id = folder_id
    flag_modified(doc, "updated_at")  # sent as it is, so the column's on-update default doesn't fire


def move_documents(
    db: Session, view: AccessView, doc_ids: list[int], folder_id: int | None, can_manage
) -> tuple[int, list[dict]]:
    """Move documents into a folder (or to the top). Returns how many moved, and the ones that
    couldn't with why. Moving arranges the shared library, so it needs the same right as
    renaming a document."""
    folders = all_folders(db)
    _parent_or_error(view, folders, folder_id)
    seen = seen_documents(view)
    moved, skipped = 0, []
    for doc_id in dict.fromkeys(doc_ids):
        doc = view.docs.get(doc_id)
        if doc is None or doc_id not in seen:
            skipped.append({"id": doc_id, "title": "", "reason": "Document not found"})
        elif not can_manage(doc):
            skipped.append(
                {"id": doc_id, "title": doc.title, "reason": "Only its owner, a manager of its team or an admin can move it"}
            )
        elif doc.folder_id != folder_id:
            audit.record(
                db, view.user, "document.move", "document", doc.id, title=doc.title, folder_id=folder_id, was=doc.folder_id
            )
            place(doc, folder_id)
            moved += 1
    return moved, skipped


# ===================================================================== pins and recent


def pinned(db: Session, user: User) -> tuple[list[int], list[int]]:
    """(document ids, folder ids) this person pinned, oldest pin first."""
    pins = list(db.scalars(select(Pin).where(Pin.user_id == user.id).order_by(Pin.created_at, Pin.id)))
    return [p.document_id for p in pins if p.document_id is not None], [p.folder_id for p in pins if p.folder_id is not None]


def set_pin(db: Session, user: User, *, document_id: int | None = None, folder_id: int | None = None, on: bool) -> None:
    where = [Pin.user_id == user.id, Pin.document_id == document_id if document_id is not None else Pin.folder_id == folder_id]
    existing = db.scalar(select(Pin).where(*where))
    if on and existing is None:
        if db.scalar(select(func.count()).select_from(Pin).where(Pin.user_id == user.id)) >= 100:
            raise ExplorerError(409, "You can pin 100 things at most")
        db.add(Pin(user_id=user.id, document_id=document_id, folder_id=folder_id))
    elif not on and existing is not None:
        db.delete(existing)


def recent_documents(db: Session, view: AccessView, limit: int = RECENT) -> list[int]:
    """Documents this person opened most recently that they can still open, newest first."""
    rows = db.execute(
        select(AuditEvent.target_id, func.max(AuditEvent.created_at).label("at"))
        .where(AuditEvent.actor_id == view.user.id, AuditEvent.action == "document.view", AuditEvent.target_id.is_not(None))
        .group_by(AuditEvent.target_id)
        .order_by(func.max(AuditEvent.created_at).desc())
        .limit(limit * 3)
    ).all()
    return [doc_id for doc_id, _ in rows if doc_id in view.allowed][:limit]


# ===================================================================== recycle bin


def may_restore(user: User, item: DeletedDocument) -> bool:
    """Who sees a deleted document in the bin and may restore or remove it: the people who
    could have deleted it (it has no sharing left to judge by, so this is by ownership)."""
    return (
        user.is_admin
        or item.deleted_by == user.id
        or item.uploaded_by == user.id
        or (user.team_id == item.owner_team_id and user.position >= Position.MANAGER)
    )


def recycle(db: Session, user: User, doc: Document) -> DeletedDocument:
    """Delete a document and keep what's needed to restore it."""
    versions = db.scalars(select(DocumentVersion).where(DocumentVersion.document_id == doc.id).order_by(DocumentVersion.version))
    now = utcnow()
    item = DeletedDocument(
        original_id=doc.id,
        title=doc.title,
        filename=doc.filename,
        size=doc.size,
        storage_key=doc.storage_key,
        owner_team_id=doc.owner_team_id,
        classification=doc.classification,
        folder_id=doc.folder_id,
        uploaded_by=doc.uploaded_by,
        deleted_by=user.id,
        deleted_at=now,
        expires_at=now + timedelta(days=KEEP_DAYS),
        payload={
            "mime_type": doc.mime_type,
            "sha256": doc.sha256,
            "min_position": doc.min_position,
            "discoverable": doc.discoverable,
            "summary": doc.summary,
            "tags": list(doc.tags or []),
            "sensitivity_flags": list(doc.sensitivity_flags or []),
            "created_at": doc.created_at.isoformat(),
            "updated_at": doc.updated_at.isoformat(),
            "ai_enabled": doc.ai_enabled,
            "version": doc.version or 1,
            "share_team_ids": sorted(doc.shared_team_ids),
            "share_group_ids": sorted(doc.shared_group_ids),
            "chunks": [c.text for c in doc.chunks],
            "versions": [
                {
                    "version": v.version,
                    "text": v.text,
                    "sha256": v.sha256,
                    "size": v.size,
                    "change_summary": v.change_summary,
                    "created_by": v.created_by,
                    "created_at": v.created_at.isoformat(),
                }
                for v in versions
            ],
        },
    )
    db.add(item)
    doc_id = doc.id
    db.delete(doc)  # the stored file stays: the bin still points at it
    after_commit(db, lambda: unindex_document(doc_id))
    return item


def _when(value: str | None):
    from datetime import datetime

    try:
        return datetime.fromisoformat(value) if value else utcnow()
    except ValueError:
        return utcnow()


def restore(db: Session, user: User, item: DeletedDocument) -> Document:
    """Bring a deleted document back as it was: same settings, sharing, passages and versions,
    and the same number if nothing has taken it, so old links and citations work again."""
    p = item.payload or {}
    doc = Document(
        title=item.title,
        filename=item.filename,
        mime_type=p.get("mime_type") or "application/octet-stream",
        size=item.size,
        sha256=p.get("sha256", ""),
        storage_key=item.storage_key,
        owner_team_id=item.owner_team_id,
        folder_id=item.folder_id if item.folder_id is not None and db.get(Folder, item.folder_id) else None,
        classification=item.classification,
        min_position=int(p.get("min_position", Position.INTERN)),
        discoverable=bool(p.get("discoverable", True)),
        summary=p.get("summary", ""),
        tags=list(p.get("tags", [])),
        sensitivity_flags=list(p.get("sensitivity_flags", [])),
        uploaded_by=item.uploaded_by,
        created_at=_when(p.get("created_at")),
        updated_at=_when(p.get("updated_at")),
        ai_enabled=bool(p.get("ai_enabled", True)),
        version=int(p.get("version", 1)),
    )
    if db.get(Document, item.original_id) is None:
        doc.id = item.original_id
    teams = set(db.scalars(select(Team.id).where(Team.id.in_(p.get("share_team_ids", []) or [0]))))
    groups = set(db.scalars(select(Group.id).where(Group.id.in_(p.get("share_group_ids", []) or [0]))))
    doc.shares = [DocumentShare(team_id=t) for t in sorted(teams) if t != item.owner_team_id]
    doc.group_shares = [DocumentGroupShare(group_id=g) for g in sorted(groups)]
    doc.chunks = [DocumentChunk(ordinal=i, text=text) for i, text in enumerate(p.get("chunks", []))]
    db.add(doc)
    db.flush()
    known_users = set(db.scalars(select(User.id)))
    for v in p.get("versions", []):
        db.add(
            DocumentVersion(
                document_id=doc.id,
                version=int(v["version"]),
                text=v.get("text", ""),
                sha256=v.get("sha256", ""),
                size=int(v.get("size", 0)),
                change_summary=v.get("change_summary", ""),
                created_by=v.get("created_by") if v.get("created_by") in known_users else None,
                created_at=_when(v.get("created_at")),
            )
        )
    audit.record(db, user, "document.restore", "document", doc.id, title=doc.title)
    db.delete(item)
    db.flush()
    index_document(doc)  # the caller takes it out again if the commit fails
    return doc


def purge(db: Session, user: User | None, item: DeletedDocument) -> None:
    """Remove a deleted document for good, and its stored file if nothing else uses it."""
    audit.record(db, user, "document.purge", "document", item.original_id, title=item.title)
    key = item.storage_key
    db.delete(item)
    collect_blob(db, key)


def purge_expired(db: Session) -> int:
    """Empty the bin of what has been there longer than `KEEP_DAYS`. Run by the scheduler."""
    items = list(db.scalars(select(DeletedDocument).where(DeletedDocument.expires_at <= utcnow())))
    for item in items:
        purge(db, None, item)
    db.commit()
    return len(items)


# ===================================================================== several files at once


def zip_documents(docs: list[Document], read) -> bytes:
    """A ZIP of the documents' files. Two files with one name get " (2)", " (3)"."""
    if len(docs) > ZIP_MAX_FILES:
        raise ExplorerError(413, f"Choose {ZIP_MAX_FILES} documents at most to download together")
    if sum(d.size for d in docs) > ZIP_MAX_BYTES:
        raise ExplorerError(413, f"Those files are more than {ZIP_MAX_BYTES // 1048576} MB together; download fewer at once")
    used: set[str] = set()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for doc in docs:
            path = PurePath(doc.filename)
            name, n = path.name, 1
            while name.casefold() in used:
                n += 1
                name = f"{path.stem} ({n}){path.suffix}"
            used.add(name.casefold())
            archive.writestr(name, read(doc.storage_key))
    return buffer.getvalue()
