"""The company glossary: everyone can read it (the assistant explains its searches with it);
admins add and edit the company's own terms. Built-in terms can't be changed, but a company
term with the same name takes their place."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit, glossary
from ..database import get_db
from ..deps import admin_user, current_user
from ..models import GlossaryTerm, User
from ..schemas import GlossaryIn, GlossaryOut, GlossaryUpdate

router = APIRouter(prefix="/api", tags=["glossary"])


def _out(t: GlossaryTerm) -> GlossaryOut:
    return GlossaryOut(id=t.id, term=t.term, synonyms=t.synonyms or [], note=t.note, builtin=False)


@router.get("/glossary", response_model=list[GlossaryOut])
def list_terms(_: User = Depends(current_user), db: Session = Depends(get_db)):
    company = {e.term.lower() for e in glossary.entries(db) if not e.builtin}
    out = []
    for e in glossary.entries(db):
        if e.builtin and e.term.lower() in company:
            continue  # the company's own version replaces the built-in one
        out.append(GlossaryOut(id=e.id, term=e.term, synonyms=list(e.synonyms), note=e.note, builtin=e.builtin))
    return out


def _check_unique(db: Session, term: str, exclude: int | None = None) -> None:
    stmt = select(GlossaryTerm.id).where(func.lower(GlossaryTerm.term) == term.lower())
    if exclude is not None:
        stmt = stmt.where(GlossaryTerm.id != exclude)
    if db.scalar(stmt) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"“{term}” is already in the glossary")


@router.post("/admin/glossary", response_model=GlossaryOut, status_code=status.HTTP_201_CREATED)
def add_term(body: GlossaryIn, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    term = " ".join(body.term.split())
    _check_unique(db, term)
    synonyms = [s for s in glossary.clean_phrases(body.synonyms) if s.lower() != term.lower()]
    if not synonyms:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Add at least one other word people use for it")
    row = GlossaryTerm(term=term, synonyms=synonyms, note=body.note.strip(), created_by=admin.id)
    db.add(row)
    db.flush()
    audit.record(db, admin, "glossary.add", "glossary", row.id, term=term, synonyms=synonyms)
    db.commit()
    return _out(row)


@router.patch("/admin/glossary/{term_id}", response_model=GlossaryOut)
def update_term(term_id: int, body: GlossaryUpdate, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    row = db.get(GlossaryTerm, term_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Term not found")
    if body.term is not None:
        term = " ".join(body.term.split())
        _check_unique(db, term, exclude=row.id)
        row.term = term
    if body.synonyms is not None:
        synonyms = [s for s in glossary.clean_phrases(body.synonyms) if s.lower() != row.term.lower()]
        if not synonyms:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Add at least one other word people use for it")
        row.synonyms = synonyms
    if body.note is not None:
        row.note = body.note.strip()
    audit.record(db, admin, "glossary.update", "glossary", row.id, term=row.term, synonyms=row.synonyms)
    db.commit()
    return _out(row)


@router.delete("/admin/glossary/{term_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_term(term_id: int, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    row = db.get(GlossaryTerm, term_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Term not found")
    audit.record(db, admin, "glossary.delete", "glossary", row.id, term=row.term)
    db.delete(row)
    db.commit()
