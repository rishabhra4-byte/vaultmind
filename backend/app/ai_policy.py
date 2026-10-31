"""What may be sent to the AI service: the company's AI data policy, enforced in code.

Text from a document reaches the AI service only when all of these hold:

- the person asking may open it (checked before anything is searched);
- its owners haven't turned AI reading off, for the document or for its whole source;
- its classification is at or below VAULTMIND_AI_MAX_CLASSIFICATION (default: confidential,
  so restricted documents never go to the AI service);

and identity numbers, card numbers and secrets are masked first (sensitivity.redact_for_ai).
The same rules cover the summary written at upload and the description of what changed in a
new version. docs/ai-data-policy.md describes all of this for buyers and administrators.
"""

from .config import get_settings
from .models import Classification, Document
from .sensitivity import rank


def ceiling() -> Classification:
    return Classification(get_settings().ai_max_classification)


def classification_allowed(classification: Classification | str) -> bool:
    return rank(Classification(classification)) <= rank(ceiling())


def may_read(doc: Document) -> bool:
    """Whether this document's text may be sent to the AI service (for someone who can open it)."""
    return bool(doc.ai_enabled) and classification_allowed(doc.classification)


def blocked_reason(doc: Document) -> str | None:
    """Why the AI service doesn't read this document, in words, or None when it may."""
    if not classification_allowed(doc.classification):
        level = Classification(doc.classification).value.title()
        return f"{level} documents are never sent to the AI service (a company setting)."
    if not doc.ai_enabled:
        return "Its owners turned AI reading off."
    return None
