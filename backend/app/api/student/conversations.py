"""会话管理：列表 / 新建 / 历史消息 / 删除。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.core.errors import BizError, ErrorCode
from app.core.response import ok
from app.core.security import get_current_user
from app.models import User
from app.repositories import chat_repo
from app.schemas.chat import ConversationDetail, ConversationOut, NewConvIn

router = APIRouter()


@router.get("/conversations")
def conversations(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    items = [
        ConversationOut(
            id=c.id,
            course_id=c.course_id,
            title=c.title,
            created_at=c.created_at,
            updated_at=c.updated_at,
        )
        for c in chat_repo.list_conversations(db, user.id)
    ]
    return ok([i.model_dump() for i in items])


@router.post("/conversations")
def new_conversation(
    payload: NewConvIn = NewConvIn(),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    conv = chat_repo.create_conversation(
        db, user.id, payload.course_id, payload.title or "新对话"
    )
    out = ConversationOut(
        id=conv.id,
        course_id=conv.course_id,
        title=conv.title,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
    )
    return ok(out.model_dump())


@router.get("/conversations/{conv_id}/messages")
def messages(
    conv_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    conv = chat_repo.get_conversation(db, conv_id, user.id)
    if conv is None:
        raise BizError(ErrorCode.NOT_FOUND, "会话不存在")
    msgs = [
        chat_repo.message_to_card(m).model_dump()
        for m in chat_repo.list_messages(db, conv_id)
    ]
    detail = ConversationDetail(
        id=conv.id,
        course_id=conv.course_id,
        title=conv.title,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        messages=msgs,
    )
    return ok(detail.model_dump())


@router.delete("/conversations/{conv_id}")
def delete_conversation(
    conv_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    conv = chat_repo.get_conversation(db, conv_id, user.id)
    if conv is None:
        raise BizError(ErrorCode.NOT_FOUND, "会话不存在")
    db.delete(conv)
    db.commit()
    return ok({"deleted": conv_id})
