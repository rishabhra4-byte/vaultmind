from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from .models import Classification, Position

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
    summary: str
    tags: list[str]
    sensitivity_flags: list[str]
    uploaded_by: str | None
    created_at: UTCDatetime
    updated_at: UTCDatetime
    access: AccessInfo


class DocumentDetail(DocumentOut):
    preview: str | None = None


class DocumentUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=255)
    classification: Classification | None = None
    min_position: int | None = Field(None, ge=Position.INTERN, le=Position.EXECUTIVE)
    discoverable: bool | None = None
    share_team_ids: list[int] | None = None
    tags: list[str] | None = None
    summary: str | None = None


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


class WhoCanAccess(BaseModel):
    user: UserOut
    via: str


class RecommendationOut(BaseModel):
    document: DocumentOut
    score: float
    reasons: list[str]


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: int | None = None


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


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]


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


class UserUpdate(BaseModel):
    team_id: int | None = None
    position: int | None = Field(None, ge=Position.INTERN, le=Position.EXECUTIVE)
    title: str | None = None
    is_active: bool | None = None
    is_admin: bool | None = None


class MetaOut(BaseModel):
    company: str
    ai_mode: str
    ai_model: str | None
    positions: list[dict]
    classifications: list[dict]
    teams: list[TeamOut]
    demo_users: list[dict]
