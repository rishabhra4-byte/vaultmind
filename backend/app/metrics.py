"""Prometheus metrics and request IDs.

GET /metrics serves the Prometheus text format when VAULTMIND_METRICS_TOKEN is set; scrapers
send it as a bearer token. Without a token the endpoint doesn't exist. Counters live in this
process (VaultMind runs one process; see docs/production-readiness.md), so they restart from
zero with it, which Prometheus' rate() handles.

Every response carries an X-Request-ID (the caller's, if it sent a sensible one), and every log
line written while handling the request includes it, so a person's error report can be matched
to the server log.

Alert rules for these metrics are in deploy/prometheus-alerts.yml.
"""

import contextvars
import logging
import re
import threading
import time
import uuid
from collections import defaultdict
from collections.abc import Iterable

request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
_VALID_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")

# Seconds; covers fast API calls up to slow AI answers.
BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)
STARTED = time.time()


def new_request_id(incoming: str | None) -> str:
    return incoming if incoming and _VALID_ID.match(incoming) else uuid.uuid4().hex


class RequestIdFilter(logging.Filter):
    """Adds `request_id` to every log record (\"-\" outside a request)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id.get()
        return True


class Registry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, dict[tuple, float]] = defaultdict(lambda: defaultdict(float))
        self._hist: dict[str, dict[tuple, list[float]]] = defaultdict(dict)  # bucket counts + sum + count
        self._help: dict[str, tuple[str, str]] = {}

    def describe(self, name: str, kind: str, help_text: str) -> None:
        self._help[name] = (kind, help_text)

    def inc(self, name: str, labels: dict[str, str] | None = None, value: float = 1.0) -> None:
        key = tuple(sorted((labels or {}).items()))
        with self._lock:
            self._counters[name][key] += value

    def observe(self, name: str, seconds: float, labels: dict[str, str] | None = None) -> None:
        key = tuple(sorted((labels or {}).items()))
        with self._lock:
            row = self._hist[name].setdefault(key, [0.0] * (len(BUCKETS) + 2))
            for i, bound in enumerate(BUCKETS):
                if seconds <= bound:
                    row[i] += 1
            row[-2] += seconds
            row[-1] += 1

    def value(self, name: str, labels: dict[str, str] | None = None) -> float:
        return self._counters.get(name, {}).get(tuple(sorted((labels or {}).items())), 0.0)

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._hist.clear()

    def render(self, gauges: Iterable[tuple[str, str, dict[str, str], float]] = ()) -> str:
        out: list[str] = []
        with self._lock:
            names = sorted(set(self._counters) | set(self._hist) | set(self._help))
            for name in names:
                kind, help_text = self._help.get(name, ("counter", ""))
                if name in self._hist:
                    out += [f"# HELP {name} {help_text}", f"# TYPE {name} histogram"]
                    for key, row in sorted(self._hist[name].items()):
                        for i, bound in enumerate(BUCKETS):
                            out.append(f"{name}_bucket{_labels(dict(key), le=_num(bound))} {_num(row[i])}")
                        out.append(f"{name}_bucket{_labels(dict(key), le='+Inf')} {_num(row[-1])}")
                        out.append(f"{name}_sum{_labels(dict(key))} {row[-2]:.6f}")
                        out.append(f"{name}_count{_labels(dict(key))} {_num(row[-1])}")
                elif kind == "counter":
                    out += [f"# HELP {name} {help_text}", f"# TYPE {name} counter"]
                    for key, v in sorted(self._counters.get(name, {}).items()):
                        out.append(f"{name}{_labels(dict(key))} {_num(v)}")
        by_name: dict[str, list[tuple[str, dict[str, str], float]]] = defaultdict(list)
        for name, help_text, labels, v in gauges:
            by_name[name].append((help_text, labels, v))
        for name, rows in by_name.items():
            out += [f"# HELP {name} {rows[0][0]}", f"# TYPE {name} gauge"]
            out += [f"{name}{_labels(labels)} {_num(v)}" for _, labels, v in rows]
        return "\n".join(out) + "\n"


def _num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else repr(float(v))


def _escape(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _labels(labels: dict[str, str], **extra: str) -> str:
    items = {**labels, **extra}
    if not items:
        return ""
    return "{" + ",".join(f'{k}="{_escape(v)}"' for k, v in items.items()) + "}"


registry = Registry()
for _name, _kind, _help in (
    ("vaultmind_http_requests_total", "counter", "HTTP requests by method, route and status class."),
    ("vaultmind_http_request_duration_seconds", "histogram", "Time to answer HTTP requests, by route."),
    ("vaultmind_sign_ins_total", "counter", "Sign-in attempts by method and result."),
    ("vaultmind_answers_total", "counter", "Assistant answers by mode (ai, offline, direct) and whether evidence was enough."),
    ("vaultmind_ai_fallbacks_total", "counter", "Answers that fell back to offline because the AI service failed, by reason."),
    ("vaultmind_access_denied_total", "counter", "Refused document opens and downloads."),
    ("vaultmind_files_blocked_total", "counter", "Files refused by the malware scanner, by where they came from."),
    ("vaultmind_scan_failures_total", "counter", "Files refused because the malware scanner couldn't scan them."),
    ("vaultmind_syncs_total", "counter", "Source syncs by result."),
    ("vaultmind_backups_total", "counter", "Scheduled backups by result (verified, check_failed, failed)."),
):
    registry.describe(_name, _kind, _help)


def inc(name: str, **labels: str) -> None:
    registry.inc(name, labels)


# Audit actions that are also counted (audit.record calls `count_audit`).
_AUDITED = {
    "document.upload_blocked": ("vaultmind_files_blocked_total", {"source": "upload"}),
    "document.sync_blocked": ("vaultmind_files_blocked_total", {"source": "sync"}),
    "backup.created": ("vaultmind_backups_total", {"result": "verified"}),
    "backup.check_failed": ("vaultmind_backups_total", {"result": "check_failed"}),
    "backup.failed": ("vaultmind_backups_total", {"result": "failed"}),
}


def count_audit(action: str, detail: dict) -> None:
    if action.endswith("_denied"):
        registry.inc("vaultmind_access_denied_total", {"action": action})
    elif action == "auth.login":
        registry.inc("vaultmind_sign_ins_total", {"method": str(detail.get("method", "password")), "result": "ok"})
    elif action in _AUDITED:
        name, labels = _AUDITED[action]
        registry.inc(name, labels)


def gauges() -> list[tuple[str, str, dict[str, str], float]]:
    """Values read when Prometheus scrapes: is the database up, how many documents and open
    sessions, is the scanner reachable, how old is the newest verified backup."""
    from datetime import UTC, datetime
    from pathlib import Path

    from sqlalchemy import func, select, text

    from . import backup, malware
    from .config import get_settings
    from .database import SessionLocal
    from .models import Document, Source, User, UserSession
    from .search import index

    s = get_settings()
    out: list[tuple[str, str, dict[str, str], float]] = [
        ("vaultmind_start_time_seconds", "When this process started (Unix time).", {}, STARTED),
        ("vaultmind_search_index_documents", "Documents in the in-memory search index.", {}, float(len(index))),
        ("vaultmind_ai_configured", "1 when an AI service is configured.", {}, float(bool(s.anthropic_api_key))),
    ]
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
            now = datetime.now(UTC)

            def count(stmt) -> float:
                return float(db.scalar(stmt) or 0)

            out += [
                ("vaultmind_database_up", "1 when the database answers.", {}, 1.0),
                ("vaultmind_documents", "Documents stored.", {}, count(select(func.count(Document.id)))),
                ("vaultmind_people_active", "Active accounts.", {}, count(select(func.count(User.id)).where(User.is_active))),
                (
                    "vaultmind_sessions_open",
                    "Signed-in sessions that haven't ended or expired.",
                    {},
                    count(select(func.count()).where(UserSession.ended_at.is_(None), UserSession.expires_at > now)),
                ),
                (
                    "vaultmind_sources_failing",
                    "Connected sources whose last sync failed.",
                    {},
                    count(select(func.count(Source.id)).where(Source.status == "error", Source.enabled)),
                ),
            ]
    except Exception:
        out.append(("vaultmind_database_up", "1 when the database answers.", {}, 0.0))
    out.append(("vaultmind_scanner_configured", "1 when a ClamAV address is set.", {}, float(bool(s.clamav_address))))
    if s.clamav_address:
        out.append(("vaultmind_scanner_up", "1 when ClamAV answers.", {}, float(malware.ping())))
    out.append(("vaultmind_backup_configured", "1 when scheduled backups are on.", {}, float(bool(s.backup_dir))))
    if s.backup_dir:
        recent = backup.status(Path(s.backup_dir))
        verified = [b for b in recent if b["verified"]]
        if verified:
            when = datetime.fromisoformat(verified[0]["created_at"]).timestamp()
            out.append(("vaultmind_backup_last_success_timestamp_seconds", "When the newest verified backup was made.", {}, when))
        if recent:
            out.append(
                (
                    "vaultmind_backup_last_verified",
                    "1 when the newest backup passed its restore check.",
                    {},
                    float(bool(recent[0]["verified"])),
                )
            )
    return out


def render() -> str:
    return registry.render(gauges())
