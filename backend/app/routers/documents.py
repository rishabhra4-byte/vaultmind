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
    AccessGrant,
    AccessRequest,
    Classification,
    Document,
    DocumentGroupShare,
    DocumentShare,
    DocumentVersion,
    Group,
    GroupMember,
    Position,
    RequestStatus,
    Team,
    User,
    utcnow,
)
from ..policy import can_manage, can_view, grant_is_active, is_discoverable_to
from ..recommend import recommend
from ..schemas import (
    AccessInfo,
    ChangesOut,
    DocumentDetail,
    DocumentOut,
    DocumentUpdate,
    NewVersionOut,
    Passage,
    RecommendationOut,
    ScanOut,
    UploadOut,
    UserOut,
    VersionOut,
    WhoCanAccess,
)
from ..search import index, title_index
from ..sensitivity import rank, redact, scan
from ..serializers import document_out
from ..services import (
    AccessView,
    access_view,
    index_document,
    ingest_document,
    remove_document,
    replace_content,
    unindex_document,
    viewer,
)
from ..text import extractive_summary, keywords
from ..versions import diff_text

MAX_PASSAGE_CHARS = 80_000  # how much of a document's text the page shows

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
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Sharing must be a list of comma-separated ids") from None


def _existing_groups(db: Session, ids: list[int]) -> list[int]:
    found = set(db.scalars(select(Group.id).where(Group.id.in_(ids)))) if ids else set()
    if set(ids) - found:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown group")
    return sorted(found)


@router.get("/documents", response_model=list[DocumentOut])
def list_documents(
    q: str | None = None,
    team_id: int | None = None,
    classification: Classification | None = None,
    source: str | None = Query(None, description='"uploaded", or a source id'),
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
        if source == "uploaded" and doc.source_id is not None:
            continue
        if source and source.isdigit() and doc.source_id != int(source):
            continue
        out.append(document_out(doc, view, pending))
    return out


@router.get("/documents/{doc_id}", response_model=DocumentDetail)
def get_document(doc_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    view = access_view(db, user)
    doc = _visible_or_404(view, doc_id)
    base = document_out(doc, view, _pending_ids(db, user))
    preview, passages = None, []
    if base.access.allowed:
        preview = "\n\n".join(c.text for c in doc.chunks)[:6000]
        used = 0
        for c in doc.chunks:
            if used >= MAX_PASSAGE_CHARS:
                break
            passages.append(Passage(ordinal=c.ordinal, text=c.text))
            used += len(c.text)
        audit.record(db, user, "document.view", "document", doc.id, title=doc.title)
    else:
        audit.record(db, user, "document.view_denied", "document", doc.id, title=doc.title, reason=base.access.code)
    db.commit()
    return DocumentDetail(**base.model_dump(), preview=preview, passages=passages)


@router.get("/documents/{doc_id}/download")
def download(doc_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    view = access_view(db, user)
    doc = _visible_or_404(view, doc_id)
    if doc_id not in view.allowed:
        audit.record(db, user, "document.download_denied", "document", doc.id, title=doc.title)
        db.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, view.can_view(doc).reason)
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
    return document_out(doc, view, _pending_ids(db, user)).access


@router.get("/documents/{doc_id}/who-can-access", response_model=list[WhoCanAccess])
def who_can_access(doc_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    view = access_view(db, user)
    doc = _visible_or_404(view, doc_id)
    if not can_manage(user, doc, view.grants, view.groups):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the document's managers can see this")
    groups_by_user: dict[int, set[int]] = {}
    for m in db.scalars(select(GroupMember)):
        groups_by_user.setdefault(m.user_id, set()).add(m.group_id)
    grants_by_user: dict[int, set[int]] = {}
    for g in db.scalars(select(AccessGrant).where(AccessGrant.document_id == doc.id, AccessGrant.revoked.is_(False))):
        if grant_is_active(g.expires_at, g.revoked):
            grants_by_user.setdefault(g.user_id, set()).add(g.document_id)
    out = []
    for member in db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.full_name)):
        decision = can_view(member, doc, grants_by_user.get(member.id, set()), groups_by_user.get(member.id, set()))
        if decision.allowed:
            out.append(WhoCanAccess(user=UserOut.of(member), via=decision.code))
    return out


@router.get("/documents/{doc_id}/versions", response_model=list[VersionOut])
def versions(doc_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    view = access_view(db, user)
    doc = _visible_or_404(view, doc_id)
    if doc_id not in view.allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, view.can_view(doc).reason)
    rows = db.scalars(
        select(DocumentVersion).where(DocumentVersion.document_id == doc.id).order_by(DocumentVersion.version.desc())
    )
    names = {u.id: u.full_name for u in db.scalars(select(User))}
    return [
        VersionOut(
            version=v.version,
            created_at=v.created_at,
            created_by=names.get(v.created_by) if v.created_by else None,
            change_summary=v.change_summary,
            size=v.size,
        )
        for v in rows
    ]


@router.get("/documents/{doc_id}/changes", response_model=ChangesOut)
def changes(
    doc_id: int,
    to_version: int | None = Query(None, alias="to"),
    from_version: int | None = Query(None, alias="from"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """What changed between two versions (by default: the latest and the one before it)."""
    view = access_view(db, user)
    doc = _visible_or_404(view, doc_id)
    if doc_id not in view.allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, view.can_view(doc).reason)
    by_number = {v.version: v for v in db.scalars(select(DocumentVersion).where(DocumentVersion.document_id == doc.id))}
    if not by_number:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No versions recorded for this document")
    to_v = to_version or max(by_number)
    if to_v not in by_number:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Version {to_v} doesn't exist")
    older = [n for n in by_number if n < to_v]
    from_v = from_version if from_version is not None else (max(older) if older else None)
    if from_v is None:
        return ChangesOut(from_version=None, to_version=to_v, summary="This is the first version.", diff=None)
    if from_v not in by_number or from_v >= to_v:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Pick an earlier version to compare with")
    diff = diff_text(by_number[from_v].text, by_number[to_v].text)
    summary = by_number[to_v].change_summary if from_v == to_v - 1 else ""
    return ChangesOut(from_version=from_v, to_version=to_v, summary=summary, diff=diff.as_dict())


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
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The file is empty")
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
    share_group_ids: str | None = Form(None),
    tags: str | None = Form(None),
    acknowledge_sensitivity: bool = Form(False),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    team_id = owner_team_id or user.team_id
    if team_id != user.team_id and not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only upload documents for your own team")
    if db.get(Team, team_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown team")
    if min_position is not None and not Position.INTERN <= min_position <= Position.EXECUTIVE:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "min_position must be between 1 and 6")

    data = await _read_upload(file)
    filename = safe_filename(file.filename)

    try:
        text = extract_text(filename, data)
    except UnsupportedFile as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc
    if not text.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No readable text found in this file")

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
        share_group_ids=_existing_groups(db, _parse_ids(share_group_ids)),
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
        document=document_out(doc, viewer(db, user)),
        scan=ScanOut(suggested_classification=result.suggested, findings=result.findings, summary=doc.summary, tags=doc.tags),
        warning=warning,
    )


@router.patch("/documents/{doc_id}", response_model=DocumentOut)
def update_document(doc_id: int, body: DocumentUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    view = access_view(db, user)
    doc = _visible_or_404(view, doc_id)
    if not can_manage(user, doc, view.grants, view.groups):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can't change this document")

    changes = body.model_dump(exclude_unset=True)
    synced_only = {"ai_enabled", "title", "tags", "summary"}
    if doc.source_id is not None and set(changes) - synced_only:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Sharing for synced files is managed in their source; only AI access and labels can change here",
        )
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
    if "ai_enabled" in changes:
        doc.ai_enabled = changes["ai_enabled"]
    if doc.classification == Classification.RESTRICTED:
        doc.discoverable = False
    doc.updated_at = utcnow()  # sharing lives in another table, so bump this explicitly
    if "share_team_ids" in changes:
        wanted = {t for t in changes["share_team_ids"] if t != doc.owner_team_id}
        doc.shares = [s for s in doc.shares if s.team_id in wanted]
        have = {s.team_id for s in doc.shares}
        doc.shares += [DocumentShare(team_id=t) for t in sorted(wanted - have)]
    if "share_group_ids" in changes:
        wanted_groups = set(_existing_groups(db, changes["share_group_ids"]))
        doc.group_shares = [g for g in doc.group_shares if g.group_id in wanted_groups]
        have_groups = {g.group_id for g in doc.group_shares}
        doc.group_shares += [DocumentGroupShare(group_id=g) for g in sorted(wanted_groups - have_groups)]

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
    return document_out(doc, viewer(db, user), _pending_ids(db, user))


@router.delete("/documents/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(doc_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    view = access_view(db, user)
    doc = _visible_or_404(view, doc_id)
    if not can_manage(user, doc, view.grants, view.groups):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can't delete this document")
    if doc.source_id is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Synced files are deleted in their source, then disappear here")
    audit.record(db, user, "document.delete", "document", doc.id, title=doc.title)
    remove_document(db, doc)  # the stored file goes after the commit, if nothing else uses it
    db.commit()


@router.post("/documents/{doc_id}/versions", response_model=NewVersionOut)
async def upload_new_version(
    doc_id: int,
    file: UploadFile = File(...),
    acknowledge_sensitivity: bool = Form(False),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Replace an uploaded document's file with a new version; readers are told what changed."""
    view = access_view(db, user)
    doc = _visible_or_404(view, doc_id)
    if not can_manage(user, doc, view.grants, view.groups):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can't change this document")
    if doc.source_id is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Synced files get new versions in their source")
    data = await _read_upload(file)
    filename = safe_filename(file.filename)
    try:
        text = extract_text(filename, data)
    except UnsupportedFile as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc
    if not text.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No readable text found in this file")
    if storage.sha256(data) == doc.sha256:
        raise HTTPException(status.HTTP_409_CONFLICT, "This file is the same as the current version")
    result = scan(text)
    if rank(doc.classification) < rank(result.suggested) and not acknowledge_sensitivity:
        found = ", ".join(f["label"].lower() for f in result.findings)
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "message": f"The new version looks {result.suggested.value} (found: {found}) "
                f"but the document is {doc.classification}.",
                "suggested_classification": result.suggested.value,
                "findings": result.findings,
            },
        )
    change = replace_content(db, doc, data=data, filename=filename, actor=user, text=text)
    audit.record(db, user, "document.new_version", "document", doc.id, title=doc.title, version=doc.version, flags=result.flags)
    db.commit()
    db.refresh(doc)
    return NewVersionOut(document=document_out(doc, viewer(db, user)), version=doc.version, change_summary=change.summary)


@router.get("/recommendations", response_model=list[RecommendationOut])
def recommendations(limit: int = Query(6, ge=1, le=20), user: User = Depends(current_user), db: Session = Depends(get_db)):
    view = access_view(db, user)
    pending = _pending_ids(db, user)
    return [
        RecommendationOut(document=document_out(r.document, view, pending), score=r.score, reasons=r.reasons)
        for r in recommend(db, view, limit)
    ]
