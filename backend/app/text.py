"""Small, dependency-free text utilities: tokenising, chunking, keywords, summaries."""

import re
from collections import Counter

STOPWORDS = frozenset(
    """a about above after again against all am an and any are as at be because been before being
    below between both but by can could did do does doing down during each few for from further had
    has have having he her here hers herself him himself his how i if in into is it its itself just
    me more most my myself no nor not now of off on once only or other our ours ourselves out over
    own same she should so some such than that the their theirs them themselves then there these
    they this those through to too under until up very was we were what when where which while who
    whom why will with would you your yours yourself yourselves also may must per via within without
    get got need needs want wants please tell show find give know us let any every
    don doesn didn isn aren wasn weren won wouldn shouldn couldn hasn haven hadn ll ve re""".split()
)

# Apostrophes split words ("what's" is "what" and "s"), so contractions become stop words.
_WORD = re.compile(r"[a-z0-9]+")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


# ---------------------------------------------------------------- stemming (Porter, 1980)
# Matches the forms of a word to each other: require / required / requirement,
# approve / approval / approver, rating / ratings / rated, policy / policies, travel / travelling.


def _cons(w: str, i: int) -> bool:
    ch = w[i]
    if ch in "aeiou":
        return False
    if ch == "y":
        return i == 0 or not _cons(w, i - 1)
    return True


def _measure(stem: str) -> int:
    """How many vowel-consonant sequences the stem has (Porter's m)."""
    n, i, size = 0, 0, len(stem)
    while i < size and _cons(stem, i):
        i += 1
    while i < size:
        while i < size and not _cons(stem, i):
            i += 1
        if i >= size:
            break
        n += 1
        while i < size and _cons(stem, i):
            i += 1
    return n


def _has_vowel(stem: str) -> bool:
    return any(not _cons(stem, i) for i in range(len(stem)))


def _double_cons(w: str) -> bool:
    return len(w) >= 2 and w[-1] == w[-2] and _cons(w, len(w) - 1)


def _cvc(w: str) -> bool:
    return len(w) >= 3 and _cons(w, len(w) - 3) and not _cons(w, len(w) - 2) and _cons(w, len(w) - 1) and w[-1] not in "wxy"


def _replace(w: str, rules: tuple[tuple[str, str], ...], min_m: int) -> str:
    for suffix, repl in rules:
        if w.endswith(suffix):
            base = w[: -len(suffix)]
            return base + repl if _measure(base) > min_m - 1 else w
    return w


_STEP2 = (
    ("ational", "ate"),
    ("tional", "tion"),
    ("enci", "ence"),
    ("anci", "ance"),
    ("izer", "ize"),
    ("bli", "ble"),
    ("alli", "al"),
    ("entli", "ent"),
    ("eli", "e"),
    ("ousli", "ous"),
    ("ization", "ize"),
    ("ation", "ate"),
    ("ator", "ate"),
    ("alism", "al"),
    ("iveness", "ive"),
    ("fulness", "ful"),
    ("ousness", "ous"),
    ("aliti", "al"),
    ("iviti", "ive"),
    ("biliti", "ble"),
    ("logi", "log"),
)
_STEP3 = (("icate", "ic"), ("ative", ""), ("alize", "al"), ("iciti", "ic"), ("ical", "ic"), ("ful", ""), ("ness", ""))
_STEP4 = (
    "ement",
    "ment",
    "ance",
    "ence",
    "able",
    "ible",
    "ant",
    "ent",
    "ism",
    "ate",
    "iti",
    "ous",
    "ive",
    "ize",
    "al",
    "er",
    "ic",
    "ou",
)


# Irregular forms Porter can't relate to their verb ("sent" and "sends").
IRREGULAR = {
    "sent": "send",
    "paid": "pay",
    "took": "take",
    "taken": "take",
    "gave": "give",
    "given": "give",
    "made": "make",
    "held": "hold",
    "kept": "keep",
    "met": "meet",
    "ran": "run",
    "went": "go",
    "gone": "go",
    "wrote": "write",
    "written": "write",
    "chose": "choose",
    "chosen": "choose",
    "bought": "buy",
    "brought": "bring",
    "spent": "spend",
    "thought": "think",
    "told": "tell",
    "found": "find",
    "built": "build",
    "began": "begin",
    "begun": "begin",
    "known": "know",
    "knew": "know",
    "lost": "lose",
    "sold": "sell",
    "led": "lead",
    "won": "win",
    "flew": "fly",
    "flown": "fly",
    "fell": "fall",
    "felt": "feel",
    "dealt": "deal",
    "meant": "mean",
    "understood": "understand",
}


def stem(word: str) -> str:
    """The Porter stem of a lower-case word (numbers and short words are left as they are)."""
    w = IRREGULAR.get(word, word)
    if len(w) <= 2 or not w.isalpha():
        return w
    # 1a: plurals
    if w.endswith("sses"):
        w = w[:-2]
    elif w.endswith("ies"):
        w = w[:-2]
    elif w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]
    # 1b: -ed, -ing
    if w.endswith("eed"):
        if _measure(w[:-3]) > 0:
            w = w[:-1]
    else:
        for suffix in ("ed", "ing"):
            if w.endswith(suffix) and _has_vowel(w[: -len(suffix)]):
                w = w[: -len(suffix)]
                if w.endswith(("at", "bl", "iz")):
                    w += "e"
                elif _double_cons(w) and w[-1] not in "lsz":
                    w = w[:-1]
                elif _measure(w) == 1 and _cvc(w):
                    w += "e"
                break
    # 1c: -y to -i
    if w.endswith("y") and _has_vowel(w[:-1]):
        w = w[:-1] + "i"
    w = _replace(w, _STEP2, 1)
    w = _replace(w, _STEP3, 1)
    # 4: remove suffixes from long stems
    for suffix in _STEP4:
        if w.endswith(suffix):
            if _measure(w[: -len(suffix)]) > 1:
                w = w[: -len(suffix)]
            break
    else:
        if w.endswith("ion") and w[:-3].endswith(("s", "t")) and _measure(w[:-3]) > 1:
            w = w[:-3]
    # 5: a final -e, and -ll
    if w.endswith("e"):
        m = _measure(w[:-1])
        if m > 1 or (m == 1 and not _cvc(w[:-1])):
            w = w[:-1]
    if w.endswith("ll") and _measure(w) > 1:
        w = w[:-1]
    return w


def edit_distance(a: str, b: str, limit: int) -> int:
    """Damerau-Levenshtein distance (adjacent swaps count once), or limit + 1 if it's larger."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2: list[int] = []
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        best = cur[0]
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
            best = min(best, cur[j])
        if best > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[-1] if prev[-1] <= limit else limit + 1


def words(text: str) -> list[str]:
    """Lower-case words, unstemmed, without stop words (for typo matching and acronyms)."""
    return [w for w in _WORD.findall(text.lower()) if w not in STOPWORDS and len(w) > 1]


_COMPOUND = re.compile(r"\b([a-z]+)-(\d+)\b")  # "SDE-2", "H-1" -> "sde2", so levels match exactly


def tokenize(text: str) -> list[str]:
    lowered = text.lower()
    tokens = [stem(w) for w in _WORD.findall(lowered) if w not in STOPWORDS and len(w) > 1]
    return tokens + [a + b for a, b in _COMPOUND.findall(lowered)]


# Words that say what kind of answer is wanted rather than what it's about. They count for
# little when judging whether a passage answers a question.
GENERIC = frozenset(
    stem(w)
    for w in """many much long often soon quickly quick fast big far time times way happen happens need needed
    required allowed can may should must rule rules policy policies process guideline guidelines document documents
    company our anyone someone something thing things kind type does do get""".split()
)
# Everyday words. When no document uses one, it's more likely the question phrases something
# differently ("what *fields* should an error *contain*") than that the subject is missing,
# which is what an unknown topic word ("hackathon", "sabbatical") usually means. Concrete
# nouns are left out on purpose: "car", "dog", "gym" are subjects.
EVERYDAY = frozenset(
    stem(w)
    for w in """able about accept access account act action actual add address affect after again against agree ahead allow
    almost alone along already also always amount another answer anything anyway appear apply approach area around arrange
    arrive ask attend available average avoid away back bad base basic become before begin behind believe best better
    between beyond big bit both break bring broken build busy buy call cancel care carry case cause certain chance change
    charge check choose clear close come common complete concern condition confirm consider contain content continue control
    correct cost count cover create current cut deal decide decision deep define deliver depend describe detail different
    difficult direct discuss done down during each early easy effect either else end enough ensure enter entire event ever
    every exact example except exist expect explain extra fact fail fair fall far fast feel few field figure fill final
    fine finish first fit fix follow form forward free full further general give goal going good great group grow handle
    hard have head hear help high hold home hope however huge idea important include increase instead interest involve issue
    item job join just keep key kind large last late later lead learn least leave left less let level life light like
    likely limited line list little live local long look lose lot low main maintain major make manage many mark matter
    maybe mean measure meet mention method middle might mind minor miss mode model moment more most move much must name
    near nearly necessary need never new next normal note nothing notice number object obtain occur offer often once only
    open option order other others outside overall own part particular pass past pay people per perhaps period person
    place plan point possible post practice prefer prepare present pretty prevent previous probably problem proceed produce
    proper provide public pull purpose push put quick quickly quite raise range rate rather reach read ready real really
    reason receive recent record reduce regard related release remain remember remove repeat replace report require rest
    result return right rise role run safe safely same save say second see seem send sense sent separate serious serve set
    several share short should show side sign similar simple since single singular situation size small soon sort sound
    speak special specific stand start state status stay step still stop store straight subject submit suggest suitable
    supply support suppose sure system take talk task tell tend term test thing think through throw time today together
    too top total toward track true try turn type under understand unit until upon usage use useful usual value various
    very view visit wait walk want watch way well whole wide within without wonder word work worth would write wrong year
    yes yet""".split()
)
# A question asking for an amount, a duration, a time or a day.
QUANTITY = re.compile(
    r"(?i)\b(?:how\s+(?:many|much|long|often|soon|quickly|fast|big|far|old)|when|what\s+time|which\s+days?|"
    r"up\s+to\s+what|what(?:'s|\s+is|\s+are)\s+(?:the|our|my)\s+(?:\w+\s+)?(?:limit|band|range|price|budget|rate|deadline|"
    r"target|cap|allowance|hours|amount|threshold|terms))\b"
)
_NUMBERISH = re.compile(
    r"(?i)\d|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|half|monday|tuesday|wednesday|thursday|"
    r"friday|saturday|sunday|weekday|weekend|january|february|march|april|may|june|july|august|september|october|"
    r"november|december|daily|weekly|monthly|yearly|annual|annually)\b"
)


def has_quantity(text: str) -> bool:
    return bool(_NUMBERISH.search(text))


def passage_sentences(text: str) -> list[tuple[str, str]]:
    """(heading, sentence) pairs: each sentence with the markdown heading it sits under."""
    out: list[tuple[str, str]] = []
    heading = ""
    for block in re.split(r"\n\s*\n", text):
        lines = block.strip().splitlines()
        body: list[str] = []
        for line in lines:
            m = re.match(r"^#+\s*(.*)$", line.strip())
            if m:
                if body:
                    out += [(heading, s) for s in sentences("\n".join(body))]
                    body = []
                heading = m.group(1).strip()
            else:
                body.append(line)
        if body:
            out += [(heading, s) for s in sentences("\n".join(body))]
    return out


def sentences(text: str) -> list[str]:
    # Drop markdown headings and turn list bullets into sentence breaks.
    body = re.sub(r"^#+ .*$", "", text, flags=re.M)
    body = re.sub(r"^\s*(?:[-*]|\d+\.)\s+(.*?)\.?\s*$", r"\1.", body, flags=re.M)
    flat = re.sub(r"\s+", " ", body).strip()
    return [s.strip() for s in _SENTENCE.split(flat) if s.strip()]


def chunk_text(text: str, target_words: int = 140) -> list[str]:
    """Pack paragraphs into chunks of roughly `target_words`, splitting very long paragraphs."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current: list[str] = []
    count = 0
    for para in paragraphs:
        words = para.split()
        if len(words) > target_words * 1.5:
            if current:
                chunks.append("\n\n".join(current))
                current, count = [], 0
            for i in range(0, len(words), target_words):
                chunks.append(" ".join(words[i : i + target_words]))
            continue
        if count + len(words) > target_words and current:
            chunks.append("\n\n".join(current))
            current, count = [], 0
        current.append(para)
        count += len(words)
    if current:
        chunks.append("\n\n".join(current))
    return chunks or ([text.strip()] if text.strip() else [])


def keywords(text: str, limit: int = 6) -> list[str]:
    """Most frequent meaningful words (unstemmed, for display as tags)."""
    words = [w for w in _WORD.findall(text.lower()) if w not in STOPWORDS and len(w) > 3 and not w.isdigit()]
    return [w for w, _ in Counter(words).most_common(limit)]


def extractive_summary(text: str, max_sentences: int = 2, max_chars: int = 320) -> str:
    """Pick the opening sentences that carry the document's most frequent terms."""
    sents = sentences(text)
    if not sents:
        return ""
    freq = Counter(tokenize(text))
    scored = []
    for i, s in enumerate(sents[:25]):
        toks = tokenize(s)
        if len(toks) < 4:
            continue
        score = sum(freq[t] for t in set(toks)) / (len(toks) ** 0.5) + (2.0 if i < 2 else 0.0)
        scored.append((score, i, s))
    best = sorted(sorted(scored, reverse=True)[:max_sentences], key=lambda x: x[1])
    summary = " ".join(s for _, _, s in best) or sents[0]
    return summary if len(summary) <= max_chars else summary[: max_chars - 1].rsplit(" ", 1)[0] + "…"


def key_points(passages: list[tuple[int, str]], limit: int = 5) -> list[tuple[int, str]]:
    """The sentences that carry a document's main terms, in reading order, with their passage number."""
    found = [(ordinal, s) for ordinal, text in passages for s in sentences(text)]
    if not found:
        return []
    freq = Counter(t for _, s in found for t in tokenize(s))
    scored = []
    for i, (ordinal, s) in enumerate(found):
        toks = tokenize(s)
        if len(toks) < 4 or len(s) > 400:
            continue
        scored.append((sum(freq[t] for t in set(toks)) / (len(toks) ** 0.5), i, ordinal, s))
    best = sorted(sorted(scored, reverse=True)[:limit], key=lambda x: x[1])
    return [(ordinal, s) for _, _, ordinal, s in best]


def best_snippet(text: str, query: str, max_chars: int = 260) -> str:
    """The sentence (plus a neighbour) that best matches the query.

    Each matched word counts for 1 / (times it occurs in the passage), so a word that
    pins down one sentence ("limit") outweighs one repeated throughout ("travel").
    """
    sents = sentences(text)
    if not sents:
        return ""
    q = set(tokenize(query))
    sent_terms = [set(tokenize(s)) for s in sents]
    df = Counter(t for s in sents for t in tokenize(s) if t in q)

    def score(i: int) -> tuple[float, int]:
        return (sum(1 / df[t] for t in sent_terms[i] & q), -i)

    best_i = max(range(len(sents)), key=score)
    snippet = sents[best_i]
    if len(snippet) < 120 and best_i + 1 < len(sents):
        snippet += " " + sents[best_i + 1]
    return snippet if len(snippet) <= max_chars else snippet[: max_chars - 1].rsplit(" ", 1)[0] + "…"
