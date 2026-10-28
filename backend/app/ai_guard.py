"""What the AI service may read, and the record of what was sent to it.

A document's text goes to the AI service only if all of these allow it:
- AI processing isn't stopped (Compliance → Controls, an emergency switch);
- the document is active (not quarantined or archived);
- AI reading is on for the document and for its source;
- its classification is within the company limit (by default, Restricted documents never go).

Every call to the AI service is recorded in `ai_requests`: what for, which documents and
passages, how many characters and masked identifiers, but never the text itself.
"""

from collections.abc import Iterable
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from . import policies
from .config import get_settings
from .models import AiRequest, Document, DocumentState, User
from .sensitivity import mask_for_ai, rank

LEVEL_LABEL = {
    "none": "no documents",
    "public": "Public documents only",
    "internal": "up to Internal",
    "confidential": "up to Confidential",
    "restricted": "every classification",
}


def paused() -> bool:
    return bool(policies.get("ai_paused"))


def limit() -> str:
    return str(policies.get("max_ai_classification"))


def may_read(doc: Document) -> bool:
    return why_not(doc) is None


def why_not(doc: Document) -> str | None:
    """Why the AI service may not read this document, in plain words (None if it may)."""
    if paused():
        return "An administrator has stopped all AI processing."
    if doc.state != DocumentState.ACTIVE:
        return "This document is quarantined or archived."
    if not doc.ai_enabled or (doc.source is not None and not doc.source.ai_enabled):
        return "AI reading is turned off for this document."
    level = limit()
    if level == "none" or rank(doc.classification) > rank(level):
        return f"Company policy keeps {doc.classification.title()} documents away from the AI service."
    return None


def endpoint() -> str:
    url = get_settings().anthropic_base_url
    return (urlparse(url).hostname or url)[:255] if url else "api.anthropic.com"


class Sent:
    """The documents (and passages) going into one request to the AI service."""

    def __init__(self) -> None:
        self.docs: dict[int, dict] = {}
        self.masked = 0

    def mask(self, text: str) -> str:
        text, n = mask_for_ai(text)
        self.masked += n
        return text

    def add(self, doc: Document, passages: Iterable[int] | None = None) -> None:
        """Record the document; `passages` are the ordinals sent, None for its whole text."""
        entry = self.docs.setdefault(
            doc.id,
            {
                "id": doc.id,
                "title": doc.title[:200],
                "classification": doc.classification,
                "source_id": doc.source_id,
                "passages": [],
            },
        )
        if passages is None:
            entry["passages"] = None
        elif entry["passages"] is not None:
            entry["passages"] = sorted(set(entry["passages"]) | set(passages))


def record(
    db: Session,
    user: User | None,
    purpose: str,
    sent: Sent | None,
    chars: int,
    outcome: str,
    model: str = "",
) -> AiRequest:
    """Write down one request to the AI service. The caller's transaction commits it."""
    row = AiRequest(
        user_id=user.id if user else None,
        purpose=purpose,
        model=model[:120],
        endpoint=endpoint(),
        documents=list(sent.docs.values()) if sent else [],
        chars=chars,
        masked=sent.masked if sent else 0,
        outcome=outcome[:40],
    )
    db.add(row)
    return row


def message_chars(system: str, messages: list[dict]) -> int:
    return len(system) + sum(len(str(m.get("content", ""))) for m in messages)
