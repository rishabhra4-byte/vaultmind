"""Background jobs, run in one thread inside the app process.

Every tick: sync sources that are due, send email/Slack copies of new notifications, and
(hourly) remind people whose temporary access is about to run out. VaultMind runs as a
single process (see the deployment notes), so one in-process scheduler is enough.
"""

import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from .database import SessionLocal

log = logging.getLogger(__name__)


def remind_expiring_grants(now: datetime | None = None) -> int:
    """Tell people two days before their temporary access to a document ends."""
    from .models import AccessGrant, Document
    from .notify import notify
    from .policy import grant_is_active

    now = now or datetime.now(UTC)
    soon = now + timedelta(days=2)
    sent = 0
    with SessionLocal() as db:
        for g in db.scalars(select(AccessGrant).where(AccessGrant.revoked.is_(False), AccessGrant.expires_at.is_not(None))):
            expires = g.expires_at if g.expires_at.tzinfo else g.expires_at.replace(tzinfo=UTC)
            if not grant_is_active(expires, g.revoked, now) or expires > soon:
                continue
            doc = db.get(Document, g.document_id)
            hours = max(1, round((expires - now).total_seconds() / 3600))
            when = f"in {hours} hour{'s' if hours != 1 else ''}" if hours < 48 else "in 2 days"
            sent += len(
                notify(
                    db,
                    [g.user_id],
                    kind="access.expiring",
                    title=f"Your access to “{doc.title}” ends {when}",
                    body="Ask for more time from the Access requests page if you still need it.",
                    link=f"/library/{doc.id}",
                    dedupe_key=f"access.expiring:{g.id}",
                )
            )
        db.commit()
    return sent


class Scheduler:
    def __init__(self, tick_seconds: float = 30.0) -> None:
        self.tick_seconds = tick_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_hourly: float | None = None  # monotonic time of the last hourly run

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="vaultmind-scheduler", daemon=True)
            self._thread.start()
            log.info("Background scheduler started")

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def _run(self) -> None:
        self._stop.wait(5)  # let the app finish starting
        while not self._stop.is_set():
            self.tick()
            self._stop.wait(self.tick_seconds)

    def tick(self) -> None:
        from .delivery import deliver_pending
        from .sync import due_sources, run_sync

        def job(name: str, fn: Callable[[], object]) -> None:
            try:
                fn()
            except Exception:
                log.exception("Background job %s failed", name)

        with SessionLocal() as db:
            due = due_sources(db)
        for source_id in due:
            if self._stop.is_set():
                return
            job(f"sync {source_id}", lambda sid=source_id: run_sync(sid))
        if self._last_hourly is None or time.monotonic() - self._last_hourly > 3600:
            self._last_hourly = time.monotonic()
            job("expiry reminders", remind_expiring_grants)
        job("deliveries", deliver_pending)
