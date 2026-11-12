"""General English, for a word the documents never use.

"Do team dinners need approval?" when the policy says "team meals"; "a supplier sent a bill"
when the process says "invoice". The company glossary (glossary.py) knows the company's own
words; this knows ordinary ones, from WordNet 3.0 (built once by scripts/build_thesaurus.py;
nothing is downloaded when VaultMind runs; licence in data/WORDNET-LICENSE). Each word has
two short lists:

  same     means the same in one of the word's common senses ("bill": "invoice")
  related  one step broader or narrower ("dinner": "meal")

It is only a fallback. A thesaurus that widens every word finds "director" for "manager" and
"support" for "approve", and answers the wrong question. So retrieval.py asks it only about a
word that appears in none of the documents the person may open, and a stand-in counts for less
than the word itself would: half when it means the same, about a third when it is only broader
or narrower. It can support an answer whose other words match, but never carry one alone.
"""

import gzip
import json
import logging
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

FILE = Path(__file__).resolve().parent / "data" / "thesaurus.json.gz"
SAME = 0.5  # a word that means the same in one of its common senses
RELATED = 0.35  # one step broader or narrower


@lru_cache(maxsize=1)
def _data() -> dict[str, str]:
    try:
        with gzip.open(FILE, "rt", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as exc:  # the search works without it, just less well
        log.warning("General thesaurus not loaded (%s): %s", FILE.name, exc)
        return {}


def _base_forms(word: str) -> list[str]:
    """The word and what it may be a form of: "dinners" -> dinner, "bullies" -> bully."""
    forms = [word]
    if word.endswith("ies") and len(word) > 4:
        forms.append(word[:-3] + "y")
    if word.endswith("es") and len(word) > 4:
        forms.append(word[:-2])
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        forms.append(word[:-1])
    for suffix in ("ing", "ed"):
        if word.endswith(suffix) and len(word) > len(suffix) + 2:
            root = word[: -len(suffix)]
            forms += [root, root + "e"]
            if len(root) > 2 and root[-1] == root[-2]:
                forms.append(root[:-1])  # "shipped" -> ship
    return forms


def stand_ins(word: str) -> list[tuple[str, float]]:
    """Ordinary words that could stand in for `word`, with how much each counts, best first."""
    data = _data()
    for form in _base_forms(word.lower()):  # the word as typed first, then what it is a form of
        entry = data.get(form)
        if entry:
            groups = zip(entry.split("|"), (SAME, RELATED), strict=False)
            return [(other, counts) for group, counts in groups for other in group.split()]
    return []
