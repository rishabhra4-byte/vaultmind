"""Company policies admins set in the app (Compliance → Controls and Retention).

Each policy has a default and limits. Values live in the `settings` table and are cached in
memory (VaultMind runs as one process); a change is visible to the next request. Callers
record changes in the audit log (see `update`).
"""

import threading
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Setting, User, utcnow
from .txn import after_commit

AI_LEVELS = ("none", "public", "internal", "confidential", "restricted")


@dataclass(frozen=True)
class Policy:
    default: object
    label: str
    minimum: int = 0
    maximum: int = 0
    choices: tuple[str, ...] = ()
    # For retention periods that must be long if they are set at all (0 still means "keep").
    floor_if_set: int = 0

    @property
    def kind(self) -> type:
        return type(self.default)


POLICIES: dict[str, Policy] = {
    # Sessions
    "session_idle_minutes": Policy(60, "Sign people out after this many minutes without activity (0: never)", 0, 1440),
    # AI
    "max_ai_classification": Policy(
        "confidential", "The most sensitive classification the AI service may read", choices=AI_LEVELS
    ),
    "ai_paused": Policy(False, "AI processing stopped by an administrator"),
    # Synced sharing
    "sync_freshness_hours": Policy(
        24, "Close mirrored files whose sharing couldn't be confirmed for this many hours (0: never)", 0, 720
    ),
    # Retention (days; 0 keeps forever)
    "retention_conversations_days": Policy(0, "Delete assistant conversations after", 0, 3650),
    "retention_notifications_days": Policy(180, "Delete notifications after", 0, 3650),
    "retention_ai_log_days": Policy(0, "Delete the record of what was sent to the AI service after", 0, 3650),
    "retention_audit_days": Policy(0, "Delete audit log events after", 0, 3650, floor_if_set=365),
    "retention_sessions_days": Policy(30, "Delete records of ended sessions after", 1, 3650),
    "retention_versions_days": Policy(0, "Delete earlier versions of documents after", 0, 3650),
    # Security alerts (0 turns an alert off)
    "alert_downloads_per_hour": Policy(50, "Alert when one person downloads more than this many files in an hour", 0, 10000),
    "alert_denials_per_10min": Policy(20, "Alert when one person is refused more than this many times in 10 minutes", 0, 10000),
    "alert_failed_signins_per_15min": Policy(
        10, "Alert when an account has more than this many failed sign-ins in 15 minutes", 0, 10000
    ),
}

_cache: dict[str, object] | None = None
_lock = threading.Lock()


class PolicyError(ValueError):
    pass


def _load() -> dict[str, object]:
    from .database import SessionLocal

    values = {k: p.default for k, p in POLICIES.items()}
    with SessionLocal() as db:
        for row in db.scalars(select(Setting)):
            if row.key in POLICIES:
                values[row.key] = (row.value or {}).get("value", POLICIES[row.key].default)
    return values


def all_values() -> dict[str, object]:
    global _cache
    with _lock:
        if _cache is None:
            _cache = _load()
        return dict(_cache)


def get(key: str):
    return all_values()[key]


def reset_cache() -> None:
    global _cache
    with _lock:
        _cache = None


def validate(key: str, value) -> object:
    policy = POLICIES.get(key)
    if policy is None:
        raise PolicyError(f"Unknown policy: {key}")
    if policy.kind is bool:
        if not isinstance(value, bool):
            raise PolicyError(f"{key} must be true or false")
        return value
    if policy.kind is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise PolicyError(f"{key} must be a whole number")
        if not policy.minimum <= value <= policy.maximum:
            raise PolicyError(f"{key} must be between {policy.minimum} and {policy.maximum}")
        if value and policy.floor_if_set and value < policy.floor_if_set:
            raise PolicyError(f"{key} must be 0 (keep forever) or at least {policy.floor_if_set}")
        return value
    if value not in policy.choices:
        raise PolicyError(f"{key} must be one of: {', '.join(policy.choices)}")
    return value


def update(db: Session, changes: dict, actor: User | None) -> dict[str, tuple]:
    """Validate and store changes. Returns {key: (before, after)} for what actually changed.

    The cache is refreshed once the caller's transaction commits.
    """
    before = all_values()
    checked = {k: validate(k, v) for k, v in changes.items()}
    changed: dict[str, tuple] = {}
    for key, value in checked.items():
        if before[key] == value:
            continue
        row = db.get(Setting, key)
        if row is None:
            row = Setting(key=key)
            db.add(row)
        row.value = {"value": value}
        row.updated_by = actor.id if actor else None
        row.updated_at = utcnow()
        changed[key] = (before[key], value)
    if changed:
        after_commit(db, reset_cache)
    return changed
