"""Answer quality for administrators: the fix-it queue and verified answers."""

import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, review, verified
from ..database import get_db
from ..deps import admin_user
from ..models import Document, ReviewCase, Team, User, VerifiedAnswer

router = APIRouter(prefix="/api/admin", tags=["quality"])


# ---------------------------------------------------------------- the fix-it queue


class DecisionIn(BaseModel):
    question: str = Field(..., min_length=1, max_length=300)
    status: Literal["resolved", "dismissed"] = "resolved"
    expect: Literal["answer", "abstain"] = "answer"
    doc_ids: list[int] = Field(default_factory=list, max_length=5)
    says: list[str] = Field(default_factory=list, max_length=5)
    note: str = Field("", max_length=500)


@router.get("/review")
def review_queue(days: int = Query(90, ge=1, le=365), _: User = Depends(admin_user), db: Session = Depends(get_db)):
    """Questions to fix (without who asked) and the decided cases with their last check."""
    return review.queue(db, days)


@router.post("/review")
def decide(body: DecisionIn, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    """Say what answers a question (and check it straight away), or dismiss it."""
    try:
        case = review.decide(db, admin, **body.model_dump())
    except review.Invalid as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    db.flush()
    if case.status == "resolved":
        review.check(db, case)
    audit.record(db, admin, f"review.{case.status}", "review_case", case.id, question=case.question, expect=case.expect)
    db.commit()
    return review.as_dict(case, dict(db.execute(select(Document.id, Document.title)).all()))


def _case(db: Session, case_id: int) -> ReviewCase:
    case = db.get(ReviewCase, case_id)
    if case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such case")
    return case


@router.post("/review/{case_id}/check")
def check_case(case_id: int, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    case = _case(db, case_id)
    review.check(db, case)
    db.commit()
    return review.as_dict(case, dict(db.execute(select(Document.id, Document.title)).all()))


@router.post("/review/check-all")
def check_all(_: User = Depends(admin_user), db: Session = Depends(get_db)):
    result = review.check_all(db)
    db.commit()
    return result


@router.delete("/review/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
def reopen(case_id: int, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    """Undo a decision: the question goes back to the queue (if it's still recent)."""
    case = _case(db, case_id)
    audit.record(db, admin, "review.reopened", "review_case", case.id, question=case.question)
    db.delete(case)
    db.commit()


@router.get("/review/export")
def export(_: User = Depends(admin_user), db: Session = Depends(get_db)):
    lines = "\n".join(json.dumps(c, ensure_ascii=False) for c in review.export(db))
    return Response(
        lines + ("\n" if lines else ""),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": 'attachment; filename="vaultmind-cases.jsonl"'},
    )


# ---------------------------------------------------------------- verified answers


class VerifiedIn(BaseModel):
    question: str = Field(..., min_length=1, max_length=300)
    phrasings: list[str] = Field(default_factory=list, max_length=10)
    answer: str = Field(..., min_length=1, max_length=4000)
    doc_ids: list[int] = Field(..., min_length=1, max_length=10)
    owner_team_id: int
    review_days: int = Field(90, ge=7, le=365)
    active: bool = True


def _docs(db: Session) -> dict[int, Document]:
    return {d.id: d for d in db.scalars(select(Document))}


@router.get("/verified")
def list_verified(_: User = Depends(admin_user), db: Session = Depends(get_db)):
    docs = _docs(db)
    rows = db.scalars(select(VerifiedAnswer).order_by(VerifiedAnswer.question))
    return [verified.as_dict(va, docs) for va in rows]


def _save(db: Session, admin: User, body: VerifiedIn, va: VerifiedAnswer | None) -> dict:
    if db.get(Team, body.owner_team_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown team")
    try:
        va = verified.save(db, admin, va=va, **body.model_dump())
    except verified.Invalid as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    db.flush()
    audit.record(db, admin, "verified.saved", "verified_answer", va.id, question=va.question, sources=va.doc_ids)
    db.commit()
    db.refresh(va)
    return verified.as_dict(va, _docs(db))


@router.post("/verified", status_code=status.HTTP_201_CREATED)
def create_verified(body: VerifiedIn, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    return _save(db, admin, body, None)


def _verified(db: Session, va_id: int) -> VerifiedAnswer:
    va = db.get(VerifiedAnswer, va_id)
    if va is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such verified answer")
    return va


@router.put("/verified/{va_id}")
def update_verified(va_id: int, body: VerifiedIn, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    return _save(db, admin, body, _verified(db, va_id))


@router.post("/verified/{va_id}/reviewed")
def mark_reviewed(va_id: int, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    """Still right: verified again today, against the sources' current versions."""
    va = _verified(db, va_id)
    verified.mark_reviewed(db, va, admin)
    audit.record(db, admin, "verified.reviewed", "verified_answer", va.id, question=va.question)
    db.commit()
    db.refresh(va)
    return verified.as_dict(va, _docs(db))


@router.delete("/verified/{va_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_verified(va_id: int, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    va = _verified(db, va_id)
    audit.record(db, admin, "verified.deleted", "verified_answer", va.id, question=va.question)
    db.delete(va)
    db.commit()
