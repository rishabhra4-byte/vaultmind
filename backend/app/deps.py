import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .database import get_db
from .models import User
from .security import decode_access_token

bearer = HTTPBearer(auto_error=False)


def current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in", headers={"WWW-Authenticate": "Bearer"})
    user_id = decode_access_token(creds.credentials)
    user = db.get(User, user_id) if user_id else None
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired or account disabled")
    return user


def admin_user(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrators only")
    return user


class RateLimiter:
    """Sliding-window limiter per user, in memory. Fine for one process; use Redis when scaling out."""

    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit, self.window = limit, window_seconds
        self._hits: dict[int, deque[float]] = defaultdict(deque)

    def check(self, key: int) -> None:
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            retry = int(self.window - (now - hits[0])) + 1
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                f"Slow down: try again in {retry}s",
                headers={"Retry-After": str(retry)},
            )
        hits.append(now)

    def reset(self) -> None:
        self._hits.clear()


chat_limiter = RateLimiter(limit=20, window_seconds=60)
