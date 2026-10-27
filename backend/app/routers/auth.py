from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..database import get_db
from ..deps import current_user, login_limiter_account, login_limiter_ip
from ..models import User
from ..schemas import LoginIn, TokenOut, UserOut
from ..security import DUMMY_HASH, create_access_token, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    login_limiter_ip.check(request.client.host if request.client else "unknown")
    login_limiter_account.check(email)
    user = db.scalar(select(User).where(func.lower(User.email) == email))
    # Always run bcrypt, so the response time doesn't reveal whether the account exists.
    password_ok = verify_password(body.password, user.password_hash if user else DUMMY_HASH)
    if user is None or not password_ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account is disabled")
    audit.record(db, user, "auth.login", "user", user.id)
    db.commit()
    return TokenOut(access_token=create_access_token(user.id), user=UserOut.of(user))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return UserOut.of(user)
