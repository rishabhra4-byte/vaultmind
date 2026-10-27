"""Relational schema.

Team ──< User                       every user belongs to one team and holds a position
Team ──< Document >──< DocumentShare >── Team
Document ──< DocumentChunk          text split into passages for retrieval
Document ──< AccessGrant >── User   time-boxed exceptions, created by approved requests
User ──< AccessRequest
User ──< Conversation ──< Message   assistant chat history
AuditEvent                          append-only activity log
"""

from datetime import UTC, datetime
from enum import IntEnum, StrEnum

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class Position(IntEnum):
    """Seniority ladder. Higher values carry more authority."""

    INTERN = 1
    ASSOCIATE = 2
    SENIOR = 3
    MANAGER = 4
    DIRECTOR = 5
    EXECUTIVE = 6

    @property
    def label(self) -> str:
        return self.name.title()


class Classification(StrEnum):
    PUBLIC = "public"  # anyone in the company
    INTERNAL = "internal"  # owning team + teams it is shared with
    CONFIDENTIAL = "confidential"  # as internal, plus a minimum position (Manager by default)
    RESTRICTED = "restricted"  # named people only: uploader or explicit grant


DEFAULT_MIN_POSITION: dict[Classification, Position] = {
    Classification.PUBLIC: Position.INTERN,
    Classification.INTERNAL: Position.INTERN,
    Classification.CONFIDENTIAL: Position.MANAGER,
    Classification.RESTRICTED: Position.EXECUTIVE,
}


class RequestStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    CANCELLED = "cancelled"


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    description: Mapped[str] = mapped_column(String(255), default="")

    members: Mapped[list["User"]] = relationship(back_populates="team")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(120), default="")  # e.g. "Backend Engineer"
    password_hash: Mapped[str] = mapped_column(String(255))
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=Position.ASSOCIATE)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    team: Mapped[Team] = relationship(back_populates="members")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(120), default="text/plain")
    size: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    storage_key: Mapped[str] = mapped_column(String(255))

    owner_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    classification: Mapped[str] = mapped_column(String(20), default=Classification.INTERNAL)
    min_position: Mapped[int] = mapped_column(Integer, default=Position.INTERN)
    # Discoverable documents may be *mentioned* (title only) to people who can't open them,
    # so they know to request access. Restricted documents are never discoverable.
    discoverable: Mapped[bool] = mapped_column(Boolean, default=True)

    summary: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    sensitivity_flags: Mapped[list[str]] = mapped_column(JSON, default=list)

    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    owner_team: Mapped[Team] = relationship()
    uploader: Mapped[User | None] = relationship()
    shares: Mapped[list["DocumentShare"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="DocumentChunk.ordinal"
    )

    @property
    def shared_team_ids(self) -> set[int]:
        return {s.team_id for s in self.shares}


class DocumentShare(Base):
    __tablename__ = "document_shares"
    __table_args__ = (UniqueConstraint("document_id", "team_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))

    document: Mapped[Document] = relationship(back_populates="shares")
    team: Mapped[Team] = relationship()


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)

    document: Mapped[Document] = relationship(back_populates="chunks")


class AccessGrant(Base):
    __tablename__ = "access_grants"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    granted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AccessRequest(Base):
    __tablename__ = "access_requests"
    __table_args__ = (
        # At most one pending request per person per document, enforced by the database so
        # two quick clicks (or two tabs) can't create duplicates.
        Index(
            "uq_one_pending_request",
            "document_id",
            "requester_id",
            unique=True,
            sqlite_where=text("status = 'pending'"),
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    reason: Mapped[str] = mapped_column(Text)
    duration_days: Mapped[int] = mapped_column(Integer, default=7)
    status: Mapped[str] = mapped_column(String(20), default=RequestStatus.PENDING, index=True)
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decision_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    document: Mapped[Document] = relationship()
    requester: Mapped[User] = relationship(foreign_keys=[requester_id])
    decider: Mapped[User | None] = relationship(foreign_keys=[decided_by])


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="New conversation")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Message.id"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text)
    # For assistant turns: cited docs, locked hints and mode, so the UI can re-render history.
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    action: Mapped[str] = mapped_column(String(60), index=True)
    target_type: Mapped[str] = mapped_column(String(30), default="")
    target_id: Mapped[int | None] = mapped_column(Integer)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    actor: Mapped[User | None] = relationship()
