"""Relational schema.

Team ──< User                       every user belongs to one team and holds a position
User >──< Group                     project groups, created by hand or synced from a directory
Team ──< Document >──< DocumentShare >── Team
Document >──< DocumentGroupShare >── Group
Document ──< DocumentChunk          text split into passages for retrieval
Document ──< DocumentVersion        every version of the text, for "what changed"
Document ──< DocumentPrincipal      sharing copied from the source system (mirrored files)
Source ──< Document                 a connected file store (shared folder, Google Drive, Microsoft 365)
Document ──< AccessGrant >── User   time-boxed exceptions, created by approved requests
User ──< AccessRequest
User ──< Conversation ──< Message   assistant chat history
User ──< AssistantAction            things the assistant offered to do, done only once confirmed
User ──< Notification               in-app notices, also sent by email or Slack when set up
User ──< UserIdentity               the person's id at Google, Microsoft or Slack
User ──< UserSession                signed-in browsers, which can be ended
GlossaryTerm                        company vocabulary used to widen searches
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


class AclMode(StrEnum):
    NATIVE = "native"  # VaultMind's rules: team, position, classification (uploads, folder sources)
    MIRROR = "mirror"  # the source system's own sharing, copied at every sync (Drive, Microsoft 365)


class SourceKind(StrEnum):
    FOLDER = "folder"
    GOOGLE = "google_drive"
    MICROSOFT = "microsoft_365"

    @property
    def label(self) -> str:
        return {"folder": "Shared folder", "google_drive": "Google Drive", "microsoft_365": "Microsoft 365"}[self.value]


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
    # "password", or "google" / "microsoft" for people who sign in with their company account
    auth_provider: Mapped[str] = mapped_column(String(20), default="password", server_default="password")
    # Set for new joiners: until this date the assistant shows a first-week checklist.
    onboarding_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notify_email: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    # Kept up to date by a directory sync (Google Workspace / Microsoft 365) rather than by hand.
    directory_managed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))

    team: Mapped[Team] = relationship(back_populates="members")
    memberships: Mapped[list["GroupMember"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class UserSession(Base):
    """One signed-in browser. The token carries a random session id; only its SHA-256 is stored
    here, so a copy of the database can't be used to sign in as anyone."""

    __tablename__ = "user_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256 of the token's session id
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    method: Mapped[str] = mapped_column(String(20), default="password")  # password | google | microsoft
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    # signed_out | password_changed | deactivated | role_changed | ended_by_admin | ended_by_user | idle
    end_reason: Mapped[str] = mapped_column(String(40), default="")


class UserIdentity(Base):
    """The person's id at an outside service, e.g. a Microsoft object id or a Slack user id."""

    __tablename__ = "user_identities"
    __table_args__ = (UniqueConstraint("provider", "subject"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(20))  # google | microsoft | slack
    subject: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Group(Base):
    """A set of people across teams (a project, a committee), made by hand or synced from a directory."""

    __tablename__ = "groups"
    __table_args__ = (UniqueConstraint("provider", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(255), default="")
    provider: Mapped[str] = mapped_column(String(20), default="manual")  # manual | google | microsoft
    external_id: Mapped[str | None] = mapped_column(String(255))  # group email or directory object id
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    members: Mapped[list["GroupMember"]] = relationship(back_populates="group", cascade="all, delete-orphan")


class GroupMember(Base):
    __tablename__ = "group_members"
    __table_args__ = (UniqueConstraint("group_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    group: Mapped[Group] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="memberships")


class Source(Base):
    """A connected file store whose files are synced into VaultMind."""

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(30))  # SourceKind
    name: Mapped[str] = mapped_column(String(120))
    # Kind-specific settings: folder path and rules, Drive folder ids, a Microsoft drive id...
    # Credentials never live here; they come from the server's environment.
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    owner_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    classification: Mapped[str] = mapped_column(String(20), default=Classification.INTERNAL)
    # "mirror": keep each file's sharing from the source; "rules": team + classification rules
    permission_mode: Mapped[str] = mapped_column(String(20), default="rules")
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sync_directory: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sync_interval_minutes: Mapped[int] = mapped_column(Integer, default=60)
    status: Mapped[str] = mapped_column(String(20), default="idle")  # idle | syncing | ok | error
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str] = mapped_column(Text, default="")
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    owner_team: Mapped[Team] = relationship()

    @property
    def label(self) -> str:
        return SourceKind(self.kind).label


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("source_id", "external_id", name="uq_documents_source_external"),)

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

    # Synced files: where they came from, and how to open them there.
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id", ondelete="SET NULL"), index=True)
    external_id: Mapped[str | None] = mapped_column(String(512))
    external_url: Mapped[str | None] = mapped_column(String(1000))
    source_path: Mapped[str] = mapped_column(String(1000), default="", server_default="")
    # The source's change marker (checksum, eTag, modified time): unchanged means no re-download.
    source_version: Mapped[str | None] = mapped_column(String(255))
    acl_mode: Mapped[str] = mapped_column(String(20), default=AclMode.NATIVE, server_default="native")
    # Whether the AI model may read this document's text (search and opening still work).
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    owner_team: Mapped[Team] = relationship()
    uploader: Mapped[User | None] = relationship()
    source: Mapped[Source | None] = relationship()
    shares: Mapped[list["DocumentShare"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    group_shares: Mapped[list["DocumentGroupShare"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    principals: Mapped[list["DocumentPrincipal"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="DocumentChunk.ordinal"
    )

    @property
    def shared_team_ids(self) -> set[int]:
        return {s.team_id for s in self.shares}

    @property
    def shared_group_ids(self) -> set[int]:
        return {s.group_id for s in self.group_shares}


class DocumentShare(Base):
    __tablename__ = "document_shares"
    __table_args__ = (UniqueConstraint("document_id", "team_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))

    document: Mapped[Document] = relationship(back_populates="shares")
    team: Mapped[Team] = relationship()


class DocumentGroupShare(Base):
    __tablename__ = "document_group_shares"
    __table_args__ = (UniqueConstraint("document_id", "group_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), index=True)

    document: Mapped[Document] = relationship(back_populates="group_shares")
    group: Mapped[Group] = relationship()


class DocumentPrincipal(Base):
    """Who a mirrored file is shared with in its source system.

    kind "user": value is an email address; "group": a VaultMind group id; "domain": an email
    domain (everyone @domain); "company": everyone who can sign in to VaultMind.
    """

    __tablename__ = "document_principals"
    __table_args__ = (UniqueConstraint("document_id", "kind", "value"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    value: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="reader")

    document: Mapped[Document] = relationship(back_populates="principals")


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("document_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer, default=0)
    change_summary: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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
    # Set for conversations held beside one document ("ask about this document").
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"), index=True)
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


class AssistantAction(Base):
    """Something the assistant offered to do (request access, share, save a summary).

    Nothing happens until the person confirms; permissions are checked again at that moment.
    """

    __tablename__ = "assistant_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(30))  # request_access | share | save_summary
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="proposed")  # proposed | done | dismissed | failed
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("user_id", "dedupe_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str] = mapped_column(String(500), default="")
    dedupe_key: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Email / Slack delivery: null until attempted; channels used and any error go in `delivery`.
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery: Mapped[dict] = mapped_column(JSON, default=dict)


class GlossaryTerm(Base):
    """Company vocabulary: a search for the term also finds documents using its synonyms."""

    __tablename__ = "glossary_terms"

    id: Mapped[int] = mapped_column(primary_key=True)
    term: Mapped[str] = mapped_column(String(80), unique=True)
    synonyms: Mapped[list[str]] = mapped_column(JSON, default=list)
    note: Mapped[str] = mapped_column(String(255), default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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
