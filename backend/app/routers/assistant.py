from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import actions
from ..assistant import QUICK, respond
from ..chat_log import record_exchange, short_title
from ..database import get_db
from ..deps import chat_limiter, current_user
from ..models import AssistantAction, Conversation, Document, Message, User
from ..policy import is_discoverable_to
from ..schemas import (
    ActionConfirmIn,
    ActionOut,
    ActionProposeIn,
    ChatIn,
    ChatOut,
    ConversationDetail,
    ConversationOut,
    MessageOut,
)
from ..services import viewer

router = APIRouter(prefix="/api/assistant", tags=["assistant"])


def _own_conversation(db: Session, user: User, conversation_id: int) -> Conversation:
    convo = db.get(Conversation, conversation_id)
    if convo is None or convo.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return convo


def _openable(db: Session, user: User, document_id: int) -> Document:
    """The document to ask about: it must exist and the person must be able to open it."""
    doc = db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    decision = viewer(db, user).can_view(doc)
    if not decision.allowed:
        if not is_discoverable_to(user, doc):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
        raise HTTPException(status.HTTP_403_FORBIDDEN, decision.reason)
    return doc


def _with_current_actions(db: Session, messages: list[Message]) -> list[MessageOut]:
    """Messages as stored, with each action card showing what has happened to it since."""
    ids = [a["id"] for m in messages for a in (m.meta or {}).get("actions", []) if isinstance(a, dict) and "id" in a]
    rows = {r.id: r for r in db.scalars(select(AssistantAction).where(AssistantAction.id.in_(ids)))} if ids else {}
    out = []
    for m in messages:
        meta = dict(m.meta or {})
        if meta.get("actions"):
            meta["actions"] = [actions.as_dict(rows[a["id"]]) if a.get("id") in rows else a for a in meta["actions"]]
        out.append(MessageOut(id=m.id, role=m.role, content=m.content, meta=meta, created_at=m.created_at))
    return out


@router.post("/chat", response_model=ChatOut)
def chat(body: ChatIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    chat_limiter.check(user.id)
    question = body.message.strip() or (QUICK[body.intent] if body.intent else "")
    if not question:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Message is empty")
    if body.compare_with is not None:
        _openable(db, user, body.compare_with)

    if body.conversation_id:
        convo = _own_conversation(db, user, body.conversation_id)
        if body.document_id is not None and body.document_id != convo.document_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "This conversation is about another document")
        document_id = convo.document_id
    else:
        document_id = body.document_id
        if document_id is not None:
            doc = _openable(db, user, document_id)
            title = f"About “{doc.title}”"
        else:
            title = question
        convo = Conversation(user_id=user.id, title=short_title(title), document_id=document_id)
        db.add(convo)
        db.flush()

    history = list(convo.messages)
    reply = respond(db, user, question, history, document_id=document_id, intent=body.intent, compare_with=body.compare_with)
    user_msg, bot_msg = record_exchange(db, user, convo, question, reply, intent=body.intent, document_id=document_id)
    db.commit()
    return ChatOut(
        conversation=ConversationOut.model_validate(convo),
        user_message=MessageOut.model_validate(user_msg),
        reply=MessageOut.model_validate(bot_msg),
    )


@router.get("/conversations", response_model=list[ConversationOut])
def conversations(document_id: int | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    stmt = select(Conversation).where(Conversation.user_id == user.id)
    if document_id is not None:
        stmt = stmt.where(Conversation.document_id == document_id)
    return db.scalars(stmt.order_by(Conversation.updated_at.desc(), Conversation.id.desc()).limit(50)).all()


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def conversation(conversation_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    convo = _own_conversation(db, user, conversation_id)
    base = ConversationOut.model_validate(convo)
    return ConversationDetail(**base.model_dump(), messages=_with_current_actions(db, list(convo.messages)))


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(conversation_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.delete(_own_conversation(db, user, conversation_id))
    db.commit()


@router.post("/actions", response_model=ActionOut, status_code=status.HTTP_201_CREATED)
def propose_action(body: ActionProposeIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The person asked for an action themselves (the "Save" button under an answer)."""
    msg = db.get(Message, body.message_id)
    if msg is None or msg.role != "assistant" or _own_conversation(db, user, msg.conversation_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Answer not found")
    asked = db.scalar(
        select(Message.content)
        .where(Message.conversation_id == msg.conversation_id, Message.role == "user", Message.id < msg.id)
        .order_by(Message.id.desc())
        .limit(1)
    )
    title = actions.default_title(asked or "")
    [row] = actions.persist(
        db, user, [actions.proposal("save_summary", f"Save “{title}”", {"message_id": msg.id, "title": title})]
    )
    db.commit()
    return row


@router.post("/actions/{action_id}/confirm", response_model=ActionOut)
def confirm_action(action_id: int, body: ActionConfirmIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = actions.load_own(db, user, action_id)
    actions.confirm(db, user, row, body.model_dump(exclude_none=True))
    db.commit()
    db.refresh(row)
    return actions.as_dict(row)


@router.post("/actions/{action_id}/dismiss", response_model=ActionOut)
def dismiss_action(action_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = actions.load_own(db, user, action_id)
    actions.dismiss(db, row)
    db.commit()
    db.refresh(row)
    return actions.as_dict(row)
