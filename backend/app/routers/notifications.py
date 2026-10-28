from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import current_user
from ..models import Notification, User, utcnow
from ..schemas import NotificationList, NotificationOut

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("", response_model=NotificationList)
def list_notifications(user: User = Depends(current_user), db: Session = Depends(get_db)):
    items = db.scalars(
        select(Notification).where(Notification.user_id == user.id).order_by(Notification.id.desc()).limit(50)
    ).all()
    unread = db.scalar(select(func.count(Notification.id)).where(Notification.user_id == user.id, Notification.read_at.is_(None)))
    return NotificationList(unread=unread or 0, items=[NotificationOut.model_validate(n) for n in items])


class ReadIn(BaseModel):
    ids: list[int] | None = Field(None, description="Leave out to mark everything as read")


@router.post("/read", response_model=NotificationList)
def mark_read(body: ReadIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    stmt = update(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None))
    if body.ids is not None:
        stmt = stmt.where(Notification.id.in_(body.ids))
    db.execute(stmt.values(read_at=utcnow()))
    db.commit()
    return list_notifications(user, db)
