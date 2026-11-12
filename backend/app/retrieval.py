"""Finding the passages that answer a question, among the documents someone may open.

Everything that searches document text goes through `rank`: the assistant, the library and
the answer-quality benchmark (backend/eval). That keeps one definition of "the best
results", so the benchmark measures what people actually get.

The steps:

1. Understand the question. Each meaningful word becomes a *concept*, satisfied by the word
   itself or by what it stands for: company glossary terms ("PTO" also finds "leave"),
   acronyms a document defines ("Paid Time Off (PTO)"), the right spelling of a typo
   ("notise" finds "notice"), two words a document writes as one ("rolled out", "rollout")
   and, for a word no document uses, ordinary words that mean it ("dinners" finds "meals";
   app/thesaurus.py). An amount is a concept of its own, satisfied by a sentence whose rule
   covers it ("a 25% discount" by "above 20%"; app/quantities.py). Suggestions, acronyms
   and stand-ins only come from documents the person may open, so they can't reveal what
   other files contain.
2. Retrieve candidates with BM25 over those words (each weighted: typed words count most).
3. Rerank the candidate passages: how much of the question they cover (rare words count
   more), how close together the matching words are, whether the title matches, and
   whether the document is old. With search by meaning on (app/embeddings.py), passages
   that mean what the question asks join the candidates and score for it too.
4. Pick the sentence that carries the answer in each of the best documents: the one that
   covers the question and is the kind of answer asked for (a frequency for "how often").
5. Rerank those documents by their sentence: a document whose words match but that has no
   sentence answering the question falls behind one that has.
6. Decide whether the evidence is good enough to answer at all: the sentence must cover
   enough of the question, and more than one of the things it's about when it's about
   several. If it isn't, the assistant says so instead of guessing.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from . import glossary, quantities, thesaurus
from .search import ChunkHit, DocHit, index
from .services import AccessView
from .text import EVERYDAY, GENERIC, QUANTITY, STOPWORDS, has_quantity, passage_sentences, stem, tokenize, words

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
# After the best sentence of each leading document is found, the documents are ordered again:
# a document's score plus this much of its sentence's score.
W_EVIDENCE = 0.5
RERANK_DOCS = 6  # how many leading documents get a best sentence and take part in that
# Search by meaning, when on: the closest passage scores up to W_SEMANTIC, the 30th closest 0.
# Words are trusted when they match and meaning when they don't: the weight shrinks as the best
# passage covers more of the question's words, so a question a passage answers word for word
# ranks as before. NOT tuned: no embedding model could be run where this was written. Measure
# with yours before relying on it (python -m eval.run --embeddings).
W_SEMANTIC = 0.6
SEMANTIC_CHUNKS = 30

# Evidence: the best sentence must cover this share of what the question is about.
ENOUGH_COVERAGE = 0.45
GENERIC_WEIGHT = 0.15  # "how many", "policy", "need": what kind of answer, not what it's about
UNKNOWN_WORD_WEIGHT = 1.0  # a subject no document mentions ("hackathon") counts fully against the evidence
EVERYDAY_WEIGHT = 0.6  # everyday words ("full", "job", "change") matter less than subjects
EVERYDAY_UNKNOWN_WEIGHT = 0.4  # ...and less still when no document uses them: likely phrased differently
NUMBER_WEIGHT = 0.2
GLOSSARY_WEIGHT = 0.7  # a glossary word standing in for the one typed ("vacation" for "leave")
QUANTITY_WEIGHT = 1.5  # an amount asked about ("a 25% discount"): a detail, but a telling one
# A question about several things isn't answered by a sentence about one of them ("how do I
# change my mentor" by a sentence that only mentions a mentor): search engines call this
# minimum-should-match.
MIN_MATCH = 2
GENERAL_STAND_INS = 4  # ordinary words tried for a word no document uses (0: none)
KIND_FIT = 0.15  # the sentence is the kind of answer asked for (a frequency for "how often")
KIND_MISS = 0.25  # ...or can't be

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
    # An amount asked about. Its terms are marks (see `_marks`) put on the sentences whose
    # rule covers the amount, not words from the index.
    quantity: quantities.Quantity | None = None

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
    subjects: int = 0  # the things the question is about that the documents mention at all
    matched: int = 0  # ...and how many of them this evidence touches

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
    by_meaning: bool = False  # whether search by meaning took part

    @property
    def info(self) -> dict:
        """What the search did, for the reply's metadata (shown under the answer)."""
        out: dict = {"meaning": True} if self.by_meaning else {}
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

    # Amounts are compared as amounts, so their words ("60k", "six", "weeks") aren't concepts.
    asked = quantities.find(question)
    amount_words = {w for q in asked for w in _RAW_WORD.findall(q.text.lower())}
    raw = [w for w in words(question) if w not in amount_words]
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
                        single.setdefault(toks[0], GLOSSARY_WEIGHT)
                    elif toks:
                        multi.append((tuple(toks), GLOSSARY_WEIGHT))
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
            # Another form of the word first ("reach", "reachable"); then, for a real word the
            # documents just don't use, ordinary words that mean it; then a typo's right spelling.
            suggestions = index.suggest(base, allowed)
            variant = next((sg for sg in suggestions if sg[2] == "variant"), None)
            general = [] if variant or key in GENERIC else _general(base, allowed, typed_stems)
            if variant:
                terms_for.setdefault(variant[1], 0.8)
                known = True
            elif general:
                for word, word_stem, counts in general:
                    terms_for.setdefault(word_stem, counts)
                    if word not in expanded and len(expanded) < glossary.MAX_ADDED:
                        expanded.append(word)
                known = True
            elif suggestions:
                right, right_stem, _how = suggestions[0]
                terms_for.setdefault(right_stem, 0.8)
                corrections.append({"from": base, "to": right})
                known = True
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
    amount_stems = {stem(w) for w in amount_words} | amount_words
    for t in tokenize(question):
        if t in amount_stems:
            continue
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
    _join_compounds(question, concepts, allowed)
    for i, q in enumerate(asked):
        concepts.append(Concept(q.text, {f"{_MARK}{i}": 1.0, f"{_MARK}{i}~": quantities.NEAR}, QUANTITY_WEIGHT, True, quantity=q))
    return concepts, expanded, corrections, acronyms


_RAW_WORD = re.compile(r"[a-z0-9]+")
_MARK = "#q"  # never a word: the tokenizer drops "#"


def _general(word: str, allowed: set[int], typed: set[str]) -> list[tuple[str, str, float]]:
    """Ordinary words that could stand in for `word` and that the person's documents use, as
    (word, stem, how much it counts). Vague words are left out: they'd match everything."""
    out: list[tuple[str, str, float]] = []
    if GENERAL_STAND_INS <= 0:
        return out
    for other, counts in thesaurus.stand_ins(word):
        other_stem = stem(other)
        if other in STOPWORDS or other_stem in GENERIC or other_stem in EVERYDAY or other_stem in typed:
            continue
        if any(other_stem == s for _, s, _ in out) or not index.appears_in(other_stem, allowed):
            continue
        out.append((other, other_stem, counts))
        if len(out) == GENERAL_STAND_INS:
            break
    return out


def _join_compounds(question: str, concepts: list[Concept], allowed: set[int]) -> None:
    """Two words the documents write as one: "rolled out" also finds "rollout", "check in" "checkin"."""
    seq = _RAW_WORD.findall(question.lower())
    by_stem = {t: c for c in concepts for t, w in c.terms.items() if w == 1.0}
    for a, b in zip(seq, seq[1:], strict=False):
        if len(a) < 2 or len(b) < 2 or (a in STOPWORDS and b in STOPWORDS) or a.isdigit() or b.isdigit():
            continue
        owners = [c for c in (by_stem.get(stem(a)), by_stem.get(stem(b))) if c is not None]
        for joined in {stem(a + b), stem(stem(a) + b)}:
            if index.appears_in(joined, allowed):
                for owner in owners:  # the one word answers for both halves ("post", "mortem")
                    owner.terms.setdefault(joined, 1.0)
                    owner.known = True


def _is_generic(c: Concept) -> bool:
    """A word about the kind of answer, not its subject ("policy"), however it was spelled ("polcy")."""
    return stem(c.word) in GENERIC or any(t in GENERIC for t, w in c.terms.items() if w >= 0.8)


def _marks(concepts: list[Concept], text: str) -> set[str]:
    """Marks for the amounts asked about that `text` speaks to: the mark itself when a rule in
    the text covers the amount, the mark with "~" when it only states a limit of the same kind."""
    asked = [c for c in concepts if c.quantity is not None]
    if not asked:
        return set()
    stated = quantities.bands(text)
    out: set[str] = set()
    for c in asked:
        how = quantities.fit(c.quantity, stated)
        if how:
            mark = next(t for t in c.terms if not t.endswith("~"))
            out.add(mark if how >= quantities.FITS else mark + "~")
    return out


def _terms(concepts: list[Concept], text: str) -> set[str]:
    """What a text offers a question: its words, and marks for the amounts it speaks to."""
    return set(tokenize(text)) | _marks(concepts, text)


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
    present = set(tokens) | _marks(concepts, hit.text)
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
_HOW_OFTEN = re.compile(r"(?i)\bhow\s+(?:often|frequently|regularly)\b|\bhow\s+many\s+times\b")
_DAY = r"monday|tuesday|wednesday|thursday|friday|saturday|sunday"
_FREQUENCY = re.compile(
    rf"(?i)\b(?:every|each)\s+(?:\d+\s+|other\s+|single\s+)?(?:minute|hour|day|week|month|quarter|year|morning|evening|{_DAY})s?\b|"
    r"\b(?:daily|weekly|monthly|quarterly|yearly|annually|hourly|fortnightly|nightly)\b|"
    r"\b(?:once|twice|thrice|\w+\s+times)\s+(?:a|an|per|every|each)\b"
)
_HOW_LONG = re.compile(r"(?i)\bhow\s+(?:long|soon|quickly|fast|late|early|far\s+in\s+advance|much\s+(?:notice|time))\b")
_HOW_MUCH = re.compile(r"(?i)\bhow\s+(?:many|much|long|soon|quickly|fast|far|old)\b")
_WHO = re.compile(
    r"(?i)^\s*who\b|\bwho\s+(?:do|does|should|can|is|are|has|gets|handles?|approves?|signs?|owns?|makes?|decides?)\b"
)
_SOMEONE = re.compile(
    r"(?i)\b(?:managers?|directors?|ceo|cfo|cto|hr|finance|team|lead|engineers?|commander|recruiters?|partner|owner|executives?|"
    r"officer|interviewers?|mentor|buddy|panel|reception|desk|it|security|secondary|primary)\b|#[a-z][\w-]+"
)


def _sentence_score(concepts: list[Concept], heading: str, sentence: str, question: str) -> tuple[float, float]:
    tokens = tokenize(sentence)
    present = set(tokens) | _marks(concepts, sentence)
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
    # The kind of answer asked for: how often wants a frequency, how long a duration, who a
    # person, team or role. A sentence that states none can still be relevant, but one that
    # does is the likelier answer; and a "how often" sentence with no frequency isn't one.
    if _HOW_OFTEN.search(question):
        if _FREQUENCY.search(sentence):
            bonus += KIND_FIT
        elif not _states_duration(sentence):  # "a shift is one week" still says how often
            bonus -= KIND_MISS
    elif _HOW_LONG.search(question) and _states_duration(sentence):
        bonus += KIND_FIT
    elif _WHO.search(question) and (_SOMEONE.search(sentence) or _NAME_WORD.search(sentence)):
        bonus += KIND_FIT / 2
    # Bonuses scale with how relevant the sentence already is: numbers alone don't make an answer.
    score = with_heading + bonus * with_heading
    if len(sentence) > 400:
        score -= 0.1
    return score, with_heading


def _states_duration(text: str) -> bool:
    return any(b.kind == quantities.DURATION for b in quantities.bands(text))


def answers_kind(question: str, text: str) -> bool:
    """Whether `text` can answer the kind of question asked: "how often" needs a frequency or
    a length of time, and "how much", "how many" or "how long" a number of some kind. A
    sentence with none isn't an answer, however many of the question's words it shares."""
    if _HOW_OFTEN.search(question):
        return bool(_FREQUENCY.search(text)) or _states_duration(text)
    if _HOW_MUCH.search(question):
        return has_quantity(text)
    return True


def _is_title_line(sentence: str, title_terms: set[str]) -> bool:
    """A line that only repeats the document's title (the first line of a text file) says nothing."""
    if not title_terms or len(sentence) > 80 or sentence.rstrip().endswith((".", "?", "!", ":")):
        return False
    own = set(tokenize(sentence))
    return bool(own) and own <= title_terms


def best_sentence(
    concepts: list[Concept], question: str, chunks: list[tuple[int, str]], doc_id: int, title: str = ""
) -> Evidence | None:
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
    title_terms = set(tokenize(title))
    for ordinal, text in chunks:
        sents = [(h, s) for h, s in passage_sentences(text) if not _is_title_line(s, title_terms)]
        for i, (heading, s) in enumerate(sents):
            score, coverage = _sentence_score(concepts, heading, s, question)
            own = _terms(concepts, s) | set(tokenize(heading))
            scored.append((ordinal, s, own))
            near = set()
            for j in (i - 1, i + 1):
                if 0 <= j < len(sents):
                    near |= _terms(concepts, sents[j][1])
            if near:
                coverage += 0.5 * max(0.0, _coverage(concepts, near | _terms(concepts, s)) - coverage)
            if score > best_score + 1e-9:
                best_score = score
                sentence = s
                if i + 1 < len(sents):
                    nxt = sents[i + 1][1]
                    adds = _coverage(concepts, own | _terms(concepts, nxt)) > _coverage(concepts, own) + 1e-9
                    # A lead-in ("Before every release:") needs what follows; a short sentence gets
                    # the next one only if it adds to the answer (not the next row of a list).
                    if s.rstrip().endswith(":") or (len(s) < 90 and sents[i + 1][0] == heading and adds):
                        sentence = f"{s} {nxt}"
                best = Evidence(doc_id, ordinal, sentence, round(coverage, 3), round(score, 3))
                best_terms = _terms(concepts, sentence) | set(tokenize(heading))
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
            best_terms |= _terms(concepts, more[1])
        subjects = [c for c in concepts if c.known and c.quantity is None and not _is_generic(c)]
        best.subjects = len(subjects)
        best.matched = sum(1 for c in subjects if c.found_in(best_terms | title_terms) > 0)
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
    candidates: int = CANDIDATES,
) -> Ranking:
    """The documents (with their best passages) that best match `question`, best first, among
    the documents `view` may open (narrowed to `only` when given, e.g. by filters)."""
    allowed = view.allowed if only is None else view.allowed & only
    concepts, expanded, corrections, acronyms = analyse(db, view, question, terms)
    weights: dict[str, float] = {}
    for c in concepts:
        if c.quantity is not None:
            # An amount's marks aren't in the index; its digits help find candidates.
            for t in tokenize(c.quantity.text):
                weights[t] = max(weights.get(t, 0.0), 0.3)
            continue
        for t, w in c.terms.items():
            weights[t] = max(weights.get(t, 0.0), w * (0.4 if stem(c.word) in GENERIC else 1.0))
        for words_, w in c.phrases:
            for t in words_:
                weights[t] = max(weights.get(t, 0.0), w * 0.5)
    query = " ".join([question, *expanded])
    if not weights:
        return Ranking([], query, expanded, corrections, acronyms, concepts)

    found = index.search_terms(weights, allowed_doc_ids=allowed, top_k=max(candidates, top_k), chunks_per_doc=3)
    semantic = _by_meaning(question, allowed)
    if semantic:
        _add_by_meaning(found, semantic)
    if not found:
        return Ranking([], query, expanded, corrections, acronyms, concepts)
    max_bm25 = max(c.score for d in found for c in d.chunks)

    lexical: list[tuple[DocHit, list[tuple[float, float, ChunkHit]]]] = []
    for d in found:
        doc = view.docs[d.doc_id]
        title_terms = set(tokenize(" ".join([doc.title, *(doc.tags or [])])))
        lexical.append((d, [(*_rerank_chunk(c, concepts, max_bm25, title_terms), c) for c in d.chunks]))
    best_coverage = max((cov for _, chunks in lexical for _, cov, _ in chunks), default=0.0)
    w_meaning = W_SEMANTIC * max(0.0, 1.0 - best_coverage)

    rescored: list[tuple[float, DocHit]] = []
    for d, chunks in lexical:
        doc = view.docs[d.doc_id]
        scored = [(score + w_meaning * semantic.get(c.chunk_id, 0.0), c) for score, _, c in chunks]
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
    if semantic and top_k > 1:
        # The passage closest in meaning is always among the results (the AI reads them all),
        # even when passages sharing more of the question's words outrank it.
        closest = index.passage(next(iter(semantic)))
        if closest and closest[0] not in {h.doc_id for h in hits}:
            extra = next((h for _, h in rescored if h.doc_id == closest[0]), None)
            if extra is not None:
                hits = hits[: top_k - 1] + [extra]

    evidence: dict[int, Evidence] = {}
    for h in hits[:RERANK_DOCS]:
        ev = best_sentence(concepts, question, [(c.ordinal, c.text) for c in h.chunks], h.doc_id, view.docs[h.doc_id].title)
        if ev is not None:
            evidence[h.doc_id] = ev
    # A document is as good as its best sentence: one whose words match the question but
    # that has no sentence answering it falls behind one that has.
    for h in hits[:RERANK_DOCS]:
        ev = evidence.get(h.doc_id)
        if ev is not None:
            h.score = round(h.score + W_EVIDENCE * max(0.0, min(ev.score, 1.2)), 4)
    hits.sort(key=lambda h: h.score, reverse=True)
    best = evidence.get(hits[0].doc_id) if hits else None
    coverage = max((e.coverage for e in evidence.values()), default=0.0)
    enough = (
        best is not None
        and best.coverage >= ENOUGH_COVERAGE
        and (best.subjects < MIN_MATCH or best.matched >= MIN_MATCH)
        and answers_kind(question, best.text)
    )
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
        bool(semantic),
    )


def _by_meaning(question: str, allowed: set[int]) -> dict[int, float]:
    """Passage id -> how close it is in meaning, 1 for the closest of the 30 closest and 0 for the
    30th; empty when search by meaning is off or unavailable."""
    from . import embeddings

    if not embeddings.enabled():
        return {}
    found = embeddings.search(question, allowed, SEMANTIC_CHUNKS)
    if not found:
        return {}
    top, low = found[0][2], found[-1][2]
    spread = top - low
    return {cid: 1.0 if spread <= 1e-9 else (sim - low) / spread for cid, _doc, sim in found}


def _add_by_meaning(found: list[DocHit], semantic: dict[int, float]) -> None:
    """Add the passages found by meaning to the candidates, as passages no word matched."""
    by_doc = {d.doc_id: d for d in found}
    for cid in semantic:
        passage = index.passage(cid)
        if passage is None:
            continue
        doc_id, text, ordinal = passage
        hit = by_doc.get(doc_id)
        if hit is None:
            hit = by_doc[doc_id] = DocHit(doc_id, 0.0, [])
            found.append(hit)
        if all(c.chunk_id != cid for c in hit.chunks):
            hit.chunks.append(ChunkHit(cid, doc_id, 0.0, text, ordinal, set()))
