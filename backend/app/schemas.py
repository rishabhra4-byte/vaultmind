from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from .models import Classification, Position, SourceKind

# SQLite hands back naive datetimes; everything is stored in UTC, so say so on the way out.
UTCDatetime = Annotated[datetime, AfterValidator(lambda d: d if d.tzinfo else d.replace(tzinfo=UTC))]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class TeamOut(ORM):
    id: int
    name: str
    description: str


class UserOut(ORM):
    id: int
    email: str
    full_name: str
    title: str
    team_id: int
    team_name: str
    position: int
    position_label: str
    is_admin: bool
    is_active: bool

    @classmethod
    def of(cls, u) -> "UserOut":
        return cls(
            id=u.id,
            email=u.email,
            full_name=u.full_name,
            title=u.title,
            team_id=u.team_id,
            team_name=u.team.name,
            position=u.position,
            position_label=Position(u.position).label,
            is_admin=u.is_admin,
            is_active=u.is_active,
        )


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=256)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class AccessInfo(BaseModel):
    allowed: bool
    code: str
    reason: str
    can_manage: bool = False
    request_pending: bool = False


class SourceRef(BaseModel):
    id: int
    kind: str
    label: str  # "Google Drive", "Microsoft 365", "Shared folder"
    name: str


class DocumentOut(BaseModel):
    id: int
    title: str
    filename: str
    mime_type: str
    size: int
    owner_team_id: int
    owner_team: str
    classification: Classification
    min_position: int
    min_position_label: str
    discoverable: bool
    shared_with: list[str]
    shared_with_groups: list[str] = []
    summary: str
    tags: list[str]
    sensitivity_flags: list[str]
    uploaded_by: str | None
    created_at: UTCDatetime
    updated_at: UTCDatetime
    version: int = 1
    ai_enabled: bool = True
    acl_mode: str = "native"
    source: SourceRef | None = None
    source_path: str = ""
    external_url: str | None = None
    access: AccessInfo


class Passage(BaseModel):
    ordinal: int
    text: str


class DocumentDetail(DocumentOut):
    preview: str | None = None
    passages: list[Passage] = []


class DocumentUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=255)
    classification: Classification | None = None
    min_position: int | None = Field(None, ge=Position.INTERN, le=Position.EXECUTIVE)
    discoverable: bool | None = None
    share_team_ids: list[int] | None = None
    share_group_ids: list[int] | None = None
    tags: list[str] | None = None
    summary: str | None = None
    ai_enabled: bool | None = None


class VersionOut(BaseModel):
    version: int
    created_at: UTCDatetime
    created_by: str | None
    change_summary: str
    size: int


class ChangesOut(BaseModel):
    from_version: int | None
    to_version: int
    summary: str
    diff: dict | None  # {"added": [...], "removed": [...], "changed": [{"before", "after"}], "counts": {...}}


class ScanFinding(BaseModel):
    flag: str
    label: str
    count: int
    level: str


class ScanOut(BaseModel):
    suggested_classification: Classification
    findings: list[ScanFinding]
    summary: str
    tags: list[str]


class UploadOut(BaseModel):
    document: DocumentOut
    scan: ScanOut
    warning: str | None = None


class NewVersionOut(BaseModel):
    document: DocumentOut
    version: int
    change_summary: str


class WhoCanAccess(BaseModel):
    user: UserOut
    via: str


class RecommendationOut(BaseModel):
    document: DocumentOut
    score: float
    reasons: list[str]


class ChatIn(BaseModel):
    message: str = Field("", max_length=2000)  # may be empty for a quick action (`intent`)
    conversation_id: int | None = None
    document_id: int | None = None  # ask about this one document
    intent: Literal["summary", "key_points", "for_me", "changes", "compare"] | None = None
    compare_with: int | None = None  # the second document, for "compare"


class MessageOut(ORM):
    id: int
    role: str
    content: str
    meta: dict
    created_at: UTCDatetime


class ConversationOut(ORM):
    id: int
    title: str
    created_at: UTCDatetime
    updated_at: UTCDatetime
    document_id: int | None = None  # set for "ask about this document" conversations


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]


class ActionProposeIn(BaseModel):
    kind: Literal["save_summary"]
    message_id: int


class ActionConfirmIn(BaseModel):
    """Optional edits made on the card before confirming."""

    reason: str | None = Field(None, max_length=1000)
    duration_days: int | None = Field(None, ge=1, le=90)
    title: str | None = Field(None, min_length=1, max_length=200)
    body: str | None = Field(None, max_length=8000)


class ActionOut(BaseModel):
    id: int
    kind: str
    label: str
    payload: dict
    status: str
    result: dict


class GlossaryIn(BaseModel):
    term: str = Field(min_length=1, max_length=80)
    synonyms: list[str] = Field(default_factory=list, max_length=20)
    note: str = Field("", max_length=255)


class GlossaryUpdate(BaseModel):
    term: str | None = Field(None, min_length=1, max_length=80)
    synonyms: list[str] | None = Field(None, max_length=20)
    note: str | None = Field(None, max_length=255)


class GlossaryOut(BaseModel):
    id: int | None  # None for built-in terms
    term: str
    synonyms: list[str]
    note: str
    builtin: bool


class ChatOut(BaseModel):
    conversation: ConversationOut
    user_message: MessageOut
    reply: MessageOut


class AccessRequestIn(BaseModel):
    document_id: int
    reason: str = Field(min_length=10, max_length=1000)
    duration_days: int = Field(7, ge=1, le=90)


class DecisionIn(BaseModel):
    note: str = Field("", max_length=1000)
    duration_days: int | None = Field(None, ge=1, le=90)


class AccessRequestOut(BaseModel):
    id: int
    document_id: int
    document_title: str
    document_team: str
    classification: str
    requester: UserOut
    reason: str
    duration_days: int
    status: str
    decided_by: str | None
    decision_note: str
    created_at: UTCDatetime
    decided_at: UTCDatetime | None


class GrantOut(BaseModel):
    id: int
    document_id: int
    document_title: str
    user_id: int
    user_name: str
    granted_by: str | None
    expires_at: UTCDatetime | None
    created_at: UTCDatetime


class AuditOut(BaseModel):
    id: int
    actor: str | None
    actor_id: int | None
    action: str
    target_type: str
    target_id: int | None
    detail: dict
    created_at: UTCDatetime


class MetaOut(BaseModel):
    company: str
    ai_mode: str
    ai_model: str | None
    positions: list[dict]
    classifications: list[dict]
    teams: list[TeamOut]
    demo_users: list[dict]
    sso_providers: list[dict] = []  # [{"id": "google", "label": "Google"}]
    password_login: bool = True


class NotificationOut(ORM):
    id: int
    kind: str
    title: str
    body: str
    link: str
    created_at: UTCDatetime
    read_at: UTCDatetime | None


class NotificationList(BaseModel):
    unread: int
    items: list[NotificationOut]


class SourceIn(BaseModel):
    kind: SourceKind
    name: str = Field(min_length=1, max_length=120)
    config: dict = {}
    owner_team_id: int
    classification: Classification = Classification.INTERNAL
    permission_mode: Literal["mirror", "rules"] = "rules"
    ai_enabled: bool = True
    sync_directory: bool = False
    enabled: bool = True
    sync_interval_minutes: int = Field(60, ge=5, le=10080)


class SourceUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    config: dict | None = None
    owner_team_id: int | None = None
    classification: Classification | None = None
    permission_mode: Literal["mirror", "rules"] | None = None
    ai_enabled: bool | None = None
    sync_directory: bool | None = None
    enabled: bool | None = None
    sync_interval_minutes: int | None = Field(None, ge=5, le=10080)


class SourceOut(BaseModel):
    id: int
    kind: str
    label: str
    name: str
    config: dict
    owner_team_id: int
    owner_team: str
    classification: str
    permission_mode: str
    ai_enabled: bool
    sync_directory: bool
    enabled: bool
    sync_interval_minutes: int
    status: str
    last_synced_at: UTCDatetime | None
    last_error: str
    stats: dict
    document_count: int
    created_at: UTCDatetime


class SourcesOut(BaseModel):
    sources: list[SourceOut]
    available: dict[str, bool]  # which kinds are set up on the server
    folder_roots: list[str]
