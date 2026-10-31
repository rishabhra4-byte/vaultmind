"""Spotting documents that disagree.

Companies keep old policies around, and two documents often state different figures for the
same thing: a hotel limit, an approval threshold, how many days a week people are in the
office. When the passages behind an answer do that, the assistant says so instead of quietly
picking one.

A *claim* is a figure in a document with the words around it: an amount of money, a
percentage, a duration, a rate ("three days a week") or a time of day. Two claims conflict
when they come from different documents, are the same kind of figure, differ, and are about
the same thing (their surrounding words overlap), and both passages are about the question.
That's deliberately strict: a wrong warning costs trust too.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from .text import GENERIC, passage_sentences, stem, tokenize

NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}
_NUM = r"(\d+(?:[.,]\d+)*|" + "|".join(NUMBER_WORDS) + r")"
_UNIT_DAYS = {"minute": 1 / 1440, "hour": 1 / 24, "day": 1, "week": 7, "month": 30, "year": 365}

PATTERNS: list[tuple[str, re.Pattern]] = [
    # "three days a week", "twice a month" style rates
    (
        "rate",
        re.compile(rf"(?i)\b{_NUM}\s+(days?|times?|hours?)\s+(?:a|per|each|every)\s+(week|month|year|day)\b"),
    ),
    # "11:00 to 16:00", "10am to 6pm"
    (
        "time_range",
        re.compile(
            r"(?i)\b(\d{1,2}(?:[:.]\d{2})?\s*(?:am|pm)?)\s*(?:to|-|\u2013|until)\s*(\d{1,2}(?:[:.]\d{2})?\s*(?:am|pm)?)\b(?=\s*(?:ist|utc|am|pm|hours|\.|,|;|\)|$|\s))"
        ),
    ),
    # "INR 7,000", "Rs. 500", "$1,200", "INR 2.4 crore"
    (
        "money",
        re.compile(
            r"(?i)(?:\b(INR|Rs\.?|USD|EUR|GBP)\s?|([₹$€£])\s?)(\d[\d,]*(?:\.\d+)?)(?:\s*(lakh|crore|k|thousand|million))?\b"
        ),
    ),
    ("percent", re.compile(r"(?i)\b(\d+(?:\.\d+)?)\s?(?:%|percent\b)")),
    # "within 30 days", "six hours", "26 weeks", "3 business days"
    (
        "duration",
        re.compile(rf"(?i)\b{_NUM}[\s-]+((?:business|working|calendar)\s+)?(minutes?|hours?|days?|weeks?|months?|years?)\b"),
    ),
]
WINDOW = 9  # words either side of a figure that say what it's about
UNIT_WORDS = {
    stem(w)
    for w in (
        "inr rs usd eur gbp lakh crore thousand million percent minute hour day week month year business working calendar per"
    ).split()
}


@dataclass(frozen=True)
class Claim:
    doc_id: int
    ordinal: int
    sentence: str
    kind: str
    value: str  # comparable form, e.g. "money:inr:7000", "duration:calendar:0.25"
    shown: str  # as written, e.g. "INR 7,000"
    context: frozenset[str]  # words around the figure, reaching into neighbouring sentences
    near: frozenset[str]  # the same, within its own sentence only
    index: int  # order within its sentence (to line up "X ... and Y" with "X' ... and Y'")


@dataclass
class Conflict:
    pairs: list[tuple[Claim, Claim]]  # (newer document's figure, older document's figure)
    overlap: list[str]

    @property
    def a(self) -> Claim:
        return self.pairs[0][0]

    @property
    def b(self) -> Claim:
        return self.pairs[0][1]

    def as_dict(self, docs: dict) -> dict:
        da, db = docs[self.a.doc_id], docs[self.b.doc_id]
        return {
            "a": {
                "id": da.id,
                "title": da.title,
                "says": [p[0].shown for p in self.pairs],
                "sentence": self.a.sentence,
                "passage": self.a.ordinal,
                "updated_at": _iso(da),
            },
            "b": {
                "id": db.id,
                "title": db.title,
                "says": [p[1].shown for p in self.pairs],
                "sentence": self.b.sentence,
                "passage": self.b.ordinal,
                "updated_at": _iso(db),
            },
            "about": self.overlap[:4],
        }


def _iso(doc) -> str:
    when = doc.updated_at or doc.created_at
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return when.isoformat()


def _number(text: str) -> float | None:
    text = text.lower().strip()
    if text in NUMBER_WORDS:
        return float(NUMBER_WORDS[text])
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def _clock(text: str) -> str:
    t = text.lower().replace(" ", "").replace(".", ":")
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?(am|pm)?$", t)
    if not m:
        return t
    hour, minute, half = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if half == "pm" and hour < 12:
        hour += 12
    if half == "am" and hour == 12:
        hour = 0
    return f"{hour:02d}:{minute:02d}"


def _value(kind: str, m: re.Match) -> tuple[str, str] | None:
    shown = m.group(0).strip()
    if kind == "rate":
        n = _number(m.group(1))
        unit = m.group(2).lower().rstrip("s")
        return (f"rate:{n:g}/{unit}/{m.group(3).lower()}", shown) if n is not None else None
    if kind == "time_range":
        start, end = _clock(m.group(1)), _clock(m.group(2))
        if not (re.match(r"^\d\d:\d\d$", start) and re.match(r"^\d\d:\d\d$", end)):
            return None
        return f"time:{start}-{end}", shown
    if kind == "money":
        n = _number(m.group(3))
        if n is None:
            return None
        n *= {"lakh": 1e5, "crore": 1e7, "k": 1e3, "thousand": 1e3, "million": 1e6}.get((m.group(4) or "").lower(), 1)
        currency = (m.group(1) or m.group(2) or "").lower().rstrip(".")
        currency = {"rs": "inr", "₹": "inr", "$": "usd", "€": "eur", "£": "gbp"}.get(currency, currency)
        return f"money:{currency}:{n:g}", shown
    if kind == "percent":
        return f"percent:{float(m.group(1)):g}", shown
    if kind == "duration":
        n = _number(m.group(1))
        if n is None:
            return None
        unit = m.group(3).lower().rstrip("s")
        calendar = "business" if (m.group(2) or "").strip().lower() in ("business", "working") else "calendar"
        return f"duration:{calendar}:{n * _UNIT_DAYS[unit]:g}", shown
    return None


def _words(text: str) -> list[str]:
    return [t for t in tokenize(text) if not any(ch.isdigit() for ch in t)]


def claims(doc_id: int, ordinal: int, text: str) -> list[Claim]:
    """The figures in one passage, each with the words around it.

    The words come from either side of the figure, reaching into the neighbouring sentence
    when the figure is near the start or end of its own ("The primary on-call responds to
    pages within 15 minutes. The secondary takes over if...").
    """
    out: list[Claim] = []
    sents = [s for _, s in passage_sentences(text)]
    tokens = [_words(s) for s in sents]
    for k, sentence in enumerate(sents):
        taken: list[tuple[int, int]] = []
        found: list[tuple[int, str, str, str]] = []
        for kind, pattern in PATTERNS:
            for m in pattern.finditer(sentence):
                if any(m.start() < e and s < m.end() for s, e in taken):
                    continue  # "three days a week" is a rate, not also a duration
                parsed = _value(kind, m)
                if parsed is None:
                    continue
                taken.append((m.start(), m.end()))
                found.append((m.start(), kind, *parsed))
        found.sort()
        for i, (start, kind, value, shown) in enumerate(found):
            before = _words(sentence[:start])
            after = _words(sentence[start + len(shown) :])
            if len(before) < WINDOW and k > 0:
                before = tokens[k - 1][-(WINDOW - len(before)) :] + before
            if len(after) < WINDOW and k + 1 < len(sents):
                after = after + tokens[k + 1][: WINDOW - len(after)]
            own = _words(sentence[:start])[-WINDOW:] + _words(sentence[start + len(shown) :])[:WINDOW]
            near = before[-WINDOW:] + after[:WINDOW]
            context = frozenset(t for t in near if t not in UNIT_WORDS and t not in GENERIC and len(t) > 2)
            own_context = frozenset(t for t in own if t not in UNIT_WORDS and t not in GENERIC and len(t) > 2)
            out.append(Claim(doc_id, ordinal, sentence, kind, value, shown, context, own_context, i))
    return out


def _updated(doc) -> datetime:
    when = doc.updated_at or doc.created_at
    return when if when.tzinfo else when.replace(tzinfo=UTC)


# What kind of figure a question asks for. A question with none of these may be answered by
# any kind ("Can I fly business class?" turns on a flight's length).
_ASKS = [
    ("rate", re.compile(r"(?i)\b(?:days?|times?)\s+(?:a|per|each|every)\s+(?:week|month|year)\b|\bhow\s+often\b")),
    ("time_range", re.compile(r"(?i)\b(?:what\s+time|hours|timings?|opening|open|close)\b")),
    (
        "duration",
        re.compile(
            r"(?i)\bhow\s+(?:long|soon|quickly|fast)\b|\b(?:within|deadline|by\s+when|notice|probation|minutes?|days|weeks|months)\b"
        ),
    ),
    (
        "money",
        re.compile(
            r"(?i)\bhow\s+much\b|\b(?:cost|price|limit|budget|spend|amount|allowance|salary|pay|fee|reimburs\w*|expenses?|"
            r"receipts?|inr|rupees?|lakh|crore)\b"
        ),
    ),
    ("percent", re.compile(r"(?i)%|\b(?:percent|percentage|discount|margin)\b")),
]


def asked_kinds(question: str) -> set[str] | None:
    kinds = {kind for kind, pattern in _ASKS if pattern.search(question)}
    if "time_range" in kinds:
        kinds.add("duration")  # "hours" may mean either
    return kinds or None


def _covered(sentence: str, concepts: list[set[str]]) -> int:
    """How many of the question's concepts the sentence mentions."""
    present = set(tokenize(sentence))
    return sum(1 for c in concepts if c & present)


def _same_subject(a: Claim, b: Claim) -> set[str] | None:
    """The words two figures share when they're about the same thing, else None."""
    shared = a.context & b.context
    if len(shared) < 2:
        return None
    for x, y in ((a.near, b.near), (a.context, b.context)):
        union = x | y
        if union and len(x & y) / len(union) >= 0.2:
            return shared
    return None


def _aligned(xs: list[Claim], ys: list[Claim]) -> list[tuple[Claim, Claim]]:
    """Line up the figures of two similar sentences: in order when they have as many of each
    kind ("X ... and Y" with "X' ... and Y'"), otherwise each with its most similar."""
    out: list[tuple[Claim, Claim]] = []
    for kind in {c.kind for c in xs} & {c.kind for c in ys}:
        a = [c for c in xs if c.kind == kind]
        b = [c for c in ys if c.kind == kind]
        if len(a) == len(b):
            out += list(zip(a, b, strict=True))
        else:
            short, long_ = (a, b) if len(a) < len(b) else (b, a)
            for c in short:
                match = max(long_, key=lambda o: len(c.context & o.context))
                out.append((c, match) if short is a else (match, c))
    return out


def find_conflicts(question: str, concepts: list[set[str]], passages: list[tuple[int, int, str]], docs: dict) -> list[Conflict]:
    """Conflicts between passages of different documents, about what the question asks.

    `concepts` are the question's meaningful words, each as the index terms that count for it
    (the word, and what the glossary, acronyms or a typo correction add); `passages` are
    (doc_id, ordinal, text).
    """
    concepts = [{t for t in c if t not in UNIT_WORDS and t not in GENERIC} for c in concepts]
    concepts = [c for c in concepts if c]
    if not concepts:
        return []
    needed = min(2, len(concepts))
    terms = set().union(*concepts)
    kinds = asked_kinds(question)
    by_sentence: dict[int, dict[str, list[Claim]]] = {}
    for doc_id, ordinal, text in passages:
        for c in claims(doc_id, ordinal, text):
            covered = _covered(c.sentence, concepts)
            if (kinds is None or c.kind in kinds) and (covered >= needed or covered * 2 >= len(concepts)):
                by_sentence.setdefault(doc_id, {}).setdefault(c.sentence, []).append(c)

    found: list[tuple[float, Conflict]] = []
    ids = sorted(by_sentence)
    for i, x in enumerate(ids):
        for y in ids[i + 1 :]:
            best: tuple[float, Conflict] | None = None
            for sa, ca_list in by_sentence[x].items():
                for sb, cb_list in by_sentence[y].items():
                    differing = []
                    shared_all: set[str] = set()
                    for ca, cb in _aligned(ca_list, cb_list):
                        shared = _same_subject(ca, cb)
                        if shared is None:
                            continue  # not about the same thing
                        shared_all |= shared
                        if ca.value != cb.value:
                            differing.append((ca, cb))
                    if not differing:
                        continue
                    score = _covered(sa, concepts) + _covered(sb, concepts) + len(shared_all) / 10
                    if best is None or score > best[0]:
                        newer_first = _updated(docs[x]) >= _updated(docs[y])
                        pairs = [(a, b) if newer_first else (b, a) for a, b in differing]
                        overlap = sorted(shared_all & terms) + sorted(shared_all - terms)
                        best = (score, Conflict(pairs, overlap))
            if best is not None:
                found.append(best)
    return [c for _, c in sorted(found, key=lambda x: -x[0])]
