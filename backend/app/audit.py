"""The audit log: append-only and hash-chained.

Every event gets a sequence number and a SHA-256 hash covering its content and the previous
event's hash. Editing, deleting or reordering any event breaks the chain from that point,
which `verify_chain` reports. The chain is extended when a transaction commits, under a lock
on its head row, so concurrent requests can't fork it.

A database administrator could still rewrite the whole chain from some point on. To catch
that too, the current head (sequence number and hash) is written to the application log once
a day and into every backup manifest: ship those somewhere the database admins can't edit,
and compare (see docs/security/overview.md).
"""

import hashlib
import json
import logging
from datetime import UTC, datetime

from sqlalchemy import event, func, insert, inspect, select, update
from sqlalchemy.orm import Session

from .models import AuditChain, AuditEvent, User, utcnow

log = logging.getLogger(__name__)

GENESIS = "0" * 64
_PENDING = "vaultmind_audit_pending"


def record(
    db: Session,
    actor: User | None,
    action: str,
    target_type: str = "",
    target_id: int | None = None,
    **detail,
) -> AuditEvent:
    """Append an audit event. The caller's transaction commits it together with the action itself."""
    event_ = AuditEvent(
        actor_id=actor.id if actor else None,
        action=action,
        target_type=target_type,
        target_id=target_id,
        # Stored exactly as it will read back from the database, so the hash stays reproducible.
        detail=json.loads(json.dumps(detail, default=str)),
        created_at=utcnow(),
    )
    db.add(event_)
    db.info.setdefault(_PENDING, []).append(event_)
    return event_


def _when(dt: datetime) -> str:
    if dt.tzinfo is None:  # SQLite hands back naive UTC
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")


def digest(prev: str, e: AuditEvent) -> str:
    body = {
        "seq": e.seq,
        "actor": e.actor_id,
        "action": e.action,
        "target_type": e.target_type or "",
        "target_id": e.target_id,
        "detail": e.detail or {},
        "at": _when(e.created_at),
    }
    payload = prev + json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode()).hexdigest()


@event.listens_for(Session, "before_flush")
def _collect(session: Session, flush_context, instances) -> None:
    """Events added without `record` (e.g. bulk demo data) are chained too, oldest first."""
    pending = session.info.setdefault(_PENDING, [])
    known = {id(e) for e in pending}
    extra = [o for o in session.new if isinstance(o, AuditEvent) and id(o) not in known]
    for e in extra:
        if e.created_at is None:
            e.created_at = utcnow()
    pending.extend(sorted(extra, key=lambda e: e.created_at))


@event.listens_for(Session, "before_commit")
def _seal(session: Session) -> None:
    """Give this transaction's new events their place in the chain, as it commits."""
    pending = session.info.get(_PENDING)
    if not pending:
        return
    session.flush()
    events = [e for e in pending if inspect(e).persistent and e.seq is None]
    session.info[_PENDING] = []
    if not events:
        return
    conn = session.connection()
    t = AuditChain.__table__
    bump = update(t).where(t.c.id == 1).values(seq=t.c.seq + len(events))
    if conn.execute(bump).rowcount == 0:  # the very first event of a new database
        conn.execute(insert(t).values(id=1, seq=0, head=GENESIS, base_seq=0, base_hash=GENESIS, verified_detail={}))
        conn.execute(bump)
    last_seq, prev = conn.execute(select(t.c.seq, t.c.head).where(t.c.id == 1)).one()
    for i, e in enumerate(events):
        e.seq = last_seq - len(events) + 1 + i
        e.prev_hash = prev
        e.hash = digest(prev, e)
        prev = e.hash
    conn.execute(update(t).where(t.c.id == 1).values(head=prev))


@event.listens_for(Session, "after_rollback")
def _forget(session: Session) -> None:
    session.info.pop(_PENDING, None)


def ensure_chain(db: Session) -> AuditChain:
    chain = db.get(AuditChain, 1)
    if chain is None:
        chain = AuditChain(id=1, seq=0, head=GENESIS, base_seq=0, base_hash=GENESIS, verified_detail={})
        db.add(chain)
        db.commit()
    return chain


def head(db: Session) -> dict:
    chain = db.get(AuditChain, 1)
    return {"seq": chain.seq if chain else 0, "hash": chain.head if chain else GENESIS}


def verify_chain(db: Session, *, save: bool = True) -> dict:
    """Recompute the whole chain. Returns {"ok", "events", "head_seq", "problem", "unsealed"}."""
    chain = db.get(AuditChain, 1)
    started = utcnow()
    result: dict = {"ok": True, "events": 0, "head_seq": 0, "problem": None, "unsealed": 0}
    if chain is not None:
        prev, expected, count, problem = chain.base_hash, chain.base_seq + 1, 0, None
        stmt = select(AuditEvent).where(AuditEvent.seq.is_not(None)).order_by(AuditEvent.seq).execution_options(yield_per=1000)
        for e in db.scalars(stmt):
            if e.seq != expected:
                kind = "missing" if e.seq > expected else "out_of_order"
                problem = {"seq": expected, "kind": kind}
                break
            if e.prev_hash != prev:
                problem = {"seq": e.seq, "kind": "broken_link"}
                break
            if digest(prev, e) != e.hash:
                problem = {"seq": e.seq, "kind": "modified"}
                break
            prev, expected, count = e.hash, expected + 1, count + 1
        if problem is None and expected - 1 != chain.seq:
            problem = {"seq": expected, "kind": "missing"}  # events at the end were removed
        elif problem is None and prev != chain.head:
            problem = {"seq": chain.seq, "kind": "head_mismatch"}
        unsealed = db.scalar(select(func.count(AuditEvent.id)).where(AuditEvent.seq.is_(None))) or 0
        result = {
            "ok": problem is None and unsealed == 0,
            "events": count,
            "head_seq": chain.seq,
            "problem": problem,
            "unsealed": unsealed,
        }
        if save:
            chain.verified_at, chain.verified_ok = utcnow(), result["ok"]
            chain.verified_detail = {**result, "seconds": round((utcnow() - started).total_seconds(), 3)}
            db.commit()
    return result


PROBLEMS = {
    "missing": "an event is missing (deleted)",
    "out_of_order": "events are out of order",
    "broken_link": "an event doesn't link to the one before it",
    "modified": "an event was changed after it was written",
    "head_mismatch": "the chain's head doesn't match its last event",
}


def describe(result: dict) -> str:
    if result["ok"]:
        return f"Intact: {result['events']} events checked, up to number {result['head_seq']}."
    if result.get("problem"):
        p = result["problem"]
        return f"Broken at event number {p['seq']}: {PROBLEMS.get(p['kind'], p['kind'])}."
    return f"{result['unsealed']} events were written outside the chain."


def purge_before(db: Session, cutoff: datetime, actor: User | None = None) -> int:
    """Delete the oldest events, up to the first one newer than `cutoff`, keeping the chain
    verifiable: its new starting point is the last deleted event. The caller commits."""
    first_kept = db.scalar(
        select(AuditEvent).where(AuditEvent.seq.is_not(None), AuditEvent.created_at >= cutoff).order_by(AuditEvent.seq).limit(1)
    )
    chain = db.get(AuditChain, 1)
    if chain is None:
        return 0
    if first_kept is None:
        return 0  # keep the latest events at least; nothing newer than the cutoff means a quiet log
    doomed = db.scalar(select(func.count(AuditEvent.id)).where(AuditEvent.seq < first_kept.seq)) or 0
    if not doomed:
        return 0
    db.execute(AuditEvent.__table__.delete().where(AuditEvent.seq < first_kept.seq))
    chain.base_seq, chain.base_hash = first_kept.seq - 1, first_kept.prev_hash
    record(db, actor, "audit.purged", "audit", None, events=doomed, before_seq=first_kept.seq)
    return doomed


def seal_line(db: Session) -> str:
    h = head(db)
    return f"audit-seal seq={h['seq']} head={h['hash']} at={_when(utcnow())}Z"
