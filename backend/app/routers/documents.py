from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, storage
from ..config import get_settings
from ..database import get_db
from ..deps import current_user
from ..extract import UnsupportedFile, extract_text
from ..files import content_disposition, media_type_for, safe_filename
from ..models import (
    DEFAULT_MIN_POSITION,
    AccessRequest,
    Classification,
    Document,
    DocumentShare,
    Position,
    RequestStatus,
    Team,
    User,
    utcnow,
)
from ..policy import can_manage, can_view, is_discoverable_to
from ..recommend import recommend
from ..schemas import (
    AccessInfo,
    DocumentDetail,
    DocumentOut,
    DocumentUpdate,
    RecommendationOut,
    ScanOut,
    UploadOut,
    UserOut,
    WhoCanAccess,
)
from ..search import index, title_index
from ..sensitivity import rank, redact, scan
from ..serializers import document_out
from ..services import (
    AccessView,
    access_view,
    active_grant_ids,
    index_document,
    ingest_document,
    unindex_document,
)
from ..text import extractive_summary, keywords

router = APIRouter(prefix="/api", tags=["documents"])


def _pending_ids(db: Session, user: User) -> set[int]:
    return set(
        db.scalars(
            select(AccessRequest.document_id).where(
                AccessRequest.requester_id == user.id, AccessRequest.status == RequestStatus.PENDING
            )
        )
    )


def _visible_or_404(view: AccessView, doc_id: int) -> Document:
    """Documents a user can neither open nor discover behave as if they don't exist."""
    doc = view.docs.get(doc_id)
    if doc is None or (doc_id not in view.allowed and not is_discoverable_to(view.user, doc)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return doc


def _parse_ids(raw: str | None) -> list[int]:
    if not raw:
        return []
    try:
        return [int(x) for x in raw.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "share_team_ids must be comma-separated ids") from None


@router.get("/documents", response_model=list[DocumentOut])
def list_documents(
    q: str | None = None,
    team_id: int | None = None,
    classification: Classification | None = None,
    include_locked: bool = True,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    view = access_view(db, user)
    candidates = set(view.allowed)
    if include_locked:
        candidates |= {i for i, d in view.docs.items() if is_discoverable_to(user, d)}

    if q and q.strip():
        # Content search only over documents the user can open; locked documents match on
        # their title alone, so search results can't be used to probe what they contain.
        openable = [h.doc_id for h in index.search(q, allowed_doc_ids=set(view.allowed), top_k=50)]
        locked = candidates - set(view.allowed)
        by_title = [h.doc_id for h in title_index.search(q, allowed_doc_ids=locked, top_k=50)] if locked else []
        ordered = openable + by_title
    else:
        ordered = sorted(candidates, key=lambda i: (i in view.allowed, view.docs[i].updated_at), reverse=True)

    pending = _pending_ids(db, user)
    out = []
    for doc_id in ordered:
        doc = view.docs[doc_id]
        if team_id and doc.owner_team_id != team_id:
            continue
        if classification and doc.classification != classification:
            continue
        out.append(document_out(doc, user, view.grants, pending))
    return out


@router.get("/documents/{doc_id}", response_model=DocumentDetail)
def get_document(doc_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    view = access_view(db, user)
    doc = _visible_or_404(view, doc_id)
    base = document_out(doc, user, view.grants, _pending_ids(db, user))
    preview = None
    if base.access.allowed:
        preview = "\n\n".join(c.text for c in doc.chunks)[:6000]
        audit.record(db, user, "document.view", "document", doc.id, title=doc.title)
    else:
        audit.record(db, user, "document.view_denied", "document", doc.id, title=doc.title, reason=base.access.code)
    db.commit()
    return DocumentDetail(**base.model_dump(), preview=preview)


@router.get("/documents/{doc_id}/download")
def download(doc_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    view = access_view(db, user)
    doc = _visible_or_404(view, doc_id)
    if doc_id not in view.allowed:
        audit.record(db, user, "document.download_denied", "document", doc.id, title=doc.title)
        db.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, can_view(user, doc, view.grants).reason)
    audit.record(db, user, "document.download", "document", doc.id, title=doc.title)
    db.commit()
    return Response(
        storage.get(doc.storage_key),
        media_type=media_type_for(doc.filename),
        headers={"Content-Disposition": content_disposition(doc.filename), "Cache-Control": "no-store"},
    )


@router.get("/documents/{doc_id}/access", response_model=AccessInfo)
def explain_access(doc_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """'Why can't I open this?' Returns the policy decision in plain language."""
    view = access_view(db, user)
    doc = _visible_or_404(view, doc_id)
    return document_out(doc, user, view.grants, _pending_ids(db, user)).access


@router.get("/documents/{doc_id}/who-can-access", response_model=list[WhoCanAccess])
def who_can_access(doc_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    view = access_view(db, user)
    doc = _visible_or_404(view, doc_id)
    if not can_manage(user, doc, view.grants):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the document's managers can see this")
    out = []
    for member in db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.full_name)):
        decision = can_view(member, doc, active_grant_ids(db, member))
        if decision.allowed:
            out.append(WhoCanAccess(user=UserOut.of(member), via=decision.code))
    return out


@router.post("/documents/scan", response_model=ScanOut)
async def scan_upload(file: UploadFile = File(...), user: User = Depends(current_user)):
    """Preview the sensitivity scan before uploading, so the form can suggest a classification."""
    data = await _read_upload(file)
    try:
        text = extract_text(safe_filename(file.filename), data)
    except UnsupportedFile as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc
    result = scan(text)
    return ScanOut(
        suggested_classification=result.suggested,
        findings=result.findings,
        summary=redact(extractive_summary(text)),  # quick and free; the AI summary is made on upload
        tags=keywords(text, 5),
    )


async def _read_upload(file: UploadFile) -> bytes:
    limit = get_settings().max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"Files are limited to {get_settings().max_upload_mb} MB")
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "The file is empty")
    return data


@router.post("/documents", response_model=UploadOut, status_code=status.HTTP_201_CREATED)
async def upload(
    file: UploadFile = File(...),
    title: str = Form(""),
    classification: Classification = Form(Classification.INTERNAL),
    min_position: int | None = Form(None),
    discoverable: bool = Form(True),
    owner_team_id: int | None = Form(None),
    share_team_ids: str | None = Form(None),
    tags: str | None = Form(None),
    acknowledge_sensitivity: bool = Form(False),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    team_id = owner_team_id or user.team_id
    if team_id != user.team_id and not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only upload documents for your own team")
    if db.get(Team, team_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown team")
    if min_position is not None and not Position.INTERN <= min_position <= Position.EXECUTIVE:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "min_position must be between 1 and 6")

    data = await _read_upload(file)
    filename = safe_filename(file.filename)

    try:
        text = extract_text(filename, data)
    except UnsupportedFile as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc
    if not text.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "No readable text found in this file")

    result = scan(text)
    warning = None
    if rank(classification) < rank(result.suggested):
        found = ", ".join(f["label"].lower() for f in result.findings)
        warning = f"This file looks {result.suggested.value} (found: {found}) but you chose {classification.value}."
        if not acknowledge_sensitivity:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                {"message": warning, "suggested_classification": result.suggested.value, "findings": result.findings},
            )

    view = access_view(db, user)
    digest = storage.sha256(data)
    duplicate = next((d for d in view.docs.values() if d.sha256 == digest and d.id in view.allowed), None)
    if duplicate:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"message": f"This exact file already exists as “{duplicate.title}”.", "document_id": duplicate.id},
        )

    ingest = ingest_document(
        db,
        uploader=user,
        filename=filename,
        data=data,
        mime_type=media_type_for(filename),
        title=title or filename.rsplit(".", 1)[0].replace("_", " ").replace("-", " ").title(),
        owner_team_id=team_id,
        classification=classification,
        min_position=min_position,
        discoverable=discoverable,
        share_team_ids=_parse_ids(share_team_ids),
        tags=[t for t in (tags or "").split(",") if t.strip()] or None,
    )
    doc = ingest.document
    audit.record(
        db,
        user,
        "document.upload",
        "document",
        doc.id,
        title=doc.title,
        classification=doc.classification,
        flags=result.flags,
        overrode_scanner=warning is not None,
    )
    try:
        db.commit()
    except Exception:
        unindex_document(doc.id)
        raise
    db.refresh(doc)
    return UploadOut(
        document=document_out(doc, user, view.grants),
        scan=ScanOut(suggested_classification=result.suggested, findings=result.findings, summary=doc.summary, tags=doc.tags),
        warning=warning,
    )


@router.patch("/documents/{doc_id}", response_model=DocumentOut)
def update_document(doc_id: int, body: DocumentUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    view = access_view(db, user)
    doc = _visible_or_404(view, doc_id)
    if not can_manage(user, doc, view.grants):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can't change this document")

    changes = body.model_dump(exclude_unset=True)
    if "title" in changes:
        doc.title = changes["title"]
    if "summary" in changes:
        doc.summary = changes["summary"]
    if "tags" in changes:
        doc.tags = [t.strip().lower() for t in changes["tags"] if t.strip()]
    if "classification" in changes:
        doc.classification = changes["classification"].value
        if "min_position" not in changes:
            doc.min_position = DEFAULT_MIN_POSITION[Classification(doc.classification)]
    if "min_position" in changes:
        doc.min_position = changes["min_position"]
    if "discoverable" in changes:
        doc.discoverable = changes["discoverable"]
    if doc.classification == Classification.RESTRICTED:
        doc.discoverable = False
    doc.updated_at = utcnow()  # sharing lives in another table, so bump this explicitly
    if "share_team_ids" in changes:
        wanted = {t for t in changes["share_team_ids"] if t != doc.owner_team_id}
        doc.shares = [s for s in doc.shares if s.team_id in wanted]
        have = {s.team_id for s in doc.shares}
        doc.shares += [DocumentShare(team_id=t) for t in sorted(wanted - have)]

    audit.record(
        db,
        user,
        "document.update",
        "document",
        doc.id,
        title=doc.title,
        changes={k: (v.value if hasattr(v, "value") else v) for k, v in changes.items()},
    )
    db.commit()
    db.refresh(doc)
    if {"title", "tags"} & changes.keys():
        index_document(doc)
    return document_out(doc, user, active_grant_ids(db, user), _pending_ids(db, user))


@router.delete("/documents/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(doc_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    view = access_view(db, user)
    doc = _visible_or_404(view, doc_id)
    if not can_manage(user, doc, view.grants):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can't delete this document")
    audit.record(db, user, "document.delete", "document", doc.id, title=doc.title)
    storage_key = doc.storage_key
    db.delete(doc)
    db.commit()
    unindex_document(doc_id)
    # Blobs are shared by identical files; remove this one only if nothing else uses it.
    if db.scalar(select(Document.id).where(Document.storage_key == storage_key).limit(1)) is None:
        storage.delete(storage_key)


@router.get("/recommendations", response_model=list[RecommendationOut])
def recommendations(limit: int = Query(6, ge=1, le=20), user: User = Depends(current_user), db: Session = Depends(get_db)):
    view = access_view(db, user)
    pending = _pending_ids(db, user)
    return [
        RecommendationOut(document=document_out(r.document, user, view.grants, pending), score=r.score, reasons=r.reasons)
        for r in recommend(db, view, limit)
    ]
