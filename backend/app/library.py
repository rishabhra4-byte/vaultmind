"""The library map: where the company's documents are, what takes up room, and what to clean up.

Modelled on disk-usage tools such as WinDirStat, for a document library rather than a disk:

- every document with its size (bytes, and passages: how much the assistant has to search),
  its file type, team, source and folder, so the page can draw a treemap and folder list;
- file types with how many documents and bytes each has;
- duplicates: the same file more than once (same SHA-256), and documents whose text is almost
  the same (an old copy and a new one, or a PDF and the Word file it came from);
- clean-up suggestions: extra copies, old documents people still rely on, and documents
  nobody has opened or got in an answer for a while.

Duplicates matter more here than on a disk. Files are stored by content, so a second copy of
the same file takes no extra room; the cost is in answers: search shows both, and when one copy
is updated and the other isn't, answers can quote the outdated one.

Near-duplicates are found with a bottom-k sketch of each document's text (the 64 smallest
hashes of its five-word runs), kept in memory next to the search index and rebuilt with it.
Two sketches estimate how much text two documents share without comparing them word by word.
Administrators only: it lists every document's title.
"""

import gzip
import hashlib
import heapq
import json
import re
import threading
from array import array
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import PurePath

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .models import AuditEvent, Document, DocumentChunk, Source, Team

# ---------------------------------------------------------------- file types

KINDS: dict[str, str] = {
    "pdf": "PDF",
    "word": "Word",
    "sheet": "Spreadsheet",
    "slides": "Slides",
    "markdown": "Markdown",
    "text": "Plain text",
    "web": "Web page",
    "data": "Data",
    "other": "Other",
}
_BY_EXTENSION = {
    ".pdf": "pdf",
    ".docx": "word",
    ".doc": "word",
    ".odt": "word",
    ".rtf": "word",
    ".xlsx": "sheet",
    ".xls": "sheet",
    ".ods": "sheet",
    ".csv": "sheet",
    ".tsv": "sheet",
    ".pptx": "slides",
    ".ppt": "slides",
    ".odp": "slides",
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "text",
    ".log": "text",
    ".html": "web",
    ".htm": "web",
    ".json": "data",
    ".yaml": "data",
    ".yml": "data",
    ".xml": "data",
}


def kind_of(filename: str, mime_type: str = "") -> str:
    kind = _BY_EXTENSION.get(PurePath(filename).suffix.lower())
    if kind:
        return kind
    if mime_type == "application/pdf":
        return "pdf"
    if mime_type.startswith("text/"):
        return "text"
    return "other"


# ---------------------------------------------------------------- near-duplicate sketches

K = 64  # hashes kept per document
SHINGLE = 5  # words per run
MIN_WORDS = 40  # shorter documents (stubs, one-liners) aren't compared: too easy to match by accident
NEAR = 0.8  # estimated share of text two documents need to count as almost the same
COMMON = 50  # a run found in more documents than this (a standard footer) doesn't make a pair a candidate
_WORD = re.compile(r"\w+")
_lock = threading.Lock()
_sketches: dict[int, array] = {}


def sketch(text: str) -> array | None:
    """The K smallest hashes of the text's five-word runs, or None for very short texts."""
    words = _WORD.findall(text.lower())
    if len(words) < MIN_WORDS:
        return None
    runs = {
        int.from_bytes(hashlib.blake2b(" ".join(words[i : i + SHINGLE]).encode(), digest_size=8).digest(), "big")
        for i in range(len(words) - SHINGLE + 1)
    }
    return array("Q", sorted(heapq.nsmallest(K, runs)))


def remember(doc_id: int, texts: list[str]) -> None:
    """Keep a document's sketch (called whenever it's indexed)."""
    s = sketch("\n".join(texts))
    with _lock:
        if s is None:
            _sketches.pop(doc_id, None)
        else:
            _sketches[doc_id] = s


def forget(doc_id: int | None = None) -> None:
    """Drop one document's sketch, or all of them (when the index is rebuilt)."""
    with _lock:
        if doc_id is None:
            _sketches.clear()
        else:
            _sketches.pop(doc_id, None)


def resemblance(a: array, b: array) -> float:
    """Estimated share of text two documents have in common (Jaccard similarity of their runs):
    of the K smallest hashes of both together, the fraction found in each."""
    sa, sb = set(a), set(b)
    smallest = heapq.nsmallest(K, sa | sb)
    return sum(1 for h in smallest if h in sa and h in sb) / len(smallest) if smallest else 0.0


def near_pairs(doc_ids: set[int] | None = None) -> list[tuple[int, int, float]]:
    """Pairs of documents whose text is almost the same, as (lower id, higher id, resemblance)."""
    with _lock:
        sketches = {d: s for d, s in _sketches.items() if doc_ids is None or d in doc_ids}
    holders: dict[int, list[int]] = defaultdict(list)
    for d, s in sketches.items():
        for h in s:
            holders[h].append(d)
    shared: dict[tuple[int, int], int] = defaultdict(int)
    for docs in holders.values():
        if 1 < len(docs) <= COMMON:
            docs.sort()
            for i, a in enumerate(docs):
                for b in docs[i + 1 :]:
                    shared[(a, b)] += 1
    # Two documents sharing NEAR of their text share at least about that much of their sketches.
    need = max(2, int(K * NEAR * 0.5))
    pairs = []
    for (a, b), n in shared.items():
        if n >= need:
            r = resemblance(sketches[a], sketches[b])
            if r >= NEAR:
                pairs.append((a, b, r))
    return sorted(pairs)


# ---------------------------------------------------------------- the map

LIMIT = 20000  # documents sent to draw; the largest ones, if a library has more
OLD_DAYS = 365  # "old": not updated for a year


def _aware(when: datetime) -> datetime:
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def _groups(pairs: list[tuple[int, int]]) -> list[list[int]]:
    """Connected groups from pairs (a and b alike, b and c alike: one group of three)."""
    parent: dict[int, int] = {}

    def root(x: int) -> int:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in pairs:
        parent[root(a)] = root(b)
    out: dict[int, list[int]] = defaultdict(list)
    for x in list(parent):
        out[root(x)].append(x)
    return [sorted(g) for g in out.values()]


def usage(db: Session, days: int) -> tuple[dict[int, int], dict[int, int]]:
    """How many times each document was opened (or downloaded), and cited in an answer."""
    since = datetime.now(UTC) - timedelta(days=days)
    opens = dict(
        db.execute(
            select(AuditEvent.target_id, func.count())
            .where(AuditEvent.action.in_(("document.view", "document.download")), AuditEvent.created_at >= since)
            .group_by(AuditEvent.target_id)
        ).all()
    )
    cited: dict[int, int] = defaultdict(int)
    for detail in db.scalars(
        select(AuditEvent.detail).where(AuditEvent.action == "assistant.query", AuditEvent.created_at >= since)
    ):
        for d in set((detail or {}).get("cited") or []):
            if isinstance(d, int):
                cited[d] += 1
    return {k: v for k, v in opens.items() if k is not None}, dict(cited)


def overview(db: Session, days: int = 90, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    docs = list(db.scalars(select(Document).options(selectinload(Document.owner_team), selectinload(Document.source))))
    passages = dict(db.execute(select(DocumentChunk.document_id, func.count()).group_by(DocumentChunk.document_id)).all())
    opens, cited = usage(db, days)
    by_id = {d.id: d for d in docs}

    # Duplicates: the same file, then text that's almost the same.
    by_hash: dict[str, list[int]] = defaultdict(list)
    for d in docs:
        by_hash[d.sha256].append(d.id)
    duplicates = []
    for ids in by_hash.values():
        if len(ids) > 1:
            duplicates.append({"kind": "same_file", "similarity": 1.0, "documents": sorted(ids)})
    near = [(a, b, r) for a, b, r in near_pairs(set(by_id)) if by_id[a].sha256 != by_id[b].sha256]
    best = {(a, b): r for a, b, r in near}
    for group in _groups([(a, b) for a, b, _ in near]):
        scores = [r for (a, b), r in best.items() if a in group and b in group]
        duplicates.append({"kind": "same_text", "similarity": round(min(scores), 2), "documents": group})
    duplicates.sort(key=lambda g: (-len(g["documents"]), -sum(by_id[i].size for i in g["documents"])))
    in_group: dict[int, int] = {}
    for n, g in enumerate(duplicates):
        for i in g["documents"]:
            in_group.setdefault(i, n)

    def used(d: Document) -> int:
        return opens.get(d.id, 0) + cited.get(d.id, 0)

    cleanup = []
    for g in duplicates:
        group = [by_id[i] for i in g["documents"]]
        # Keep the copy people use most; among equals, the newest (a synced one, if any: it's
        # the one kept up to date at its source).
        keep = max(group, key=lambda d: (used(d), d.source_id is not None, _aware(d.updated_at), -d.id))
        for d in group:
            if d is keep:
                continue
            if g["kind"] == "same_file":
                why = f"The same file as “{keep.title}”. Search shows both, and if one is updated, answers can quote the other."
            else:
                older = _aware(d.updated_at) <= _aware(keep.updated_at)
                pct = round(best.get((min(d.id, keep.id), max(d.id, keep.id)), g["similarity"]) * 100)
                why = (
                    f"Almost the same text as “{keep.title}” (about {pct}% the same)"
                    f"{', and older' if older else ''}. Answers can quote either; keep the right one."
                )
            cleanup.append({"kind": g["kind"], "doc_id": d.id, "keep_id": keep.id, "why": why})
    flagged = {c["doc_id"] for c in cleanup}
    old_before = now - timedelta(days=OLD_DAYS)
    unused_before = now - timedelta(days=days)
    for d in sorted(docs, key=lambda d: (-used(d), _aware(d.updated_at))):
        if d.id in flagged:
            continue
        updated = _aware(d.updated_at)
        if updated < old_before and used(d) > 0:
            months = max(12, (now - updated).days // 30)
            uses = []
            if opens.get(d.id):
                uses.append(f"opened {_times(opens[d.id])}")
            if cited.get(d.id):
                uses.append(f"used in {_answers(cited[d.id])}")
            cleanup.append(
                {
                    "kind": "old_but_used",
                    "doc_id": d.id,
                    "why": f"Not updated for {months} months, but {' and '.join(uses)} in the last {days} days. "
                    "Check it's still right.",
                }
            )
    for d in sorted(docs, key=lambda d: -d.size):
        if d.id in flagged or used(d) or _aware(d.created_at) >= unused_before:
            continue
        cleanup.append(
            {
                "kind": "unused",
                "doc_id": d.id,
                "why": f"Nobody opened it or got it in an answer in the last {days} days.",
            }
        )

    kinds: dict[str, dict] = {}
    for d in docs:
        k = kinds.setdefault(kind_of(d.filename, d.mime_type), {"documents": 0, "bytes": 0, "passages": 0})
        k["documents"] += 1
        k["bytes"] += d.size
        k["passages"] += passages.get(d.id, 0)

    shown = sorted(docs, key=lambda d: (-d.size, d.id))[:LIMIT]
    return {
        "generated_at": now.isoformat(),
        "days": days,
        "totals": {
            "documents": len(docs),
            "bytes": sum(d.size for d in docs),
            "passages": sum(passages.values()),
            "compared": sum(1 for d in docs if d.id in _sketches),
        },
        "truncated": len(docs) > LIMIT,
        "kinds": sorted(({"kind": k, "label": KINDS[k], **v} for k, v in kinds.items()), key=lambda k: (-k["bytes"], k["kind"])),
        "teams": [{"id": t.id, "name": t.name} for t in db.scalars(select(Team).order_by(Team.name))],
        "sources": [{"id": s.id, "name": s.name, "kind": s.kind} for s in db.scalars(select(Source).order_by(Source.name))],
        "documents": [
            {
                "id": d.id,
                "title": d.title,
                "kind": kind_of(d.filename, d.mime_type),
                "bytes": d.size,
                "passages": passages.get(d.id, 0),
                "classification": d.classification,
                "team_id": d.owner_team_id,
                "source_id": d.source_id,
                "folder": (d.source_path or "").strip("/"),
                "created_at": _aware(d.created_at).isoformat(),
                "updated_at": _aware(d.updated_at).isoformat(),
                "opens": opens.get(d.id, 0),
                "cited": cited.get(d.id, 0),
                "duplicate": in_group.get(d.id),
            }
            for d in shown
        ],
        "duplicates": duplicates,
        "cleanup": cleanup,
    }


def _times(n: int) -> str:
    return "once" if n == 1 else "twice" if n == 2 else f"{n} times"


def _answers(n: int) -> str:
    return "an answer" if n == 1 else f"{n} answers"


def encode(payload: dict, accept_encoding: str) -> tuple[bytes, dict[str, str]]:
    """JSON, gzipped when the browser accepts it: a large library is a few megabytes of JSON.
    (Done here rather than for the whole app, so streamed answers are never buffered.)"""
    body = json.dumps(payload, separators=(",", ":")).encode()
    headers = {"Cache-Control": "no-store", "Vary": "Accept-Encoding"}
    if "gzip" in accept_encoding.lower() and len(body) > 1024:
        body = gzip.compress(body, compresslevel=5)
        headers["Content-Encoding"] = "gzip"
    return body, headers
