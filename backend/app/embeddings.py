"""Optional search by meaning ("embeddings"): finds passages that say the same thing in other
words ("sign off" for "approve", "2 lakh" for "200000"), which word search can miss.

Off unless VAULTMIND_EMBEDDINGS_URL is set. Any service with an OpenAI-style
`POST {url}/embeddings` works: one run inside the company (Ollama, Hugging Face
text-embeddings-inference, vLLM), so no text leaves it, or a hosted one (Voyage AI, OpenAI).

What is sent follows the AI data policy (app/ai_policy.py), like everything sent to the AI
service: only passages of documents the AI may read, with ID numbers, card numbers and secrets
masked; and each question, as it's asked. Vectors of documents the AI may no longer read are
deleted.

How it's used: retrieval.rank adds a "means the same" score to its reranking, next to how
much of the question a passage covers. It can bring in a passage word search didn't find, but
it never decides on its own that there's enough evidence to answer: that stays with the words.

Vectors are computed in the background (the scheduler, or `python -m app.cli embed`), so
uploads never wait for the service; until a passage has one, it's found by words only. They're
kept in the database and in memory, and compared in plain Python (math.sumprod over float32
arrays): about 0.2 s per question for 20,000 passages of 1,024 dimensions. For much larger
libraries, choose a model with fewer dimensions.
"""

import array
import heapq
import logging
import math
import threading
from collections import OrderedDict

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from . import ai_policy
from .config import get_settings
from .models import ChunkEmbedding, Document, DocumentChunk
from .sensitivity import redact_for_ai

log = logging.getLogger(__name__)

BATCH = 32  # passages per request to the service
PER_RUN = 512  # passages embedded per background run, so one run never takes long
QUERY_TIMEOUT = 8.0  # seconds to wait for a question's vector before searching by words alone
MAX_PASSAGE_CHARS = 6000  # long passages are cut: most models read ~512 tokens anyway


class EmbeddingError(RuntimeError):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


def enabled() -> bool:
    s = get_settings()
    return bool(s.embeddings_url and s.embeddings_model)


class EmbeddingClient:
    """An OpenAI-style embeddings endpoint: {"model", "input": [texts]} -> {"data": [{"index", "embedding"}]}."""

    def __init__(self, url: str, model: str, api_key: str = "", timeout: float = 30.0) -> None:
        self.url = url.rstrip("/") + "/embeddings"
        self.model = model
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.http = httpx.Client(headers=headers, timeout=timeout)

    def embed(self, texts: list[str], timeout: float | None = None) -> list[list[float]]:
        if not texts:
            return []
        try:
            r = self.http.post(self.url, json={"model": self.model, "input": texts}, timeout=timeout or httpx.USE_CLIENT_DEFAULT)
        except httpx.TimeoutException as exc:
            raise EmbeddingError("timeout") from exc
        except httpx.HTTPError as exc:
            raise EmbeddingError("unreachable", type(exc).__name__) from exc
        if r.status_code in (401, 403):
            raise EmbeddingError("auth", f"HTTP {r.status_code}")
        if r.status_code == 429:
            raise EmbeddingError("rate_limited")
        if r.status_code >= 400:
            raise EmbeddingError("error", f"HTTP {r.status_code}")
        try:
            data = sorted(r.json()["data"], key=lambda d: d.get("index", 0))
            vectors = [[float(x) for x in d["embedding"]] for d in data]
        except (ValueError, KeyError, TypeError) as exc:
            raise EmbeddingError("bad_response") from exc
        if len(vectors) != len(texts) or any(not v for v in vectors) or len({len(v) for v in vectors}) != 1:
            raise EmbeddingError("bad_response", "wrong number or size of vectors")
        return vectors


_client: EmbeddingClient | None = None
_client_key: tuple | None = None
_client_lock = threading.Lock()


def client() -> EmbeddingClient | None:
    global _client, _client_key
    if not enabled():
        return None
    s = get_settings()
    key = (s.embeddings_url, s.embeddings_model, s.embeddings_api_key)
    with _client_lock:
        if _client is None or _client_key != key:
            _client, _client_key = EmbeddingClient(s.embeddings_url, s.embeddings_model, s.embeddings_api_key), key
        return _client


def _unit(vector: list[float]) -> array.array:
    norm = math.sqrt(math.sumprod(vector, vector)) or 1.0
    return array.array("f", (x / norm for x in vector))


class VectorIndex:
    """Unit-length vectors of passages, in memory, for the configured model."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._vectors: dict[int, array.array] = {}  # chunk id -> vector
        self._doc: dict[int, int] = {}  # chunk id -> document id
        self._by_doc: dict[int, set[int]] = {}

    def __len__(self) -> int:
        return len(self._vectors)

    def clear(self) -> None:
        with self._lock:
            self._vectors.clear()
            self._doc.clear()
            self._by_doc.clear()

    def add(self, chunk_id: int, doc_id: int, vector: array.array) -> None:
        with self._lock:
            self._vectors[chunk_id] = vector
            self._doc[chunk_id] = doc_id
            self._by_doc.setdefault(doc_id, set()).add(chunk_id)

    def keep_only(self, doc_id: int, chunk_ids: set[int]) -> None:
        """Forget this document's vectors except for these passages (all of them when empty)."""
        with self._lock:
            for cid in self._by_doc.get(doc_id, set()) - chunk_ids:
                self._vectors.pop(cid, None)
                self._doc.pop(cid, None)
            kept = self._by_doc.get(doc_id, set()) & chunk_ids
            if kept:
                self._by_doc[doc_id] = kept
            else:
                self._by_doc.pop(doc_id, None)

    def has_doc(self, doc_id: int) -> bool:
        return doc_id in self._by_doc

    def search(self, query: array.array, allowed_doc_ids: set[int], top_k: int) -> list[tuple[int, int, float]]:
        """(chunk id, document id, cosine similarity), best first, among `allowed_doc_ids` only."""
        with self._lock:
            pairs = [(cid, self._doc[cid]) for d in allowed_doc_ids for cid in self._by_doc.get(d, ())]
            scored = ((math.sumprod(query, self._vectors[cid]), cid, did) for cid, did in pairs)
            best = heapq.nlargest(top_k, scored)
        return [(cid, did, sim) for sim, cid, did in best]


vectors = VectorIndex()


# ---------------------------------------------------------------- questions


class _QueryCache:
    def __init__(self, size: int = 256) -> None:
        self.size = size
        self._items: OrderedDict[tuple[str, str], array.array] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                return self._items[key]
            return None

    def put(self, key, value) -> None:
        with self._lock:
            self._items[key] = value
            self._items.move_to_end(key)
            while len(self._items) > self.size:
                self._items.popitem(last=False)


_queries = _QueryCache()


def reset() -> None:
    """Forget cached question vectors and the client (tests, or after changing settings)."""
    global _client, _client_key
    _queries._items.clear()
    with _client_lock:
        _client, _client_key = None, None


def search(question: str, allowed_doc_ids: set[int], top_k: int = 30) -> list[tuple[int, int, float]]:
    """Passages that mean what `question` asks, among documents this person may open. Empty
    when meaning-based search is off, nothing has vectors yet, or the service fails (then the
    search is by words alone, and the failure is counted)."""
    from . import metrics

    c = client()
    if c is None or not len(vectors) or not any(vectors.has_doc(d) for d in allowed_doc_ids):
        return []
    s = get_settings()
    text = s.embeddings_query_prefix + redact_for_ai(question)
    key = (c.model, text)
    q = _queries.get(key)
    if q is None:
        try:
            q = _unit(c.embed([text], timeout=QUERY_TIMEOUT)[0])
        except EmbeddingError as exc:
            log.warning("Search by meaning skipped: %s", exc)
            metrics.inc("vaultmind_embedding_failures_total", reason=exc.reason, stage="question")
            return []
        _queries.put(key, q)
    return vectors.search(q, allowed_doc_ids, top_k)


# ---------------------------------------------------------------- passages


def _passage_text(doc: Document, chunk: DocumentChunk) -> str:
    return get_settings().embeddings_document_prefix + redact_for_ai(f"{doc.title}\n\n{chunk.text}")[:MAX_PASSAGE_CHARS]


def load(db: Session) -> int:
    """Fill the in-memory vectors from the database (at start-up)."""
    vectors.clear()
    if not enabled():
        return 0
    model = get_settings().embeddings_model
    rows = db.execute(
        select(ChunkEmbedding.chunk_id, ChunkEmbedding.vector, DocumentChunk.document_id, Document)
        .join(DocumentChunk, DocumentChunk.id == ChunkEmbedding.chunk_id)
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(ChunkEmbedding.model == model)
    )
    for chunk_id, blob, doc_id, doc in rows:
        if ai_policy.may_read(doc):
            v = array.array("f")
            v.frombytes(blob)
            vectors.add(chunk_id, doc_id, v)
    return len(vectors)


def forget_document(doc: Document) -> None:
    """Keep in memory only the vectors of passages the document still has, and none when the AI
    may no longer read it (called whenever a document is indexed)."""
    keep = {c.id for c in doc.chunks} if ai_policy.may_read(doc) else set()
    vectors.keep_only(doc.id, keep)


def embed_pending(db: Session, limit: int | None = PER_RUN) -> int:
    """Embed passages that have no vector for the configured model yet, for documents the AI
    may read; delete vectors of documents it may no longer read. Returns how many were embedded."""
    from . import metrics

    c = client()
    if c is None:
        return 0
    model = c.model
    docs = db.scalars(select(Document).options(selectinload(Document.chunks))).all()
    blocked = [d.id for d in docs if not ai_policy.may_read(d)]
    if blocked:
        chunk_ids = select(DocumentChunk.id).where(DocumentChunk.document_id.in_(blocked))
        db.execute(delete(ChunkEmbedding).where(ChunkEmbedding.chunk_id.in_(chunk_ids)))
        db.commit()
        for doc_id in blocked:
            vectors.keep_only(doc_id, set())
    have = {cid: m for cid, m in db.execute(select(ChunkEmbedding.chunk_id, ChunkEmbedding.model))}  # one row per passage
    todo = [(d, ch) for d in docs if ai_policy.may_read(d) for ch in d.chunks if have.get(ch.id) != model]
    if limit is not None:
        todo = todo[:limit]
    done = 0
    for i in range(0, len(todo), BATCH):
        batch = todo[i : i + BATCH]
        try:
            vecs = c.embed([_passage_text(d, ch) for d, ch in batch])
        except EmbeddingError as exc:
            log.warning("Embedding passages failed (%d done this run): %s", done, exc)
            metrics.inc("vaultmind_embedding_failures_total", reason=exc.reason, stage="passages")
            break
        for (d, ch), raw in zip(batch, vecs, strict=True):
            v = _unit(raw)
            row = db.get(ChunkEmbedding, ch.id)
            if row is None:
                db.add(ChunkEmbedding(chunk_id=ch.id, model=model, dims=len(v), vector=v.tobytes()))
            else:
                row.model, row.dims, row.vector = model, len(v), v.tobytes()
            vectors.add(ch.id, d.id, v)
        db.commit()
        done += len(batch)
    if done:
        log.info("Embedded %d passages for search by meaning", done)
    return done


def status(db: Session) -> dict:
    """For the admin overview: is it on, which model, how many passages have vectors."""
    if not enabled():
        return {"enabled": False}
    s = get_settings()
    from sqlalchemy import func

    docs = db.scalars(select(Document)).all()
    allowed = [d.id for d in docs if ai_policy.may_read(d)]
    total = db.scalar(select(func.count(DocumentChunk.id)).where(DocumentChunk.document_id.in_(allowed))) or 0
    done = db.scalar(
        select(func.count(ChunkEmbedding.chunk_id))
        .join(DocumentChunk, DocumentChunk.id == ChunkEmbedding.chunk_id)
        .where(DocumentChunk.document_id.in_(allowed), ChunkEmbedding.model == s.embeddings_model)
    )
    return {"enabled": True, "model": s.embeddings_model, "passages": total, "with_vectors": done or 0}
