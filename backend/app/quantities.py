"""Amounts, percentages and durations, and whether a sentence's rule covers the one asked about.

"Who can approve a 25% discount?" isn't answered by the sentence that contains "25" (a list
price of INR 25,000). It is answered by "Discounts above 20% need Finance Director approval":
25% falls inside the band that sentence states. Matching numbers as words gets this wrong both
ways, so numbers that carry a unit are read as quantities and compared:

  the same kind       a percentage with percentages, money with money, a duration with durations
  inside the band     "up to 20%", "above INR 50,000", "3 to 5", "within 30 days", "at least one week"

Dependency-free and deliberately small: Indian and Western number styles (60k, 2.5 lakh,
1,50,000), a handful of currencies, and time units from minutes to years.
"""

import re
from dataclasses import dataclass

MONEY, PERCENT, DURATION, COUNT = "money", "percent", "duration", "count"

_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "ninety": 90, "hundred": 100,
}  # fmt: skip
_SCALE = {"k": 1e3, "thousand": 1e3, "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "crore": 1e7, "crores": 1e7, "million": 1e6, "m": 1e6}
_DAYS = {
    "minute": 1 / 1440, "min": 1 / 1440, "hour": 1 / 24, "hr": 1 / 24, "day": 1, "week": 7, "fortnight": 14,
    "month": 30, "quarter": 91, "year": 365,
}  # fmt: skip
_CURRENCY = r"(?:inr|rs\.?|usd|eur|gbp|rupees?|dollars?|euros?|pounds?|[₹$€£])"
_UNIT = r"(?:minutes?|mins?|hours?|hrs?|days?|weeks?|fortnights?|months?|quarters?|years?)"
_NUMBER = r"\d+(?:,\d{2,3})*(?:\.\d+)?"

_QUANTITY = re.compile(
    rf"""(?ix)
    (?<![\w.:/-])                                  # not inside "SDE-2", "v1.2", "11:30", "Q3"
    (?P<cur1>{_CURRENCY}\s?)?
    (?P<num>{_NUMBER}|\b(?:{"|".join(_WORDS)})\b)
    (?!\d|:\d|\.\d|-\d)
    (?:\s?(?P<scale>k|m|thousand|lakhs?|lac|crores?|million)\b)?
    (?:
        \s?(?P<pct>%|percent\b|per\s+cent\b)
      | [\s-]?(?:business\s+|working\s+|calendar\s+)?(?P<unit>{_UNIT})\b
      | \s?(?P<cur2>{_CURRENCY})(?![\w])
    )?
    """
)
# "for a month", "within a week", and "for every week", "each month": one of the unit.
_A_UNIT = re.compile(rf"(?i)\b(?:(?:for|within|after|in|over)\s+(?:an?|every|each)|every|each)\s+(?P<unit>{_UNIT})\b")
_CLOCK = re.compile(r"(?i)^\d{1,2}\s?(?:am|pm)\b")
_YEAR = re.compile(r"^(?:19|20)\d\d$")

# What a sentence says about a number: nothing more than it ("up to"), nothing less ("above").
_AT_MOST = re.compile(
    r"(?i)(?:\bup\s+to|\bunder|\bbelow|\bless\s+than|\bfewer\s+than|\bno\s+more\s+than|\bnot\s+more\s+than|\bat\s+most|"
    r"\bmaximum(?:\s+of)?|\bmax(?:\s+of)?|\blimit(?:ed)?\s+(?:is|of|to)|\bwithin|\bcapped\s+at|\bnot\s+exceed(?:ing)?|"
    r"\bkeep\b[^.]{0,30}\bunder)\s*(?:of\s+)?$"
)
_AT_LEAST = re.compile(
    r"(?i)(?:\babove|\bover|\bmore\s+than|\bgreater\s+than|\bexceed(?:s|ing)?|\bat\s+least|\bminimum(?:\s+of)?|\bmin(?:\s+of)?|"
    r"\bbeyond|\bafter|\bfrom)\s*(?:of\s+)?$"
)
_OR_MORE = re.compile(r"(?i)^\s*(?:or\s+(?:more|above|over)|and\s+(?:above|over)|\+)")
_OR_LESS = re.compile(r"(?i)^\s*(?:or\s+(?:less|fewer|below|under)|and\s+(?:below|under))")
_RANGE = re.compile(r"(?i)^\s*(?:to|and|-|–|—)\s*$")
_BETWEEN = re.compile(r"(?i)\bbetween\s*$")


@dataclass(frozen=True)
class Quantity:
    value: float  # money in units of currency, a duration in days, a percentage or a plain number
    kind: str
    text: str  # as written
    start: int = 0
    end: int = 0


@dataclass(frozen=True)
class Band:
    low: float
    high: float
    kind: str

    def holds(self, value: float) -> bool:
        return self.low <= value <= self.high


def _value(m: re.Match) -> float | None:
    raw = m.group("num").lower()
    if raw in _WORDS:
        return float(_WORDS[raw])
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def find(text: str) -> list[Quantity]:
    """The quantities a text mentions, in order. Years, clock times and bare small words are left out."""
    out: list[Quantity] = []
    for m in _QUANTITY.finditer(text):
        raw = m.group("num")
        value = _value(m)
        if value is None:
            continue
        money = bool(m.group("cur1") or m.group("cur2"))
        scale = (m.group("scale") or "").lower()
        if scale in ("m",) and not money:
            continue  # "5 m" is metres as often as millions
        if _CLOCK.match(text[m.start("num") :]):
            continue
        is_word = raw.lower() in _WORDS
        if scale:
            value *= _SCALE[scale]
        if m.group("pct"):
            kind = PERCENT
        elif m.group("unit") and not money:
            unit = m.group("unit").lower().rstrip("s")
            value, kind = value * _DAYS.get(unit, 1), DURATION
        elif money or scale:
            kind = MONEY
        else:
            if is_word or (_YEAR.match(raw) and "," not in raw):
                continue  # "one" is rarely a number worth matching; 2026 is a year
            kind = COUNT
        out.append(Quantity(value, kind, m.group(0).strip(), m.start(), m.end()))
    for m in _A_UNIT.finditer(text):
        unit = m.group("unit").lower().rstrip("s")
        out.append(Quantity(float(_DAYS.get(unit, 1)), DURATION, m.group(0), m.start(), m.end()))
    out.sort(key=lambda q: q.start)
    return out


def same_kind(asked: str, stated: str) -> bool:
    """A bare number in a question ("a 5,000 gift") may be money; otherwise kinds must agree."""
    return asked == stated or {asked, stated} == {MONEY, COUNT}


def bands(text: str) -> list[Band]:
    """The ranges a sentence states: "up to 20%" is 0 to 20, "above 20%" is 20 and up, "3 to 5" is 3 to 5,
    and a number with nothing around it is just itself."""
    found = find(text)
    out: list[Band] = []
    skip = -1
    for i, q in enumerate(found):
        if i == skip:
            continue
        before = text[max(0, q.start - 40) : q.start]
        after = text[q.end : q.end + 24]
        nxt = found[i + 1] if i + 1 < len(found) else None
        between = text[q.end : nxt.start] if nxt else ""
        if nxt and same_kind(q.kind, nxt.kind) and nxt.value >= q.value and (_RANGE.match(between) or _BETWEEN.search(before)):
            kind = nxt.kind if q.kind == COUNT else q.kind
            # "14 to 22" then "lakh" or "weeks" on the second number: both share its unit.
            low = q.value * (nxt.value / _plain(nxt)) if q.kind == COUNT and nxt.kind != COUNT and _plain(nxt) else q.value
            out.append(Band(low, nxt.value, kind))
            skip = i + 1
        elif _AT_MOST.search(before) or _OR_LESS.match(after):
            out.append(Band(0.0, q.value, q.kind))
        elif _AT_LEAST.search(before) or _OR_MORE.match(after):
            out.append(Band(q.value, float("inf"), q.kind))
        else:
            out.append(Band(q.value, q.value, q.kind))
    return out


def _plain(q: Quantity) -> float:
    """The number as written, before its unit or scale was applied."""
    m = re.match(_NUMBER, q.text.lstrip("₹$€£ ").lower().removeprefix("inr").removeprefix("rs.").removeprefix("rs").strip())
    try:
        return float(m.group(0).replace(",", "")) if m else 0.0
    except ValueError:
        return 0.0


FITS = 1.0  # the sentence's rule covers the amount asked about
NEAR = 0.5  # the sentence states a limit of the same kind, which the amount falls outside


def fit(asked: Quantity, stated: list[Band]) -> float:
    """How well a sentence speaks to the amount asked about."""
    best = 0.0
    for band in stated:
        if not same_kind(asked.kind, band.kind):
            continue
        if band.holds(asked.value):
            return FITS
        if band.low != band.high:  # a limit, not just some other number
            best = NEAR
    return best
