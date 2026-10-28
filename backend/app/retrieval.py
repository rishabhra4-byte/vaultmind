"""Finding the passages that answer a question, among the documents someone may open.

Everything that searches document text goes through `rank`: the assistant, the library and
the answer-quality benchmark (backend/eval). That keeps one definition of "the best
results", so the benchmark measures what people actually get.

The steps:

1. Understand the question. Each meaningful word becomes a *concept*, satisfied by the word
   itself or by what it stands for: company glossary terms ("PTO" also finds "leave"),
   acronyms a document defines ("Paid Time Off (PTO)"), and the right spelling of a typo
   ("notise" finds "notice"). Suggestions and acronyms only come from documents the person
   may open, so they can't reveal what other files contain.
2. Retrieve candidates with BM25 over those words (each weighted: typed words count most).
3. Rerank the candidate passages: how much of the question they cover (rare words count
   more), how close together the matching words are, whether the title matches, and
   whether the document is old.
4. Pick the sentences that carry the answer, and decide whether the evidence is good enough
   to answer at all. If it isn't, the assistant says so instead of guessing.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from . import glossary
from .search import ChunkHit, DocHit, index
from .services import AccessView
from .text import EVERYDAY, GENERIC, QUANTITY, has_quantity, passage_sentences, stem, tokenize, words

CANDIDATES = 30  # documents taken from BM25 into reranking
OLD_AFTER_DAYS = 365  # a document not updated for this long may be out of date

# Reranking weights. Tuned on the benchmark's dev split only (python -m eval.run --split dev);
# the test split is reported, never tuned on.
W_BM25 = 0.20
W_COVERAGE = 0.60
W_PROXIMITY = 0.20
W_TITLE = 0.10
OLD_PENALTY = 0.08
SECOND_CHUNK = 0.15

# Evidence: the best sentence must cover this share of what the question is about.
ENOUGH_COVERAGE = 0.45
GENERIC_WEIGHT = 0.15  # "how many", "policy", "need": what kind of answer, not what it's about
UNKNOWN_WORD_WEIGHT = 1.0  # a subject no document mentions ("hackathon") counts fully against the evidence
EVERYDAY_WEIGHT = 0.6  # everyday words ("full", "job", "change") matter less than subjects
EVERYDAY_UNKNOWN_WEIGHT = 0.4  # ...and less still when no document uses them: likely phrased differently
NUMBER_WEIGHT = 0.2

_ACRONYMISH = re.compile(r"\b([A-Z][A-Z0-9&]{1,6})(s)?\b")
_YEAR = re.compile(r"\b(20\d\d)\b")


@dataclass
class Concept:
    """One thing the question is about, and the index terms that count as finding it."""

    word: str
    terms: dict[str, float]  # stem -> how much finding it counts (1 typed, less for stand-ins)
    weight: float  # importance: rare words matter more, words like "many" or "policy" less
    known: bool  # whether any document the person may open uses it (or a stand-in)
    # Multi-word stand-ins ("purchase order" for PO, "parental leave" for paternity): they count
    # only when all their words are there, so "leave" alone doesn't stand in for "paternity".
    phrases: list[tuple[tuple[str, ...], float]] = field(default_factory=list)

    def found_in(self, present: set[str]) -> float:
        """How well a passage with these terms covers this concept (0 to 1)."""
        best = max((w for t, w in self.terms.items() if t in present), default=0.0)
        for words_, w in self.phrases:
            if w > best and all(t in present for t in words_):
                best = w
        return best


@dataclass
class Evidence:
    doc_id: int
    ordinal: int  # the passage, for citing [D12.3]
    sentence: str
    coverage: float  # share of the question this sentence covers
    score: float
    # A second sentence when the question has parts the first doesn't cover
    # ("who approves a large claim, and when is it paid?"): (ordinal, sentence)
    more: tuple[int, str] | None = None

    @property
    def text(self) -> str:
        return f"{self.sentence} {self.more[1]}" if self.more else self.sentence

    @property
    def ordinals(self) -> list[int]:
        return sorted({self.ordinal, *([self.more[0]] if self.more else [])})


@dataclass
class Ranking:
    hits: list[DocHit]
    query: str  # what was searched (with glossary words), for display
    expanded: list[str] = field(default_factory=list)  # glossary words added
    corrections: list[dict] = field(default_factory=list)  # [{"from": "notise", "to": "notice"}]
    acronyms: list[dict] = field(default_factory=list)  # [{"acronym": "PO", "means": "purchase order"}]
    concepts: list[Concept] = field(default_factory=list)
    evidence: dict[int, Evidence] = field(default_factory=dict)  # best sentence per document
    enough: bool = False  # whether the best evidence covers the question well enough to answer
    coverage: float = 0.0  # how well the best evidence covers the question
    scores: dict[int, float] = field(default_factory=dict)  # final document scores

    @property
    def info(self) -> dict:
        """What the search did, for the reply's metadata (shown under the answer)."""
        out: dict = {}
        if self.expanded:
            out["expanded"] = self.expanded
        if self.corrections:
            out["corrected"] = self.corrections
        if self.acronyms:
            out["acronyms"] = self.acronyms
        return out


# ---------------------------------------------------------------- understanding the question


def analyse(db: Session, view: AccessView, question: str, terms: list[glossary.Entry] | None = None):
    """The question's concepts, plus what was added for display (glossary, acronyms, typos)."""
    allowed = view.allowed
    entries = glossary.entries(db) if terms is None else terms
    found = glossary.matches(question, entries)
    acronyms_typed = {m.group(1).lower() for m in _ACRONYMISH.finditer(question) if not m.group(1).isdigit()}

    concepts: list[Concept] = []
    seen: set[str] = set()
    expanded: list[str] = []
    corrections: list[dict] = []
    acronyms: list[dict] = []

    raw = words(question)
    typed_stems = {t for w in raw for t in tokenize(w)}

    def split(phrase: str) -> list[str]:
        """A phrase's meaningful words, without the question's own words."""
        return [t for t in tokenize(phrase) if t not in GENERIC and t not in typed_stems]

    def stand_ins(word: str) -> tuple[dict[str, float], list[tuple[tuple[str, ...], float]]]:
        """Glossary phrases for a word the question used: single words, and whole phrases."""
        single: dict[str, float] = {}
        multi: list[tuple[tuple[str, ...], float]] = []
        for used, others in found:
            if word in tokenize(used) or stem(word) in tokenize(used):
                for phrase in others:
                    if phrase not in expanded and len(expanded) < glossary.MAX_ADDED:
                        expanded.append(phrase)
                    toks = split(phrase)
                    if len(toks) == 1:
                        single.setdefault(toks[0], 0.6)
                    elif toks:
                        multi.append((tuple(toks), 0.6))
        return single, multi

    # Acronyms typed in capitals keep their letters ("PRs" is the acronym "pr").
    for w in raw:
        base = w[:-1] if w.endswith("s") and w[:-1] in acronyms_typed else w
        terms_for = {t: 1.0 for t in tokenize(base)}
        if not terms_for:
            continue
        key = next(iter(terms_for))
        if key in seen:
            continue
        seen.add(key)
        single, phrases = stand_ins(base)
        terms_for.update({t: wt for t, wt in single.items() if t not in terms_for})
        known = any(index.appears_in(t, allowed) for t in terms_for) or any(
            all(index.appears_in(t, allowed) for t in p) for p, _ in phrases
        )
        if base in acronyms_typed or (len(base) <= 5 and base.isalpha() and not known):
            means = index.acronym(base, allowed)
            if means:
                acronyms.append({"acronym": base.upper(), "means": means})
                toks = split(means) or tokenize(means)
                if len(toks) == 1:
                    terms_for.setdefault(toks[0], 0.8)
                else:
                    phrases.append((tuple(toks), 0.8))
                known = True
        if not known and base.isalpha():
            for right, right_stem, how in index.suggest(base, allowed):
                terms_for.setdefault(right_stem, 0.8)
                if how == "typo":
                    corrections.append({"from": base, "to": right})
                known = True
                break
        # How much the word matters: its rarity in the documents, or that of what stands in for it
        # (a corrected typo, an acronym's meaning, a glossary term).
        candidates = list(terms_for) + [t for p, _ in phrases for t in p]
        used = [t for t in candidates if index.appears_in(t, None)]
        weight = max(index.idf(t) for t in (used or candidates))
        if key in GENERIC or (not known and len(base) <= 3 and not re.search(r"[aeiou]", base)):
            weight *= GENERIC_WEIGHT  # "how many", "policy"; or "mny", a short typo nothing matches
        elif any(ch.isdigit() for ch in key) and not known:
            weight *= NUMBER_WEIGHT  # "60k": a value they mention, not a subject
        elif key in EVERYDAY:
            weight *= EVERYDAY_WEIGHT if known else EVERYDAY_UNKNOWN_WEIGHT
        elif not known:
            weight *= UNKNOWN_WORD_WEIGHT
        concepts.append(Concept(base, terms_for, weight, known, phrases))
    # Names like "SDE-2" or "H-1" are their own concept: "SDE-2" must not match "SDE-1".
    for t in tokenize(question):
        if t not in seen and any(ch.isdigit() for ch in t) and any(ch.isalpha() for ch in t):
            seen.add(t)
            known = index.appears_in(t, allowed)
            concepts.append(Concept(t, {t: 1.0}, index.idf(t) * (1.0 if known else NUMBER_WEIGHT), known))
    # Glossary phrases whose words the loop didn't attach to a typed word (multi-word matches).
    for used, others in found:
        if not any(t in c.terms for c in concepts for t in tokenize(used)):
            for phrase in others:
                if phrase not in expanded and len(expanded) < glossary.MAX_ADDED:
                    expanded.append(phrase)
    return concepts, expanded, corrections, acronyms


# ---------------------------------------------------------------- scoring passages


def _coverage(concepts: list[Concept], present: set[str]) -> float:
    total = sum(c.weight for c in concepts)
    if not total:
        return 0.0
    return sum(c.weight * c.found_in(present) for c in concepts) / total


def _proximity(concepts: list[Concept], tokens: list[str]) -> float:
    """1 when the matched concepts sit right next to each other, falling as they spread apart."""
    positions: dict[int, list[int]] = {}
    present = set(tokens)
    for i, t in enumerate(tokens):
        for ci, c in enumerate(concepts):
            if t in c.terms or any(t in p and all(x in present for x in p) for p, _ in c.phrases):
                positions.setdefault(ci, []).append(i)
    if len(positions) < 2:
        return 0.0
    events = sorted((p, ci) for ci, ps in positions.items() for p in ps)
    need = len(positions)
    best = len(tokens)
    counts: dict[int, int] = {}
    left = 0
    covered = 0
    for right in range(len(events)):
        ci = events[right][1]
        counts[ci] = counts.get(ci, 0) + 1
        if counts[ci] == 1:
            covered += 1
        while covered == need:
            best = min(best, events[right][0] - events[left][0] + 1)
            lc = events[left][1]
            counts[lc] -= 1
            if counts[lc] == 0:
                covered -= 1
            left += 1
    return min(1.0, (need + 2) / best)


def _age_days(doc) -> int:
    when = doc.updated_at or doc.created_at
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return (datetime.now(UTC) - when).days


def is_old(doc) -> bool:
    return _age_days(doc) > OLD_AFTER_DAYS


def _asks_for_old(question: str, doc) -> bool:
    """'What did the 2024 travel policy say?' is about the old document on purpose."""
    years = set(_YEAR.findall(question))
    return bool(years & set(_YEAR.findall(doc.title))) or bool(re.search(r"(?i)\b(?:old|previous|former|earlier)\b", question))


def _rerank_chunk(hit: ChunkHit, concepts: list[Concept], max_bm25: float, title_terms: set[str]) -> tuple[float, float]:
    tokens = tokenize(hit.text)
    present = set(tokens)
    coverage = _coverage(concepts, present | title_terms)
    proximity = _proximity(concepts, tokens)
    title = _coverage(concepts, title_terms)
    score = W_BM25 * (hit.score / max_bm25 if max_bm25 else 0) + W_COVERAGE * coverage + W_PROXIMITY * proximity + W_TITLE * title
    return score, coverage


_NAME_WORD = re.compile(r"(?<=[a-z0-9,;:)] )([A-Z][a-z]+)")  # capitalised, not starting a sentence
_WHEN = re.compile(r"(?i)^\s*when\b|\bwhat\s+time\b|\bwhich\s+days?\b|\bby\s+when\b")
_DATEISH = re.compile(
    r"(?i)\b(?:\d{1,2}(?::\d{2})?\s*(?:am|pm)|\d{1,2}:\d{2}|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"january|february|march|april|may|june|july|august|september|october|november|december|weekly|daily|"
    r"every\s+(?:day|week|month|year|monday|tuesday|wednesday|thursday|friday)|twice|once)\b"
)
_MONEYISH = re.compile(r"(?i)\b(?:inr|rs\.?|usd|lakh|crore)\b|[₹$€£]\s?\d")


def _sentence_score(concepts: list[Concept], heading: str, sentence: str, question: str) -> tuple[float, float]:
    tokens = tokenize(sentence)
    present = set(tokens)
    # A word that's only part of a name here ("Prestige Tech Park") counts half for "park".
    names = {stem(w.lower()) for w in _NAME_WORD.findall(sentence)}
    plain = set(tokenize(re.sub(_NAME_WORD, " ", sentence)))
    named_only = (names & present) - plain
    heading_terms = set(tokenize(heading))
    in_sentence = _coverage(concepts, present)
    coverage = _coverage(concepts, present - named_only)
    coverage += 0.5 * (in_sentence - coverage)
    # The heading a sentence sits under is its context ("Parking is on levels B1 and B2" under
    # "Office Guide"): it counts nearly as much as the sentence's own words.
    with_heading = coverage + 0.8 * (_coverage(concepts, present | heading_terms) - in_sentence)
    bonus = 0.15 * _proximity(concepts, tokens)
    if QUANTITY.search(question) and has_quantity(sentence):
        bonus += 0.1
        if _WHEN.search(question) and _DATEISH.search(sentence):
            bonus += 0.15
        elif re.search(r"(?i)\bhow\s+much\b|\blimit\b|\bbudget\b|\bprice\b", question) and _MONEYISH.search(sentence):
            bonus += 0.15
    # Bonuses scale with how relevant the sentence already is: numbers alone don't make an answer.
    score = with_heading + bonus * with_heading
    if len(sentence) > 400:
        score -= 0.1
    return score, with_heading


def best_sentence(concepts: list[Concept], question: str, chunks: list[tuple[int, str]], doc_id: int) -> Evidence | None:
    """The sentence in these passages that best answers the question (with a neighbour when it's short).

    A neighbouring sentence counts for half towards how much of the question is covered:
    an answer is often spread over two ("The debrief makes the decision... HR sends the
    offer within 2 business days"). When another sentence covers parts of the question the
    best one doesn't, it's added too.
    """
    best: Evidence | None = None
    best_score = float("-inf")
    best_terms: set[str] = set()
    scored: list[tuple[int, str, set[str]]] = []
    for ordinal, text in chunks:
        sents = passage_sentences(text)
        for i, (heading, s) in enumerate(sents):
            score, coverage = _sentence_score(concepts, heading, s, question)
            own = set(tokenize(s)) | set(tokenize(heading))
            scored.append((ordinal, s, own))
            near = set()
            for j in (i - 1, i + 1):
                if 0 <= j < len(sents):
                    near |= set(tokenize(sents[j][1]))
            if near:
                coverage += 0.5 * max(0.0, _coverage(concepts, near | set(tokenize(s))) - coverage)
            if score > best_score + 1e-9:
                best_score = score
                sentence = s
                if i + 1 < len(sents):
                    nxt = sents[i + 1][1]
                    adds = _coverage(concepts, own | set(tokenize(nxt))) > _coverage(concepts, own) + 1e-9
                    # A lead-in ("Before every release:") needs what follows; a short sentence gets
                    # the next one only if it adds to the answer (not the next row of a list).
                    if s.rstrip().endswith(":") or (len(s) < 90 and sents[i + 1][0] == heading and adds):
                        sentence = f"{s} {nxt}"
                best = Evidence(doc_id, ordinal, sentence, round(coverage, 3), round(score, 3))
                best_terms = set(tokenize(sentence)) | set(tokenize(heading))
    if best is not None:
        base = _coverage(concepts, best_terms)
        gain, more = 0.0, None
        for ordinal, s, own in scored:
            if s in best.sentence:
                continue
            g = _coverage(concepts, best_terms | own) - base
            if g > gain + 1e-9:
                gain, more = g, (ordinal, s)
        if more is not None and gain >= 0.15:
            best.more = more
            best.coverage = round(min(1.0, best.coverage + gain), 3)
    return best


# ---------------------------------------------------------------- the whole thing


def rank(
    db: Session,
    view: AccessView,
    question: str,
    *,
    top_k: int = 10,
    terms: list[glossary.Entry] | None = None,
    only: set[int] | None = None,
) -> Ranking:
    """The documents (with their best passages) that best match `question`, best first, among
    the documents `view` may open (narrowed to `only` when given, e.g. by filters)."""
    allowed = view.allowed if only is None else view.allowed & only
    concepts, expanded, corrections, acronyms = analyse(db, view, question, terms)
    weights: dict[str, float] = {}
    for c in concepts:
        for t, w in c.terms.items():
            weights[t] = max(weights.get(t, 0.0), w * (0.4 if stem(c.word) in GENERIC else 1.0))
        for words_, w in c.phrases:
            for t in words_:
                weights[t] = max(weights.get(t, 0.0), w * 0.5)
    query = " ".join([question, *expanded])
    if not weights:
        return Ranking([], query, expanded, corrections, acronyms, concepts)

    candidates = index.search_terms(weights, allowed_doc_ids=allowed, top_k=CANDIDATES, chunks_per_doc=3)
    if not candidates:
        return Ranking([], query, expanded, corrections, acronyms, concepts)
    max_bm25 = max(c.score for d in candidates for c in d.chunks)

    rescored: list[tuple[float, DocHit]] = []
    for d in candidates:
        doc = view.docs[d.doc_id]
        title_terms = set(tokenize(" ".join([doc.title, *(doc.tags or [])])))
        scored = []
        for c in d.chunks:
            score, _ = _rerank_chunk(c, concepts, max_bm25, title_terms)
            scored.append((score, c))
        scored.sort(key=lambda x: x[0], reverse=True)
        doc_score = scored[0][0] + SECOND_CHUNK * (scored[1][0] if len(scored) > 1 else 0)
        if is_old(doc) and not _asks_for_old(question, doc):
            doc_score -= OLD_PENALTY
        chunks = [c for _, c in scored]
        for s, c in scored:
            c.score = round(s, 4)
        rescored.append((doc_score, DocHit(d.doc_id, round(doc_score, 4), chunks)))
    rescored.sort(key=lambda x: x[0], reverse=True)
    hits = [h for _, h in rescored[:top_k]]

    evidence: dict[int, Evidence] = {}
    for h in hits[:6]:
        ev = best_sentence(concepts, question, [(c.ordinal, c.text) for c in h.chunks], h.doc_id)
        if ev is not None:
            evidence[h.doc_id] = ev
    best = evidence.get(hits[0].doc_id) if hits else None
    coverage = max((e.coverage for e in evidence.values()), default=0.0)
    enough = best is not None and best.coverage >= ENOUGH_COVERAGE
    return Ranking(
        hits,
        query,
        expanded,
        corrections,
        acronyms,
        concepts,
        evidence,
        enough,
        round(coverage, 3),
        {h.doc_id: h.score for h in hits},
    )
