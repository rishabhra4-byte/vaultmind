"""The library as a file explorer: folders, moving, pins, recent, the recycle bin, ZIP downloads.

Listing and opening documents stay in documents.py; this is everything around them.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, explorer, storage
from ..database import get_db
from ..deps import current_user
from ..files import content_disposition
from ..models import AccessRequest, DeletedDocument, Folder, RequestStatus, User
from ..policy import can_manage
from ..schemas import (
    DeletedOut,
    DocumentOut,
    FolderCreate,
    FolderOut,
    FolderUpdate,
    LibraryHome,
    MoveIn,
    MoveOut,
    PinIn,
    ZipIn,
)
from ..serializers import document_out
from ..services import AccessView, access_view, unindex_document

router = APIRouter(prefix="/api/library", tags=["library"])


def _fail(exc: explorer.ExplorerError) -> HTTPException:
    return HTTPException(exc.status, exc.message)


def _folders_out(view: AccessView, folders: dict[int, Folder], pinned: set[int], only: set[int] | None = None) -> list[FolderOut]:
    visible = explorer.visible_folders(view, folders)
    seen = explorer.seen_documents(view)
    counts: dict[int, int] = {}
    for doc_id in seen:
        folder_id = view.docs[doc_id].folder_id
        if folder_id is not None:
            counts[folder_id] = counts.get(folder_id, 0) + 1
    return [
        FolderOut(
            id=f.id,
            name=f.name,
            parent_id=f.parent_id,
            documents=counts.get(f.id, 0),
            can_edit=explorer.can_edit_folder(view.user, f),
            pinned=f.id in pinned,
        )
        for f in sorted(folders.values(), key=lambda f: (f.name.casefold(), f.id))
        if f.id in visible and (only is None or f.id in only)
    ]


def _one(view: AccessView, db: Session, folder: Folder) -> FolderOut:
    _, pinned_folders = explorer.pinned(db, view.user)
    return _folders_out(view, explorer.all_folders(db), set(pinned_folders), {folder.id})[0]


# ===================================================================== folders


@router.get("/folders", response_model=list[FolderOut])
def list_folders(user: User = Depends(current_user), db: Session = Depends(get_db)):
    view = access_view(db, user)
    _, pinned_folders = explorer.pinned(db, user)
    return _folders_out(view, explorer.all_folders(db), set(pinned_folders))


@router.post("/folders", response_model=FolderOut, status_code=status.HTTP_201_CREATED)
def create_folder(body: FolderCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    view = access_view(db, user)
    try:
        folder = explorer.create_folder(db, view, body.name, body.parent_id)
    except explorer.ExplorerError as exc:
        raise _fail(exc) from None
    db.commit()
    return _one(view, db, folder)


@router.patch("/folders/{folder_id}", response_model=FolderOut)
def update_folder(folder_id: int, body: FolderUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    view = access_view(db, user)
    try:
        folder = None
        if body.name is not None:
            folder = explorer.rename_folder(db, view, folder_id, body.name)
        if "parent_id" in body.model_fields_set:
            folder = explorer.move_folder(db, view, folder_id, body.parent_id)
        if folder is None:
            raise explorer.ExplorerError(422, "Send a new name, a new place, or both")
    except explorer.ExplorerError as exc:
        db.rollback()
        raise _fail(exc) from None
    db.commit()
    return _one(view, db, folder)


@router.delete("/folders/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_folder(folder_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Remove a folder. Its documents and folders move up to where it was; nothing is deleted."""
    view = access_view(db, user)
    try:
        explorer.remove_folder(db, view, folder_id)
    except explorer.ExplorerError as exc:
        raise _fail(exc) from None
    db.commit()


@router.post("/move", response_model=MoveOut)
def move(body: MoveIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Move documents and folders into a folder, or to the top of the library."""
    view = access_view(db, user)
    try:
        moved, skipped = explorer.move_documents(
            db, view, body.document_ids, body.to, lambda doc: can_manage(user, doc, view.grants, view.groups)
        )
        folders = explorer.all_folders(db)
        visible = explorer.visible_folders(view, folders)
        for folder_id in dict.fromkeys(body.folder_ids):
            folder = folders.get(folder_id)
            if folder is None or folder_id not in visible:
                skipped.append({"id": folder_id, "title": "", "reason": "Folder not found"})
            elif folder.parent_id != body.to:
                try:
                    explorer.move_folder(db, view, folder_id, body.to)
                    db.flush()
                    moved += 1
                except explorer.ExplorerError as exc:
                    skipped.append({"id": folder_id, "title": folder.name, "reason": exc.message})
    except explorer.ExplorerError as exc:
        db.rollback()
        raise _fail(exc) from None
    db.commit()
    return MoveOut(moved=moved, skipped=skipped)


# ===================================================================== home: pins and recent


def _pending(db: Session, user: User) -> set[int]:
    return set(
        db.scalars(
            select(AccessRequest.document_id).where(
                AccessRequest.requester_id == user.id, AccessRequest.status == RequestStatus.PENDING
            )
        )
    )


@router.get("/home", response_model=LibraryHome)
def home(user: User = Depends(current_user), db: Session = Depends(get_db)):
    view = access_view(db, user)
    pinned_docs, pinned_folders = explorer.pinned(db, user)
    seen = explorer.seen_documents(view)
    pending = _pending(db, user)
    pins = set(pinned_docs)

    def out(ids: list[int]) -> list[DocumentOut]:
        return [document_out(view.docs[i], view, pending, pins) for i in ids if i in seen]

    folders = explorer.all_folders(db)
    listed = {f.id: f for f in _folders_out(view, folders, set(pinned_folders), set(pinned_folders))}
    return LibraryHome(
        pinned_documents=out(pinned_docs),
        pinned_folders=[listed[i] for i in pinned_folders if i in listed],
        recent=out(explorer.recent_documents(db, view)),
    )


def _set_pin(body: PinIn, on: bool, user: User, db: Session) -> None:
    if (body.document_id is None) == (body.folder_id is None):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Pin one document or one folder")
    view = access_view(db, user)
    if on:  # anything may be unpinned; only what the person sees may be pinned
        if body.document_id is not None and body.document_id not in explorer.seen_documents(view):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
        if body.folder_id is not None and body.folder_id not in explorer.visible_folders(view, explorer.all_folders(db)):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Folder not found")
    try:
        explorer.set_pin(db, user, document_id=body.document_id, folder_id=body.folder_id, on=on)
    except explorer.ExplorerError as exc:
        raise _fail(exc) from None
    db.commit()


@router.put("/pins", status_code=status.HTTP_204_NO_CONTENT)
def pin(body: PinIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _set_pin(body, True, user, db)


@router.delete("/pins", status_code=status.HTTP_204_NO_CONTENT)
def unpin(
    document_id: int | None = Query(None),
    folder_id: int | None = Query(None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _set_pin(PinIn(document_id=document_id, folder_id=folder_id), False, user, db)


# ===================================================================== recycle bin


def _bin_item(db: Session, user: User, item_id: int) -> DeletedDocument:
    item = db.get(DeletedDocument, item_id)
    if item is None or not explorer.may_restore(user, item):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not in the recycle bin")
    return item


@router.get("/bin", response_model=list[DeletedOut])
def recycle_bin(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Deleted documents this person may restore, newest first."""
    items = db.scalars(select(DeletedDocument).order_by(DeletedDocument.deleted_at.desc(), DeletedDocument.id.desc()))
    folders = explorer.all_folders(db)
    visible = explorer.visible_folders(access_view(db, user), folders)
    people = dict(db.execute(select(User.id, User.full_name)).all())
    return [
        DeletedOut(
            id=i.id,
            original_id=i.original_id,
            title=i.title,
            filename=i.filename,
            size=i.size,
            classification=i.classification,
            owner_team=i.owner_team.name,
            folder=folders[i.folder_id].name if i.folder_id in visible else None,
            deleted_by=people.get(i.deleted_by),
            deleted_at=i.deleted_at,
            expires_at=i.expires_at,
        )
        for i in items
        if explorer.may_restore(user, i)
    ]


@router.post("/bin/{item_id}/restore", response_model=DocumentOut)
def restore(item_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    item = _bin_item(db, user, item_id)
    doc = explorer.restore(db, user, item)
    doc_id = doc.id
    try:
        db.commit()
    except Exception:
        unindex_document(doc_id)
        raise
    view = access_view(db, user)
    return document_out(view.docs[doc_id], view)


@router.delete("/bin/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_for_good(item_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    explorer.purge(db, user, _bin_item(db, user, item_id))
    db.commit()


@router.delete("/bin", status_code=status.HTTP_204_NO_CONTENT)
def empty_bin(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Remove for good every deleted document this person may restore."""
    for item in list(db.scalars(select(DeletedDocument))):
        if explorer.may_restore(user, item):
            explorer.purge(db, user, item)
    db.commit()


# ===================================================================== several files at once


@router.post("/zip")
def download_zip(body: ZipIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The chosen documents' files as one ZIP. Only documents the person can open are put in."""
    view = access_view(db, user)
    docs = [view.docs[i] for i in dict.fromkeys(body.ids) if i in view.allowed]
    if not docs:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "None of those documents can be downloaded")
    try:
        data = explorer.zip_documents(docs, storage.get)
    except explorer.ExplorerError as exc:
        raise _fail(exc) from None
    for doc in docs:
        audit.record(db, user, "document.download", "document", doc.id, title=doc.title, via="zip")
    db.commit()
    return Response(
        data,
        media_type="application/zip",
        headers={"Content-Disposition": content_disposition("VaultMind documents.zip"), "Cache-Control": "no-store"},
    )
