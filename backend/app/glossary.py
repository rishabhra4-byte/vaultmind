"""Company glossary: words people use for the same thing ("PTO", "time off", "leave").

The assistant adds the other words of a group to its search, so a question about "PTO" finds
the Leave Policy. A few common groups are built in; admins add their company's own terms
(team nicknames, product code names, internal acronyms) on the Admin page.
"""

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import GlossaryTerm

# Built in, always on. Each entry: the main term and the other ways people say it.
DEFAULT_TERMS: dict[str, list[str]] = {
    "leave": ["time off", "pto", "vacation", "holiday", "absence"],
    "work from home": ["wfh", "remote work", "working remotely"],
    "compensation": ["salary", "pay", "ctc", "pay band", "salary band"],
    "expense": ["reimbursement", "claim", "per diem", "travel allowance"],
    "onboarding": ["new joiner", "new hire", "first week", "getting started"],
    "one-on-one": ["1:1", "one on one", "1on1"],
    "performance review": ["appraisal", "review cycle", "perf review"],
    "incident": ["outage", "sev1", "production issue", "on-call", "pager"],
    "laptop": ["equipment", "hardware", "device"],
    "okr": ["objectives", "key results", "quarterly goals"],
    "it support": ["helpdesk", "help desk", "tech support"],
    "notice period": ["resignation", "last working day"],
    "insurance": ["health cover", "medical cover", "mediclaim"],
    "wi-fi": ["wifi", "wireless", "internet access"],
}

MAX_ADDED = 8  # keep the search focused


@dataclass(frozen=True)
class Entry:
    term: str
    synonyms: tuple[str, ...]
    note: str = ""
    builtin: bool = False
    id: int | None = None

    @property
    def phrases(self) -> tuple[str, ...]:
        return (self.term, *self.synonyms)


def entries(db: Session) -> list[Entry]:
    """Company terms first (they can refine a built-in term), then the built-in ones."""
    out = [
        Entry(t.term, tuple(t.synonyms or []), t.note, False, t.id)
        for t in db.scalars(select(GlossaryTerm).order_by(GlossaryTerm.term))
    ]
    out += [Entry(term, tuple(syns), builtin=True) for term, syns in DEFAULT_TERMS.items()]
    return out


def _normal(text: str) -> str:
    return " " + re.sub(r"[^a-z0-9:+#]+", " ", text.lower()).strip() + " "


def _mentions(normal_text: str, phrase: str) -> bool:
    p = _normal(phrase)
    return len(p.strip()) > 1 and p in normal_text


def expand(question: str, glossary: list[Entry]) -> tuple[str, list[str]]:
    """The search query with related words added, and the words that were added."""
    text = _normal(question)
    added: list[str] = []
    for entry in glossary:
        if any(_mentions(text, p) for p in entry.phrases):
            for phrase in entry.phrases:
                if not _mentions(text, phrase) and phrase.lower() not in added:
                    added.append(phrase.lower())
    added = added[:MAX_ADDED]
    return (f"{question} {' '.join(added)}" if added else question), added


def clean_phrases(values: list[str]) -> list[str]:
    """Trim, drop empties and duplicates, keep order."""
    out: list[str] = []
    for v in values:
        v = re.sub(r"\s+", " ", v).strip()[:60]
        if v and v.lower() not in {o.lower() for o in out}:
            out.append(v)
    return out[:20]
