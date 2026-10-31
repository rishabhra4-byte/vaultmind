import time
from collections import defaultdict, deque
from collections.abc import Hashable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .database import get_db
from .models import User
from .sessions import Auth, SessionInvalid, authenticate

bearer = HTTPBearer(auto_error=False)


def current_auth(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> Auth:
    """The signed-in person and their session. Every request checks the session is still open."""
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in", headers={"WWW-Authenticate": "Bearer"})
    try:
        return authenticate(db, creds.credentials)
    except SessionInvalid as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc), headers={"WWW-Authenticate": "Bearer"}) from None


def current_user(auth: Auth = Depends(current_auth)) -> User:
    return auth.user


def optional_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User | None:
    """The signed-in user if the request carries a valid token, otherwise None (never raises)."""
    if creds is None:
        return None
    try:
        return authenticate(db, creds.credentials, touch=False).user
    except SessionInvalid:
        return None


def admin_user(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrators only")
    return user


class RateLimiter:
    """Sliding-window limiter per key, in memory. Fine for one process; use Redis when scaling out."""

    def __init__(self, limit: int, window_seconds: float, message: str = "Slow down: try again in {retry}s") -> None:
        self.limit, self.window, self.message = limit, window_seconds, message
        self._hits: dict[Hashable, deque[float]] = defaultdict(deque)
        self._calls = 0

    def check(self, key: Hashable) -> None:
        """Count this attempt, refusing it if the key is already at the limit."""
        self.ensure_allowed(key)
        self.record(key)

    def ensure_allowed(self, key: Hashable) -> None:
        """Refuse with 429 if the key is at the limit, without counting this attempt."""
        now = time.monotonic()
        self._calls += 1
        if self._calls % 1000 == 0:
            self._prune(now)
        hits = self._hits[key]
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            retry = int(self.window - (now - hits[0])) + 1
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                self.message.format(retry=retry),
                headers={"Retry-After": str(retry)},
            )

    def record(self, key: Hashable) -> None:
        self._hits[key].append(time.monotonic())

    def _prune(self, now: float) -> None:
        """Forget keys with no hits inside the window, so memory doesn't grow with every new key."""
        for key in [k for k, hits in self._hits.items() if not hits or now - hits[-1] > self.window]:
            del self._hits[key]

    def __len__(self) -> int:
        return len(self._hits)

    def reset(self) -> None:
        self._hits.clear()


chat_limiter = RateLimiter(limit=20, window_seconds=60)
# Per account and per network address, so neither one password-guesser nor one target can be
# hammered. Only failed attempts count: a whole office signing in at 9am from one address
# (or through one proxy) must not lock itself out.
LOGIN_MESSAGE = "Too many sign-in attempts. Try again in {retry}s."
login_limiter_account = RateLimiter(limit=10, window_seconds=300, message=LOGIN_MESSAGE)
login_limiter_ip = RateLimiter(limit=50, window_seconds=300, message=LOGIN_MESSAGE)
