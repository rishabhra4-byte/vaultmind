"""Narrowing a search to some documents: a team, where they come from, who added them, how recent.

The library and the assistant use the same filters, so "Engineering documents updated in the
last 30 days" means the same thing in both. Filters only ever narrow what the person may
already open; they never widen it.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .models import Document
from .services import AccessView

# "Updated within" choices offered in the interface (days).
RECENCY_CHOICES = (7, 30, 90, 365)


@dataclass(frozen=True)
class DocFilter:
    team_id: int | None = None
    classification: str | None = None
    source: str | None = None  # "uploaded", or a connected source's id
    owner_id: int | None = None  # the person who uploaded it
    updated_within_days: int | None = None

    @property
    def active(self) -> bool:
        return any(
            v is not None for v in (self.team_id, self.classification, self.source, self.owner_id, self.updated_within_days)
        )

    def matches(self, doc: Document, now: datetime | None = None) -> bool:
        if self.team_id is not None and doc.owner_team_id != self.team_id:
            return False
        if self.classification is not None and doc.classification != self.classification:
            return False
        if self.source == "uploaded" and doc.source_id is not None:
            return False
        if self.source is not None and self.source.isdigit() and doc.source_id != int(self.source):
            return False
        if self.owner_id is not None and doc.uploaded_by != self.owner_id:
            return False
        if self.updated_within_days is not None:
            when = doc.updated_at or doc.created_at
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
            if when < (now or datetime.now(UTC)) - timedelta(days=self.updated_within_days):
                return False
        return True

    def narrow(self, view: AccessView, ids: set[int] | None = None) -> set[int]:
        """The documents among `ids` (default: those the person may open) that pass the filters."""
        pool = view.allowed if ids is None else ids
        if not self.active:
            return set(pool)
        now = datetime.now(UTC)
        return {i for i in pool if self.matches(view.docs[i], now)}

    def describe(self, view: AccessView) -> str:
        """In words, for the assistant's reply: "Engineering documents updated in the last 30 days"."""
        parts = []
        if self.team_id is not None:
            team = next((d.owner_team.name for d in view.docs.values() if d.owner_team_id == self.team_id), "that team's")
            parts.append(team)
        if self.classification is not None:
            parts.append(self.classification.title())
        what = " ".join(parts + ["documents"])
        if self.source == "uploaded":
            what += " uploaded to VaultMind"
        elif self.source is not None:
            name = next((d.source.name for d in view.docs.values() if d.source and str(d.source_id) == self.source), None)
            what += f" from {name}" if name else " from that source"
        if self.owner_id is not None:
            who = next((d.uploader.full_name for d in view.docs.values() if d.uploaded_by == self.owner_id and d.uploader), None)
            what += f" added by {who}" if who else " added by that person"
        if self.updated_within_days is not None:
            what += f" updated in the last {self.updated_within_days} days"
        return what
