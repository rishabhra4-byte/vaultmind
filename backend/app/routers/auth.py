from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..database import get_db
from ..deps import current_user
from ..models import User
from ..schemas import LoginIn, TokenOut, UserOut
from ..security import create_access_token, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(func.lower(User.email) == body.email.strip().lower()))
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account is disabled")
    audit.record(db, user, "auth.login", "user", user.id)
    db.commit()
    return TokenOut(access_token=create_access_token(user.id), user=UserOut.of(user))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return UserOut.of(user)
