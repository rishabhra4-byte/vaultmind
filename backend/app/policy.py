"""Access policy engine.

Every permission decision in the app goes through this module. The functions are pure
(no database calls) so the rules are easy to read, test and reason about; callers load
the user's active grants once and pass them in.

The decision returns a reason code and a human explanation, which powers the
"Why can't I open this?" feature and the assistant's locked-document hints.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from .models import Classification, Document, Position, User


@dataclass(frozen=True)
class Decision:
    allowed: bool
    code: str
    reason: str
    required_position: int | None = None

    def __bool__(self) -> bool:
        return self.allowed


def _label(position: int) -> str:
    return Position(position).label


def can_view(user: User, doc: Document, granted_doc_ids: set[int] | frozenset[int] = frozenset()) -> Decision:
    if not user.is_active:
        return Decision(False, "inactive", "Your account is deactivated.")
    if user.is_admin:
        return Decision(True, "admin", "Administrators can open every document.")
    if doc.uploaded_by == user.id:
        return Decision(True, "uploader", "You uploaded this document.")
    if doc.id in granted_doc_ids:
        return Decision(True, "grant", "You have a temporary access grant for this document.")

    classification = Classification(doc.classification)
    if classification is Classification.RESTRICTED:
        return Decision(
            False,
            "restricted",
            "This document is restricted to named people. Request access from its owning team.",
        )

    in_scope = (
        classification is Classification.PUBLIC
        or user.team_id == doc.owner_team_id
        or user.team_id in doc.shared_team_ids
        # Executives have company-wide visibility for anything that isn't restricted.
        or user.position >= Position.EXECUTIVE
    )
    if not in_scope:
        return Decision(
            False,
            "team",
            f"This document belongs to {doc.owner_team.name} and hasn't been shared with your team.",
            required_position=doc.min_position,
        )

    if user.position < doc.min_position:
        return Decision(
            False,
            "position",
            f"This document needs {_label(doc.min_position)} level or above; you are {_label(user.position)}.",
            required_position=doc.min_position,
        )

    if classification is Classification.PUBLIC:
        return Decision(True, "public", "Public documents are open to everyone in the company.")
    if user.team_id == doc.owner_team_id:
        return Decision(True, "team", f"Your team ({doc.owner_team.name}) owns this document.")
    if user.team_id in doc.shared_team_ids:
        return Decision(True, "shared", "This document is shared with your team.")
    return Decision(True, "executive", "Executives can view all non-restricted documents.")


def can_manage(user: User, doc: Document, granted_doc_ids: set[int] | frozenset[int] = frozenset()) -> bool:
    """Edit metadata, change classification, share or delete."""
    if user.is_admin or doc.uploaded_by == user.id:
        return True
    return user.team_id == doc.owner_team_id and user.position >= Position.MANAGER and bool(can_view(user, doc, granted_doc_ids))


def can_approve(user: User, doc: Document, requester_id: int) -> bool:
    """Who may approve an access request for this document."""
    if user.id == requester_id or not user.is_active:
        return False
    if user.is_admin or doc.uploaded_by == user.id:
        return True
    needed = max(Position.MANAGER, doc.min_position)
    if doc.classification == Classification.RESTRICTED:
        needed = max(needed, Position.DIRECTOR)
    return user.team_id == doc.owner_team_id and user.position >= needed


def is_discoverable_to(user: User, doc: Document) -> bool:
    """May this user see that the document *exists* (title only), even if they can't open it?"""
    return doc.discoverable and doc.classification != Classification.RESTRICTED


def grant_is_active(expires_at: datetime | None, revoked: bool, now: datetime | None = None) -> bool:
    if revoked:
        return False
    if expires_at is None:
        return True
    now = now or datetime.now(UTC)
    if expires_at.tzinfo is None:  # SQLite returns naive datetimes
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at > now
