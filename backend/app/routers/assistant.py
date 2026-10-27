from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..assistant import respond
from ..database import get_db
from ..deps import chat_limiter, current_user
from ..models import Conversation, Message, User, utcnow
from ..schemas import ChatIn, ChatOut, ConversationDetail, ConversationOut, MessageOut

router = APIRouter(prefix="/api/assistant", tags=["assistant"])


def _own_conversation(db: Session, user: User, conversation_id: int) -> Conversation:
    convo = db.get(Conversation, conversation_id)
    if convo is None or convo.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return convo


@router.post("/chat", response_model=ChatOut)
def chat(body: ChatIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    chat_limiter.check(user.id)
    question = body.message.strip()
    if not question:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Message is empty")

    if body.conversation_id:
        convo = _own_conversation(db, user, body.conversation_id)
    else:
        title = question if len(question) <= 60 else question[:57].rsplit(" ", 1)[0] + "…"
        convo = Conversation(user_id=user.id, title=title)
        db.add(convo)
        db.flush()

    history = list(convo.messages)
    reply = respond(db, user, question, history)

    user_msg = Message(conversation_id=convo.id, role="user", content=question, meta={})
    bot_msg = Message(conversation_id=convo.id, role="assistant", content=reply.content, meta=reply.meta)
    db.add_all([user_msg, bot_msg])
    convo.updated_at = utcnow()
    audit.record(
        db,
        user,
        "assistant.query",
        "conversation",
        convo.id,
        question=question[:300],
        mode=reply.mode,
        fallback=reply.fallback_reason,
        cited=[c["id"] for c in reply.citations],
        locked=[l["id"] for l in reply.locked],
    )
    db.commit()
    return ChatOut(
        conversation=ConversationOut.model_validate(convo),
        user_message=MessageOut.model_validate(user_msg),
        reply=MessageOut.model_validate(bot_msg),
    )


@router.get("/conversations", response_model=list[ConversationOut])
def conversations(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return db.scalars(
        select(Conversation).where(Conversation.user_id == user.id).order_by(Conversation.updated_at.desc()).limit(50)
    ).all()


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def conversation(conversation_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _own_conversation(db, user, conversation_id)


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(conversation_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.delete(_own_conversation(db, user, conversation_id))
    db.commit()
