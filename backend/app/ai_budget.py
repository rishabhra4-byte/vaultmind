"""Limits on AI use: for a public demo, and for keeping the AI bill predictable.

- VAULTMIND_AI_DAILY_LIMIT: calls to the AI service per day (UTC), company-wide. Every call
  counts: answers, summaries at upload, descriptions of changes, rephrased searches.
- VAULTMIND_AI_DAILY_LIMIT_PER_PERSON: answers written by the AI per person per day (UTC),
  counted from the activity log, so deleting conversations doesn't reset it.

0 means no limit (the default). Past a limit, answers are still given, built offline from the
matching passages, and say why. The company-wide count is kept in the app process and starts
again if it restarts; the AI provider's own spending limit is the backstop.
"""

import threading
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import AuditEvent, User


class DailyBudget:
    """At most `limit` uses per UTC day, in this process."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._day: date | None = None
        self._used = 0
        self._lock = threading.Lock()

    def take(self, today: date | None = None) -> bool:
        today = today or datetime.now(UTC).date()
        with self._lock:
            if today != self._day:
                self._day, self._used = today, 0
            if self._used >= self.limit:
                return False
            self._used += 1
            return True

    @property
    def used(self) -> int:
        return self._used if self._day == datetime.now(UTC).date() else 0


class BudgetedLLM:
    """Wraps the AI client so that, past the company-wide daily limit, calls fail with the
    reason "budget" and the assistant answers offline instead."""

    def __init__(self, inner, budget: DailyBudget) -> None:
        self.inner = inner
        self.budget = budget
        self.name = inner.name

    def _check(self) -> None:
        from .llm import LLMError

        if not self.budget.take():
            raise LLMError("budget", f"daily limit of {self.budget.limit} AI calls reached")

    def complete(self, system: str, messages: list[dict], max_tokens: int = 1024, *, timeout: float | None = None) -> str:
        self._check()
        return self.inner.complete(system, messages, max_tokens, timeout=timeout)

    def stream(self, system: str, messages: list[dict], max_tokens: int = 1024):
        self._check()
        yield from self.inner.stream(system, messages, max_tokens)


def answers_today(db: Session, user: User) -> int:
    """AI-written answers this person got today (UTC), from the activity log."""
    midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    details = db.scalars(
        select(AuditEvent.detail).where(
            AuditEvent.action == "assistant.query", AuditEvent.actor_id == user.id, AuditEvent.created_at >= midnight
        )
    )
    return sum(1 for d in details if (d or {}).get("mode") == "ai")


def person_may_use_ai(db: Session, user: User) -> bool:
    limit = get_settings().ai_daily_limit_per_person
    return limit <= 0 or answers_today(db, user) < limit
