"""会话与消息数据访问。"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Conversation, Message
from app.schemas.card import CardMessage


def _uuid() -> str:
    return str(uuid.uuid4())


# ── Conversation ───────────────────────────────────────
def list_conversations(db: Session, user_id: str) -> list[Conversation]:
    stmt = (
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc())
    )
    return list(db.scalars(stmt).all())


def get_conversation(db: Session, conv_id: str, user_id: str) -> Conversation | None:
    obj = db.get(Conversation, conv_id)
    if obj is None or obj.user_id != user_id:
        return None
    return obj


def create_conversation(db: Session, user_id: str, course_id: str, title: str) -> Conversation:
    conv = Conversation(user_id=user_id, course_id=course_id, title=title or "新对话")
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


# ── Message ────────────────────────────────────────────
def add_message(
    db: Session,
    conversation_id: str,
    role: str,
    text: str = "",
    *,
    id: str | None = None,
    card_type: str = "text",
    payload: dict | None = None,
    evidence: list | None = None,
    confidence: float | None = None,
    gave_answer: bool | None = None,
    scaffold_level: str | None = None,
    reason: str | None = None,
    next_action: str | None = None,
    strategy: list | None = None,
    tool_calls: list | None = None,
    thinking: str | None = None,
) -> Message:
    """写入一条消息（与 CardMessage 字段一一对应，落库为 JSON）。

    id 透传 CardMessage.id：前端拿到的卡片 id 必须与库里主键一致，
    否则练习提交等「按 card_id 定位」的交互在刷新后全部失配。
    """
    msg = Message(
        id=id or _uuid(),
        conversation_id=conversation_id,
        role=role,
        card_type=card_type,
        text=text or "",
        payload=payload,
        evidence=evidence,
        confidence=confidence,
        gave_answer=gave_answer,
        scaffold_level=scaffold_level,
        reason=reason,
        next_action=next_action,
        strategy=strategy,
        # 新增：与 Agent 配套
        tool_calls=tool_calls,
        thinking=thinking,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


def list_messages(db: Session, conversation_id: str) -> list[Message]:
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
    )
    return list(db.scalars(stmt).all())


def get_message(db: Session, conversation_id: str, message_id: str) -> Message | None:
    """取某会话下的一条消息；不存在或不属于该会话返回 None（防越权）。"""
    obj = db.get(Message, message_id)
    if obj is None or obj.conversation_id != conversation_id:
        return None
    return obj


def message_to_card(m: Message) -> CardMessage:
    """落库 Message → 前端 CardMessage。"""
    return CardMessage(
        id=m.id,
        role=m.role,
        card_type=m.card_type,
        text=m.text,
        payload=m.payload,
        evidence=m.evidence,
        confidence=m.confidence,
        gave_answer=m.gave_answer,
        scaffold_level=m.scaffold_level,
        reason=m.reason,
        next_action=m.next_action,
        strategy=m.strategy,
        tool_calls=m.tool_calls,
        thinking=m.thinking,
        created_at=m.created_at,
    )
