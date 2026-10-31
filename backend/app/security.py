from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from .config import get_settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


# Compared against when the email doesn't exist, so a failed login takes the same time
# whether or not the account exists (no user enumeration by timing).
DUMMY_HASH = bcrypt.hashpw(b"not-a-real-password", bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


def create_access_token(user_id: int, session_id: str, expires_at: datetime | None = None) -> str:
    """A signed token naming the person and their session (see sessions.py)."""
    s = get_settings()
    now = datetime.now(UTC)
    exp = expires_at or now + timedelta(minutes=s.jwt_expire_minutes)
    payload = {"sub": str(user_id), "sid": session_id, "iat": now, "exp": exp}
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm)


def decode_access_token(token: str) -> tuple[int, str] | None:
    """(user id, session id) from a valid token. Tokens without a session id (from before
    sessions could be ended) are refused, so their holders sign in again."""
    s = get_settings()
    try:
        payload = jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])
        session_id = payload["sid"]
        if not isinstance(session_id, str) or not session_id:
            return None
        return int(payload["sub"]), session_id
    except (jwt.PyJWTError, KeyError, ValueError, TypeError):
        return None
