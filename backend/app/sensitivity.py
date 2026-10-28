"""Sensitivity scanner.

Runs on every upload. It looks for personal data, credentials and finance/HR language,
then suggests a minimum classification. If an uploader picks something lower than the
suggestion, the API refuses unless they explicitly acknowledge the warning, which is how
a payroll sheet accidentally marked "public" gets caught.
"""

import re
from dataclasses import dataclass, field

from .models import Classification

_RANK = {
    Classification.PUBLIC: 0,
    Classification.INTERNAL: 1,
    Classification.CONFIDENTIAL: 2,
    Classification.RESTRICTED: 3,
}


def rank(c: Classification | str) -> int:
    return _RANK[Classification(c)]


def _luhn_ok(number: str) -> bool:
    digits = [int(d) for d in number][::-1]
    total = sum(d if i % 2 == 0 else (d * 2 - 9 if d * 2 > 9 else d * 2) for i, d in enumerate(digits))
    return total % 10 == 0


@dataclass(frozen=True)
class Detector:
    flag: str
    label: str
    pattern: re.Pattern
    level: Classification
    validate: object = None  # optional callable(match_text) -> bool


DETECTORS = [
    Detector(
        "api_key",
        "API key or secret token",
        re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{30,}|xox[bp]-[A-Za-z0-9-]{20,})\b"),
        Classification.RESTRICTED,
    ),
    Detector(
        "password", "Password written in plain text", re.compile(r"(?i)\bpassword\s*[:=]\s*\S{6,}"), Classification.RESTRICTED
    ),
    Detector("aadhaar", "Aadhaar number", re.compile(r"\b[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}\b"), Classification.RESTRICTED),
    Detector("pan", "PAN number", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"), Classification.RESTRICTED),
    Detector(
        "card",
        "Payment card number",
        re.compile(r"\b(?:\d[ -]?){13,19}\b"),
        Classification.RESTRICTED,
        validate=lambda m: _luhn_ok(re.sub(r"\D", "", m)) and 13 <= len(re.sub(r"\D", "", m)) <= 19,
    ),
    Detector(
        "compensation",
        "Salary or compensation details",
        re.compile(r"(?i)\b(?:salary|salaries|compensation|ctc|payroll|bonus(?:es)?|pay band|salary band)s?\b"),
        Classification.CONFIDENTIAL,
    ),
    Detector(
        "financials",
        "Unreleased financial figures",
        re.compile(r"(?i)\b(?:revenue|ebitda|burn rate|runway|gross margin|forecast)\b"),
        Classification.CONFIDENTIAL,
    ),
    Detector(
        "confidential_marker",
        "Marked confidential",
        re.compile(r"(?i)\b(?:strictly confidential|confidential|do not distribute|internal only|under nda)\b"),
        Classification.CONFIDENTIAL,
    ),
    Detector("phone", "Phone number", re.compile(r"(?<!\d)(?:\+91[ -]?)?[6-9]\d{4}[ -]?\d{5}(?!\d)"), Classification.INTERNAL),
    Detector(
        "email", "Email address", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), Classification.INTERNAL
    ),
]


@dataclass
class ScanResult:
    suggested: Classification = Classification.PUBLIC
    findings: list[dict] = field(default_factory=list)

    @property
    def flags(self) -> list[str]:
        return [f["flag"] for f in self.findings]


def scan(text: str) -> ScanResult:
    result = ScanResult()
    for det in DETECTORS:
        matches = [m.group(0) for m in det.pattern.finditer(text)]
        if det.validate:
            matches = [m for m in matches if det.validate(m)]
        if not matches:
            continue
        result.findings.append({"flag": det.flag, "label": det.label, "count": len(matches), "level": det.level.value})
        if rank(det.level) > rank(result.suggested):
            result.suggested = det.level
    # Several kinds of personal data together is more sensitive than any one alone.
    personal = {"email", "phone"} & set(result.flags)
    if len(personal) == 2 and rank(result.suggested) < rank(Classification.CONFIDENTIAL):
        result.suggested = Classification.CONFIDENTIAL
    return result


def floor_for_flags(flags: list[str]) -> Classification:
    """The lowest classification a document with these scanner flags should have."""
    levels = {d.flag: d.level for d in DETECTORS}
    floor = Classification.PUBLIC
    for flag in flags or []:
        level = levels.get(flag)
        if level is not None and rank(level) > rank(floor):
            floor = level
    if {"email", "phone"} <= set(flags or []) and rank(floor) < rank(Classification.CONFIDENTIAL):
        floor = Classification.CONFIDENTIAL
    return floor


def redact_for_ai(text: str) -> str:
    """Mask identity numbers, card numbers and secrets before text is sent to the AI service.

    Names, emails and phone numbers stay: "who do I contact?" is one of the most common
    questions, and the person asking can open the document anyway.
    """
    return mask_for_ai(text)[0]


def mask_for_ai(text: str) -> tuple[str, int]:
    """`redact_for_ai`, also counting how many identifiers were masked (for the AI data record)."""
    count = 0
    for det in DETECTORS:
        if det.level != Classification.RESTRICTED:
            continue

        def swap(m: re.Match, d: Detector = det) -> str:
            nonlocal count
            if d.validate and not d.validate(m.group(0)):
                return m.group(0)
            count += 1
            return f"[{d.label.lower()} removed]"

        text = det.pattern.sub(swap, text)
    return text, count


def redact(text: str) -> str:
    """Mask identity numbers, secrets and contact details, e.g. in auto-generated summaries."""
    for det in DETECTORS:
        if det.level == Classification.CONFIDENTIAL:
            continue  # words like "salary" are context, not data
        text = det.pattern.sub(lambda m, d=det: "[redacted]" if not d.validate or d.validate(m.group(0)) else m.group(0), text)
    return text
