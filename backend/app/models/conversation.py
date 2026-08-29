"""会话与消息持久化（对话历史列表 + 聊天落库）。"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    course_id: Mapped[str] = mapped_column(String(32), default="general")
    title: Mapped[str] = mapped_column(String(128), default="新对话")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # ── Write 策略（上下文工程）：信息搬到窗口外持久化，跨轮次/中断可恢复 ──
    # 短期记忆层：早期对话的滚动压缩摘要，覆盖前 summary_covered 条消息；
    # 原始消息永远全量留痕在 messages，这里只是注入用的压缩视图。
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_covered: Mapped[int] = mapped_column(Integer, default=0)
    # 任务状态层：Scratchpad 草稿纸——{topic, progress, pending}，记录学到哪、等学生做什么
    scratchpad: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # 级联删除：删会话时一并清掉名下所有消息，否则会撞外键约束报 500。
    # 刻意不加 passive_deletes=True —— 让 SQLAlchemy 主动发 DELETE 子表语句，
    # 这样对「已建好、DDL 上没带 ON DELETE CASCADE」的存量库同样立即生效。
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # ondelete="CASCADE" 是 DDL 层（新建表 / 未来 Alembic 迁移生效）；
    # ORM 层那半由 Conversation.messages 的 cascade 负责，两者是双保险。
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    conversation: Mapped["Conversation"] = relationship(back_populates="messages")
    role: Mapped[str] = mapped_column(String(16))                 # user/assistant
    card_type: Mapped[str] = mapped_column(String(24), default="text")
    # Text 不限长：GLM 长回答 / 思维链轻松破万字符，VARCHAR 会直接写入报错
    text: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    evidence: Mapped[list | None] = mapped_column(JSON, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    gave_answer: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    scaffold_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    next_action: Mapped[str | None] = mapped_column(String(16), nullable=True)
    strategy: Mapped[list | None] = mapped_column(JSON, nullable=True)   # 引导策略标签
    # ── Agent 新增字段（对齐 GLM + Tools 编排）────────────
    tool_calls: Mapped[list | None] = mapped_column(JSON, nullable=True)  # 工具调用记录
    thinking: Mapped[str | None] = mapped_column(Text, nullable=True)  # 思维链（启用 thinking 时）
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
