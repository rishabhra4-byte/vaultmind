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
    get got need needs want wants please tell show find give know us let any every""".split()
)

_WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


def stem(word: str) -> str:
    """A deliberately tiny suffix stripper: enough to match policy/policies, review/reviews."""
    if len(word) <= 4 or word.isdigit():
        return word
    for suffix, repl in (("ies", "y"), ("sses", "ss"), ("ing", ""), ("ed", ""), ("es", "e"), ("s", "")):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            if suffix == "s" and word.endswith("ss"):
                return word
            return word[: -len(suffix)] + repl
    return word


_COMPOUND = re.compile(r"\b([a-z]+)-(\d+)\b")  # "SDE-2", "H-1" -> "sde2", so levels match exactly


def tokenize(text: str) -> list[str]:
    lowered = text.lower()
    tokens = [stem(w) for w in _WORD.findall(lowered) if w not in STOPWORDS and len(w) > 1]
    return tokens + [a + b for a, b in _COMPOUND.findall(lowered)]


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


def best_snippet(text: str, query: str, max_chars: int = 260) -> str:
    """The sentence (plus a neighbour) that overlaps most with the query."""
    sents = sentences(text)
    if not sents:
        return ""
    q = set(tokenize(query))
    best_i = max(range(len(sents)), key=lambda i: (len(q & set(tokenize(sents[i]))), -i))
    snippet = sents[best_i]
    if len(snippet) < 120 and best_i + 1 < len(sents):
        snippet += " " + sents[best_i + 1]
    return snippet if len(snippet) <= max_chars else snippet[: max_chars - 1].rsplit(" ", 1)[0] + "…"
