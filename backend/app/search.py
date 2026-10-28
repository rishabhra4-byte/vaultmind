"""In-memory BM25 index over document chunks.

Why BM25 and not embeddings? It needs no model download or external service, it's fast,
explainable, and strong on the keyword-heavy queries people type when looking for a
company document. The index sits behind a small interface (add / remove / search), so an
embedding or hybrid retriever can replace it without touching the rest of the app.

Permission filtering happens *before* ranking: `search` only scores chunks whose
document id is in `allowed_doc_ids`, so text from documents a user can't open never
reaches the assistant.
"""

import math
import threading
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from .text import tokenize

K1 = 1.5
B = 0.75
TITLE_WEIGHT = 3  # title and tag terms count this many times in every chunk of the doc


@dataclass
class ChunkHit:
    chunk_id: int
    doc_id: int
    score: float
    text: str
    ordinal: int = 0  # position of the passage in its document, for citing a paragraph


@dataclass
class DocHit:
    doc_id: int
    score: float
    chunks: list[ChunkHit]


class BM25Index:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._postings: dict[str, dict[int, int]] = defaultdict(dict)  # term -> {chunk_id: tf}
        self._chunk_len: dict[int, int] = {}
        self._chunk_doc: dict[int, int] = {}
        self._chunk_text: dict[int, str] = {}
        self._chunk_ordinal: dict[int, int] = {}
        self._chunk_terms: dict[int, list[str]] = {}
        self._doc_chunks: dict[int, list[int]] = defaultdict(list)
        self._total_len = 0

    def __len__(self) -> int:
        return len(self._chunk_len)

    def clear(self) -> None:
        with self._lock:
            self.__init__()

    def add_document(
        self, doc_id: int, title: str, tags: Iterable[str], chunks: Iterable[tuple[int, str] | tuple[int, str, int]]
    ) -> None:
        """`chunks` are (chunk_id, text) or (chunk_id, text, ordinal) pairs."""
        header = tokenize(" ".join([title, *tags])) * TITLE_WEIGHT
        with self._lock:
            self.remove_document(doc_id)
            for i, chunk in enumerate(chunks):
                chunk_id, text = chunk[0], chunk[1]
                self._chunk_ordinal[chunk_id] = chunk[2] if len(chunk) > 2 else i
                terms = tokenize(text) + header
                counts = Counter(terms)
                for term, tf in counts.items():
                    self._postings[term][chunk_id] = tf
                self._chunk_terms[chunk_id] = list(counts)
                self._chunk_len[chunk_id] = len(terms)
                self._chunk_doc[chunk_id] = doc_id
                self._chunk_text[chunk_id] = text
                self._doc_chunks[doc_id].append(chunk_id)
                self._total_len += len(terms)

    def remove_document(self, doc_id: int) -> None:
        with self._lock:
            for chunk_id in self._doc_chunks.pop(doc_id, []):
                self._total_len -= self._chunk_len.pop(chunk_id, 0)
                self._chunk_doc.pop(chunk_id, None)
                self._chunk_text.pop(chunk_id, None)
                self._chunk_ordinal.pop(chunk_id, None)
                for term in self._chunk_terms.pop(chunk_id, []):
                    posting = self._postings.get(term)
                    if posting is not None:
                        posting.pop(chunk_id, None)
                        if not posting:
                            del self._postings[term]

    def search(
        self,
        query: str,
        allowed_doc_ids: set[int] | None = None,
        top_k: int = 6,
        chunks_per_doc: int = 2,
    ) -> list[DocHit]:
        """Return up to `top_k` documents, each with its best-matching chunks."""
        terms = set(tokenize(query))
        if not terms:
            return []
        with self._lock:
            n = len(self._chunk_len)
            if n == 0:
                return []
            avg_len = self._total_len / n
            scores: dict[int, float] = defaultdict(float)
            for term in terms:
                posting = self._postings.get(term)
                if not posting:
                    continue
                idf = math.log(1 + (n - len(posting) + 0.5) / (len(posting) + 0.5))
                for chunk_id, tf in posting.items():
                    if allowed_doc_ids is not None and self._chunk_doc[chunk_id] not in allowed_doc_ids:
                        continue
                    norm = tf + K1 * (1 - B + B * self._chunk_len[chunk_id] / avg_len)
                    scores[chunk_id] += idf * tf * (K1 + 1) / norm

            by_doc: dict[int, list[ChunkHit]] = defaultdict(list)
            for chunk_id, score in scores.items():
                doc_id = self._chunk_doc[chunk_id]
                by_doc[doc_id].append(
                    ChunkHit(chunk_id, doc_id, score, self._chunk_text[chunk_id], self._chunk_ordinal.get(chunk_id, 0))
                )

        results = []
        for doc_id, hits in by_doc.items():
            hits.sort(key=lambda h: h.score, reverse=True)
            # Document score: best chunk, plus a small bonus for further supporting chunks.
            score = hits[0].score + 0.25 * sum(h.score for h in hits[1:3])
            results.append(DocHit(doc_id, score, hits[:chunks_per_doc]))
        results.sort(key=lambda d: d.score, reverse=True)
        return results[:top_k]


# Full-text index: searched only over documents the user may open.
index = BM25Index()

# Title-only index: the *only* index ever searched for documents a user can't open
# ("locked" hints in the library and the assistant). Matching locked documents on their
# content would let someone probe what a locked file says by watching which searches hit it.
title_index = BM25Index()
