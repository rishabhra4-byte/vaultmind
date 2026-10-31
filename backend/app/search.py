"""In-memory BM25 index over document chunks.

Why BM25 and not embeddings? It needs no model download or external service, it's fast,
explainable, and strong on the keyword-heavy queries people type when looking for a
company document. retrieval.py builds on it: typo matching, acronyms, a reranking stage, and
optionally a dense (embedding) retriever fused with it.

Permission filtering happens *before* ranking: `search` only scores chunks whose
document id is in `allowed_doc_ids`, so text from documents a user can't open never
reaches the assistant. The same goes for the helpers that look at the vocabulary (typo
suggestions, acronyms): they only answer from documents the person may open, so they can't
be used to find out which words appear in someone else's files.
"""

import math
import re
import threading
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from .text import edit_distance, stem, tokenize, words

K1 = 1.5
B = 0.75
TITLE_WEIGHT = 3  # title and tag terms count this many times in every chunk of the doc

# "Paid Time Off (PTO)" and "PTO (Paid Time Off)": acronyms a document defines.
_ACRONYM_AFTER = re.compile(r"\b((?:[A-Z][a-z]+[ -]){1,5}[A-Za-z][a-z]+)\s+\(([A-Z][A-Za-z&]{1,7})\)")
_ACRONYM_BEFORE = re.compile(r"\b([A-Z][A-Z&]{1,7})\s+\(((?:[A-Za-z][a-z]+[ -]){1,5}[A-Za-z][a-z]+)\)")


def _initials_match(acronym: str, phrase: str) -> bool:
    letters = acronym.lower().replace("&", "")
    initials = "".join(w[0] for w in re.split(r"[ -]+", phrase.lower()) if w and w not in ("of", "and", "the", "for"))
    return initials == letters or initials.startswith(letters) and len(letters) >= 3


def defined_acronyms(text: str) -> dict[str, str]:
    """Acronyms the text defines, e.g. {"pto": "paid time off"}."""
    found: dict[str, str] = {}
    for m in _ACRONYM_AFTER.finditer(text):
        phrase, acronym = m.group(1), m.group(2)
        # Keep the last words of the phrase that the acronym's letters stand for.
        parts = re.split(r"[ -]+", phrase)
        for start in range(len(parts)):
            candidate = " ".join(parts[start:])
            if _initials_match(acronym, candidate):
                found.setdefault(acronym.lower(), candidate.lower())
                break
    for m in _ACRONYM_BEFORE.finditer(text):
        acronym, phrase = m.group(1), m.group(2)
        if _initials_match(acronym, phrase):
            found.setdefault(acronym.lower(), phrase.lower())
    return found


@dataclass
class ChunkHit:
    chunk_id: int
    doc_id: int
    score: float
    text: str
    ordinal: int = 0  # position of the passage in its document, for citing a paragraph
    matched: set[str] = field(default_factory=set)  # query terms found in this chunk


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
        # Unstemmed words seen in documents, for typo suggestions: word -> stem, and buckets
        # by (first letter, length) so a suggestion only compares a handful of words.
        self._raw: dict[str, str] = {}
        self._raw_buckets: dict[tuple[str, int], set[str]] = defaultdict(set)
        # Acronyms documents define: acronym -> {doc_id: what it stands for}
        self._acronyms: dict[str, dict[int, str]] = defaultdict(dict)
        self._doc_acronyms: dict[int, list[str]] = {}

    def __len__(self) -> int:
        return len(self._chunk_len)

    def clear(self) -> None:
        with self._lock:
            self.__init__()

    def add_document(
        self, doc_id: int, title: str, tags: Iterable[str], chunks: Iterable[tuple[int, str] | tuple[int, str, int]]
    ) -> None:
        """`chunks` are (chunk_id, text) or (chunk_id, text, ordinal) pairs."""
        tags = list(tags)
        header = tokenize(" ".join([title, *tags])) * TITLE_WEIGHT
        chunks = list(chunks)
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
            full = "\n\n".join([title, " ".join(tags)] + [c[1] for c in chunks])
            for w in set(words(full)):
                if len(w) >= 4 and w.isalpha() and w not in self._raw:
                    self._raw[w] = stem(w)
                    self._raw_buckets[(w[0], len(w))].add(w)
            defined = defined_acronyms(full)
            for acronym, phrase in defined.items():
                self._acronyms[acronym][doc_id] = phrase
            self._doc_acronyms[doc_id] = list(defined)

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
            for acronym in self._doc_acronyms.pop(doc_id, []):
                self._acronyms[acronym].pop(doc_id, None)
                if not self._acronyms[acronym]:
                    del self._acronyms[acronym]
            # Unstemmed words aren't removed: a word whose documents are gone has no postings
            # left, so suggesting it finds nothing (and suggestions check postings anyway).

    # ---------------------------------------------------------------- vocabulary

    def idf(self, term: str) -> float:
        """How rare a term is; terms nobody uses get the highest value."""
        with self._lock:
            n = max(1, len(self._chunk_len))
            df = len(self._postings.get(term, ()))
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def appears_in(self, term: str, allowed_doc_ids: set[int] | None) -> bool:
        """Whether any chunk of an allowed document contains the term."""
        with self._lock:
            posting = self._postings.get(term)
            if not posting:
                return False
            if allowed_doc_ids is None:
                return True
            return any(self._chunk_doc[c] in allowed_doc_ids for c in posting)

    def suggest(self, word: str, allowed_doc_ids: set[int] | None, limit: int = 2) -> list[tuple[str, str, str]]:
        """Words in the documents close to `word`, as (word, stem, how), most likely first:
        another form of it ("reach" and "reachable": how = "variant") or the right spelling of
        a typo ("notise": how = "typo").

        Only words that appear in documents the person may open are suggested.
        """
        if len(word) < 4 or not word.isalpha():
            return []
        max_distance = 2 if len(word) >= 8 else 1
        candidates: list[tuple[int, int, str, str]] = []
        with self._lock:
            for size in range(len(word) - max_distance, len(word) + 5):
                for other in self._raw_buckets.get((word[0], size), ()):
                    if other == word:
                        continue
                    if other.startswith(word) or (word.startswith(other) and len(other) >= 5):
                        rank, how = 0, "variant"
                    else:
                        d = edit_distance(word, other, max_distance)
                        if d > max_distance:
                            continue
                        rank, how = d, "typo"
                    term = self._raw[other]
                    if not self.appears_in(term, allowed_doc_ids):
                        continue
                    candidates.append((rank, -len(self._postings.get(term, ())), other, how))
        candidates.sort()
        out: list[tuple[str, str, str]] = []
        for _, _, other, how in candidates:
            if self._raw[other] not in {s for _, s, _ in out}:
                out.append((other, self._raw[other], how))
            if len(out) == limit:
                break
        return out

    def acronym(self, acronym: str, allowed_doc_ids: set[int] | None) -> str | None:
        """What an acronym stands for, as defined in a document the person may open."""
        with self._lock:
            defs = self._acronyms.get(acronym.lower(), {})
            for doc_id, phrase in defs.items():
                if allowed_doc_ids is None or doc_id in allowed_doc_ids:
                    return phrase
        return None

    # ---------------------------------------------------------------- searching

    def search(
        self,
        query: str,
        allowed_doc_ids: set[int] | None = None,
        top_k: int = 6,
        chunks_per_doc: int = 2,
    ) -> list[DocHit]:
        """Return up to `top_k` documents, each with its best-matching chunks."""
        return self.search_terms({t: 1.0 for t in tokenize(query)}, allowed_doc_ids, top_k, chunks_per_doc)

    def search_terms(
        self,
        weights: dict[str, float],
        allowed_doc_ids: set[int] | None = None,
        top_k: int = 6,
        chunks_per_doc: int = 2,
    ) -> list[DocHit]:
        """Like `search`, with each term weighted (typo corrections and expansions count less)."""
        if not weights:
            return []
        with self._lock:
            n = len(self._chunk_len)
            if n == 0:
                return []
            avg_len = self._total_len / n
            scores: dict[int, float] = defaultdict(float)
            matched: dict[int, set[str]] = defaultdict(set)
            for term, weight in weights.items():
                posting = self._postings.get(term)
                if not posting or weight <= 0:
                    continue
                idf = math.log(1 + (n - len(posting) + 0.5) / (len(posting) + 0.5))
                for chunk_id, tf in posting.items():
                    if allowed_doc_ids is not None and self._chunk_doc[chunk_id] not in allowed_doc_ids:
                        continue
                    norm = tf + K1 * (1 - B + B * self._chunk_len[chunk_id] / avg_len)
                    scores[chunk_id] += weight * idf * tf * (K1 + 1) / norm
                    matched[chunk_id].add(term)

            by_doc: dict[int, list[ChunkHit]] = defaultdict(list)
            for chunk_id, score in scores.items():
                doc_id = self._chunk_doc[chunk_id]
                by_doc[doc_id].append(
                    ChunkHit(
                        chunk_id,
                        doc_id,
                        score,
                        self._chunk_text[chunk_id],
                        self._chunk_ordinal.get(chunk_id, 0),
                        matched[chunk_id],
                    )
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
