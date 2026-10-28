"""Things the assistant can do for someone, but only after they confirm.

The assistant *proposes* an action, which is stored with status "proposed" and shown as a card
with a button. Nothing happens until the person presses it. Confirming re-checks permissions at
that moment, with the same rules as the regular pages, so an action can never do more than the
person could do by hand, even if the proposal is old or their access changed since.

  request_access  ask a document's owners for access (same as the Request access button)
  share           share a document with a team or group, or give one person time-limited access
  save_summary    save one of the assistant's answers as a private document
  draft_message   a message draft to copy or open in an email app; VaultMind never sends it
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import audit
from .access_flow import create_access_request, grant_access
from .models import (
    AclMode,
    AssistantAction,
    Classification,
    Conversation,
    Document,
    DocumentGroupShare,
    DocumentShare,
    Group,
    Message,
    Position,
    Team,
    User,
    utcnow,
)
from .policy import can_manage
from .search import title_index
from .services import AccessView, ingest_document, viewer

KINDS = ("request_access", "share", "save_summary", "draft_message")
SHARE_DAYS = 30  # default length of access given to one person

# "share the hiring plan with Sales", "give Priya access to the runbook"
SHARE_WITH = re.compile(r"(?i)^\s*(?:please\s+|can you\s+|could you\s+)?share\s+(?P<what>.+?)\s+with\s+(?P<who>.+?)[\s.?!]*$")
GIVE_ACCESS = re.compile(
    r"(?i)^\s*(?:please\s+|can you\s+|could you\s+)?(?:give|grant)\s+(?P<who>.+?)\s+access\s+to\s+(?P<what>.+?)[\s.?!]*$"
)
# "draft an email to Priya about the release", "write a Slack message to my manager asking for ..."
DRAFT = re.compile(
    r"(?i)\b(?:draft|write|compose|prepare)\b(?:\s+(?:me|a|an|the|quick|short|polite))*\s+"
    r"(?:slack\s+|e-?mail\s+)?(?:message|email|e-mail|mail|note|dm|reply)\b"
)
DRAFT_TO = re.compile(r"(?i)\bto\s+(?P<to>(?:my\s+)?[\w.@'-]+(?:\s+(?!about\b|asking\b|regarding\b|saying\b)[A-Z][\w'-]+)?)")
DRAFT_ABOUT = re.compile(r"(?i)\b(?:about|regarding|asking(?:\s+(?:for|about|if|whether))?|saying)\s+(?P<about>.+?)[\s.?!]*$")
# "save this", "save that answer", "keep this as a note"
SAVE = re.compile(r"(?i)^\s*(?:please\s+)?(?:save|keep|store)\s+(?:this|that|it|the\s+(?:answer|summary|above))\b")
THIS_DOC = re.compile(r"(?i)^(?:this|it|this\s+(?:document|doc|file|page)|the\s+(?:document|doc|file))$")


@dataclass
class Target:
    kind: str  # team | group | user
    id: int
    name: str
    email: str = ""


@dataclass
class Proposal:
    """What the assistant says, plus the action card(s) to show."""

    content: str
    actions: list[dict] = field(default_factory=list)
    documents: list[Document] = field(default_factory=list)


def proposal(kind: str, label: str, payload: dict) -> dict:
    return {"kind": kind, "label": label, "payload": payload}


# ---------------------------------------------------------------- understanding requests


def _clean(text: str) -> str:
    return re.sub(r"(?i)^(?:the|my|our)\s+", "", text.strip().strip("“”\"'")).strip(" .?!")


def resolve_target(db: Session, who: str, me: User) -> Target | None:
    """A team, a group or a person, by name ("Sales", "the payments squad", "Priya", "priya@…")."""
    name = _clean(who)
    if not name:
        return None
    bare = re.sub(r"(?i)\s+(?:team|department|dept|group|squad)$", "", name).strip()
    for candidate in dict.fromkeys([name, bare]):
        team = db.scalar(select(Team).where(func.lower(Team.name) == candidate.lower()))
        if team:
            return Target("team", team.id, team.name)
        group = db.scalar(select(Group).where(func.lower(Group.name) == candidate.lower()))
        if group:
            return Target("group", group.id, group.name)
    people = [u for u in db.scalars(select(User).where(User.is_active.is_(True))) if u.id != me.id]
    lowered = name.lower()
    exact = [u for u in people if u.email.lower() == lowered or u.full_name.lower() == lowered]
    if not exact:
        exact = [u for u in people if u.full_name.lower().split()[0] == lowered]
    if len(exact) == 1:
        u = exact[0]
        return Target("user", u.id, u.full_name, u.email)
    return None


def resolve_document(view: AccessView, what: str, scope: Document | None, managed_only: bool) -> Document | None:
    what = _clean(what)
    if scope is not None and (not what or THIS_DOC.match(what)):
        return scope
    pool = {i for i in view.allowed if not managed_only or can_manage(view.user, view.docs[i], view.grants, view.groups)}
    if not pool or not what:
        return None
    hits = title_index.search(what, allowed_doc_ids=pool, top_k=1)
    return view.docs[hits[0].doc_id] if hits and hits[0].score >= 1.0 else None


def share_request(db: Session, view: AccessView, question: str, scope: Document | None) -> Proposal | None:
    m = SHARE_WITH.match(question) or GIVE_ACCESS.match(question)
    if not m:
        return None
    user = view.user
    doc = resolve_document(view, m.group("what"), scope, managed_only=False)
    if doc is None:
        return Proposal(
            "I couldn't tell which document you mean. Use its title, for example “Share the Q4 Territory Plan with Marketing”."
        )
    target = resolve_target(db, m.group("who"), user)
    if target is None:
        return Proposal(
            f"I couldn't tell who “{_clean(m.group('who'))}” is. Use a team or group name, or a person's full name or email.",
            documents=[doc],
        )
    cite = f"**{doc.title}** [D{doc.id}]"
    if doc.source_id is not None and not user.is_admin:
        where = doc.source.label if doc.source else "its source"
        return Proposal(f"{cite} is synced from {where}, so it's shared there, not in VaultMind.", documents=[doc])
    if target.kind == "user":
        payload = {"document_id": doc.id, "target": target.__dict__, "duration_days": SHARE_DAYS}
        label = f"Give {target.name} access to “{doc.title}” for {SHARE_DAYS} days"
        return Proposal(
            f"I can give {target.name} access to {cite} for {SHARE_DAYS} days. They'll get a notification. Confirm below.",
            [proposal("share", label, payload)],
            [doc],
        )
    if not can_manage(user, doc, view.grants, view.groups):
        return Proposal(
            f"You can't change who {cite} is shared with. Its owners in {doc.owner_team.name} (Manager level and above) can.",
            documents=[doc],
        )
    if doc.classification == Classification.RESTRICTED:
        return Proposal(
            f"{cite} is restricted, so it can only be opened by named people. Sharing it with a {target.kind} wouldn't let "
            "anyone open it. Give a named person access instead.",
            documents=[doc],
        )
    level = doc.min_position
    who = f"people in {target.name}" if target.kind == "team" else f"members of {target.name}"
    note = f" at {Position(level).label} level or above" if level > Position.INTERN else ""
    payload = {"document_id": doc.id, "target": target.__dict__}
    return Proposal(
        f"I can share {cite} with {target.name}. Afterwards {who}{note} can open it. Confirm below.",
        [proposal("share", f"Share “{doc.title}” with {target.name}", payload)],
        [doc],
    )


def save_request(question: str, history: list[Message]) -> Proposal | None:
    if not SAVE.match(question):
        return None
    last = next((m for m in reversed(history) if m.role == "assistant" and m.id), None)
    if last is None:
        return Proposal("There's no answer to save yet. Ask me something first.")
    asked = next((m.content for m in reversed(history) if m.role == "user" and m.id < last.id), "")
    title = default_title(asked)
    return Proposal(
        "I can save my previous answer as a document only you can open. You can share it from its page afterwards. "
        "Confirm below.",
        [proposal("save_summary", f"Save “{title}”", {"message_id": last.id, "title": title})],
    )


def default_title(question: str) -> str:
    q = re.sub(r"\s+", " ", question).strip().rstrip("?.!")
    q = q if len(q) <= 70 else q[:67].rsplit(" ", 1)[0] + "…"
    return f"Notes: {q}" if q else "Saved answer"


def parse_draft(question: str) -> tuple[str, str] | None:
    """(to, about) for "draft a message to Priya about the release", or None if it isn't a draft request."""
    if not DRAFT.search(question):
        return None
    to = DRAFT_TO.search(question)
    about = DRAFT_ABOUT.search(question)
    return (to.group("to").strip() if to else "", about.group("about").strip() if about else "")


def template_draft(user: User, to: str, about: str, docs: list[Document]) -> tuple[str, str]:
    """A plain draft without AI: (subject, body)."""
    greeting = f"Hi {to.split()[0] if to and not to.lower().startswith('my ') else 'there'},"
    topic = about or "this"
    lines = [greeting, "", f"I wanted to check with you about {topic}."]
    if docs:
        refs = ", ".join(f"“{d.title}”" for d in docs[:2])
        lines += ["", f"For reference, I've been reading {refs} in VaultMind."]
    lines += ["", "Thanks,", user.full_name.split()[0]]
    subject = topic[:1].upper() + topic[1:] if about else "Quick question"
    return subject[:120], "\n".join(lines)


def draft_proposal(db: Session, user: User, to: str, subject: str, body: str) -> dict:
    email = ""
    target = resolve_target(db, to, user) if to else None
    if target and target.kind == "user":
        email = target.email
    label = f"Message to {target.name if target else (to or 'someone')}"
    return proposal(
        "draft_message", label, {"to": target.name if target else to, "email": email, "subject": subject, "body": body}
    )


# ---------------------------------------------------------------- doing them


def persist(db: Session, user: User, proposals: list[dict]) -> list[dict]:
    """Store proposed actions; returns them as they go in a message (with ids and status)."""
    out = []
    for p in proposals:
        payload = {**p["payload"], "label": p["label"]}
        row = AssistantAction(user_id=user.id, kind=p["kind"], payload=payload, status="proposed", result={})
        db.add(row)
        db.flush()
        out.append(as_dict(row))
    return out


def as_dict(row: AssistantAction) -> dict:
    payload = dict(row.payload or {})
    label = payload.pop("label", row.kind)
    return {"id": row.id, "kind": row.kind, "label": label, "payload": payload, "status": row.status, "result": row.result or {}}


def load_own(db: Session, user: User, action_id: int) -> AssistantAction:
    row = db.get(AssistantAction, action_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Action not found")
    return row


def confirm(db: Session, user: User, row: AssistantAction, edits: dict) -> dict:
    """Run a proposed action after the person confirmed it. Raises HTTPException if it can't run."""
    if row.status != "proposed":
        raise HTTPException(status.HTTP_409_CONFLICT, f"This was already {row.status}")
    runner = {
        "request_access": _request_access,
        "share": _share,
        "save_summary": _save_summary,
        "draft_message": _draft_message,
    }[row.kind]
    result = runner(db, user, row.payload, edits)
    row.status, row.result, row.decided_at = "done", result, datetime.now(UTC)
    audit.record(db, user, "assistant.action", "assistant_action", row.id, kind=row.kind, result=result.get("message", ""))
    return result


def dismiss(db: Session, row: AssistantAction) -> None:
    if row.status == "proposed":
        row.status, row.decided_at = "dismissed", datetime.now(UTC)


def _document(db: Session, payload: dict) -> Document:
    doc = db.get(Document, int(payload.get("document_id", 0)))
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That document no longer exists")
    return doc


def _request_access(db: Session, user: User, payload: dict, edits: dict) -> dict:
    doc = _document(db, payload)
    reason = str(edits.get("reason") or payload.get("reason") or "")
    days = int(edits.get("duration_days") or payload.get("duration_days") or 7)
    req = create_access_request(db, user, doc, reason, days)
    return {"message": f"Asked for access to “{doc.title}”. You'll get a notification when it's decided.", "request_id": req.id}


def _share(db: Session, user: User, payload: dict, edits: dict) -> dict:
    doc = _document(db, payload)
    target = payload.get("target") or {}
    kind, target_id = target.get("kind"), int(target.get("id", 0))
    if kind == "user":
        person = db.get(User, target_id)
        if person is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "That person no longer exists")
        days = int(edits.get("duration_days") or payload.get("duration_days") or SHARE_DAYS)
        grant_access(db, user, doc, person, days)
        return {"message": f"{person.full_name} can open “{doc.title}” for {days} days.", "link": f"/library/{doc.id}"}

    v = viewer(db, user)
    if not can_manage(user, doc, v.grants, v.groups):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can't change who this document is shared with")
    if doc.source_id is not None or doc.acl_mode == AclMode.MIRROR:
        raise HTTPException(status.HTTP_409_CONFLICT, "Sharing for synced files is managed in their source")
    if doc.classification == Classification.RESTRICTED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Restricted documents can only be shared with named people")
    if kind == "team":
        team = db.get(Team, target_id)
        if team is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "That team no longer exists")
        if team.id != doc.owner_team_id and team.id not in doc.shared_team_ids:
            doc.shares.append(DocumentShare(team_id=team.id))
        name = team.name
    elif kind == "group":
        group = db.get(Group, target_id)
        if group is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "That group no longer exists")
        if group.id not in doc.shared_group_ids:
            doc.group_shares.append(DocumentGroupShare(group_id=group.id))
        name = group.name
    else:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown share target")
    doc.updated_at = utcnow()
    audit.record(db, user, "document.update", "document", doc.id, title=doc.title, changes={f"share_{kind}": name})
    return {"message": f"Shared “{doc.title}” with {name}.", "link": f"/library/{doc.id}"}


def _save_summary(db: Session, user: User, payload: dict, edits: dict) -> dict:
    msg = db.get(Message, int(payload.get("message_id", 0)))
    convo = db.get(Conversation, msg.conversation_id) if msg else None
    if msg is None or convo is None or convo.user_id != user.id or msg.role != "assistant":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That answer no longer exists")
    title = re.sub(r"\s+", " ", str(edits.get("title") or payload.get("title") or "Saved answer")).strip()[:200]
    cited = [c for c in (msg.meta or {}).get("citations", []) if isinstance(c, dict) and "id" in c]
    sources = [d for d in (db.get(Document, int(c["id"])) for c in cited) if d is not None]
    v = viewer(db, user)
    sources = [d for d in sources if v.can_view(d)]  # only list sources the person can still open
    numbers = {d.id: i + 1 for i, d in enumerate(sources)}

    def cite(m: re.Match) -> str:
        n = numbers.get(int(m.group(1)))
        return f"[{n}]" if n else ""

    body = re.sub(r"\[D(\d+)(?:\.\d+)?\]", cite, msg.content)
    text = f"# {title}\n\n{body}\n"
    if sources:
        text += "\n## Sources\n\n" + "\n".join(f"{numbers[d.id]}. {d.title} (VaultMind document {d.id})" for d in sources) + "\n"
    safe = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-")[:60] or "saved-answer"
    ingest = ingest_document(
        db,
        uploader=user,
        filename=f"{safe}.md",
        data=text.encode(),
        mime_type="text/markdown",
        title=title,
        owner_team_id=user.team_id,
        classification=Classification.RESTRICTED,  # private until its owner shares it
        discoverable=False,
        tags=["saved answer"],
        summary=f"Saved from a VaultMind answer by {user.full_name}.",
        text=text,
        # If any source is kept away from the AI, so is a note made from it.
        ai_enabled=all(d.ai_enabled for d in sources),
    )
    doc = ingest.document
    audit.record(db, user, "document.upload", "document", doc.id, title=doc.title, classification=doc.classification, saved=True)
    return {
        "message": f"Saved as “{doc.title}”. Only you (and admins) can open it until you share it.",
        "link": f"/library/{doc.id}",
    }


def _draft_message(db: Session, user: User, payload: dict, edits: dict) -> dict:
    body = str(edits.get("body") or payload.get("body") or "")
    return {"message": "Draft ready. VaultMind doesn't send messages; copy it into your email or chat app.", "body": body}
