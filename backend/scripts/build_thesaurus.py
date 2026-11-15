"""Build app/data/thesaurus.json.gz, the general-English thesaurus the search falls back on.

Not needed to run VaultMind: the built file is in the repository. Run this only to rebuild it.

    pip install nltk
    python -m nltk.downloader wordnet
    python scripts/build_thesaurus.py

For every common single word it keeps two short lists from WordNet 3.0:

  same     words that mean the same in the word's most frequent senses ("bill": "invoice").
           WordNet orders senses by how often they occur; rare senses are where most wrong
           matches come from ("bill" as a bird's beak), so only the first are used.
  related  one step broader or narrower ("dinner": "meal"; "weekday": "monday")

The file maps each word to "same|related", each a space-separated list, most used first.

WordNet 3.0, Copyright 2006 by Princeton University. See app/data/WORDNET-LICENSE.
"""

import gzip
import json
import re
import sys
from pathlib import Path

from nltk.corpus import wordnet as wn

OUT = Path(__file__).resolve().parent.parent / "app" / "data" / "thesaurus.json.gz"
SENSES = {"n": 2, "v": 2, "a": 1, "s": 1, "r": 1}  # senses kept per part of speech
MAX_NARROWER = 12  # a sense with more kinds than this ("person") is too broad to list them
MAX_WORDS = 10  # per word and list
_PLAIN = re.compile(r"^[a-z]{3,}$")


def plain(lemma) -> str | None:
    name = lemma.name().lower().replace("-", "")  # "co-worker" is looked up as "coworker"
    return name if _PLAIN.match(name) else None


def frequency(word: str) -> int:
    return sum(lemma.count() for s in wn.synsets(word) for lemma in s.lemmas() if plain(lemma) == word)


def from_wordnet() -> dict[str, list[list[str]]]:
    out: dict[str, list[list[str]]] = {}
    spelled: dict[str, str] = {}
    for name in wn.all_lemma_names():
        key = name.replace("-", "")
        if _PLAIN.match(key) and (key not in spelled or "-" not in name):
            spelled[key] = name
    for word, lemma_name in sorted(spelled.items()):
        same: dict[str, int] = {}
        related: dict[str, int] = {}
        taken: dict[str, int] = {}
        for synset in wn.synsets(lemma_name):
            pos = synset.pos()
            taken[pos] = taken.get(pos, 0) + 1
            if taken[pos] > SENSES.get(pos, 1):
                continue
            own = next((lemma for lemma in synset.lemmas() if plain(lemma) == word), None)
            if taken[pos] > 1 and (own is None or own.count() == 0):
                continue  # a second sense nobody was seen using
            for lemma in synset.lemmas():
                name = plain(lemma)
                if name and name != word:
                    same[name] = max(same.get(name, 0), lemma.count())
            narrower = synset.hyponyms()
            for other in synset.hypernyms() + (narrower if len(narrower) <= MAX_NARROWER else []):
                for lemma in other.lemmas():
                    name = plain(lemma)
                    if name and name != word and name not in same:
                        related[name] = max(related.get(name, 0), lemma.count())

        def top(counts: dict[str, int]) -> list[str]:
            return [w for w, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_WORDS]]

        if same or related:
            out[word] = [top(same), top({w: c for w, c in related.items() if w not in same})]
    # Keep words somebody was seen using, or that such a word points to: the rest is rare or archaic.
    common = {w for w in out if frequency(w) > 0 or frequency(w[:2] + "-" + w[2:]) > 0}
    reachable = common | {o for w in common for group in out[w] for o in group}
    return {w: groups for w, groups in out.items() if w in reachable}


def main() -> int:
    data = {word: "|".join(" ".join(group) for group in groups) for word, groups in sorted(from_wordnet().items())}
    payload = json.dumps(data, separators=(",", ":"), sort_keys=True).encode()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with gzip.GzipFile(OUT, "wb", mtime=0) as f:
        f.write(payload)
    print(f"{len(data)} words, {len(payload) / 1e6:.1f} MB of JSON, {OUT.stat().st_size / 1e6:.2f} MB on disk -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
