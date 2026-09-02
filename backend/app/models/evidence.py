"""过程性证据表（mastery_evidence 工具的落点）。

定位（docs/10-认知状态与工具边界.md）：
- 只存**证据标签**，绝不存分数——「AI 觉得学生懂了」不算数，存的是 AI
  在对话中观察到的行为时刻（自我解释/深问/卡壳/迁移/预测验证）；
- 写入方：`mastery_evidence` 工具（GLM 在对话中实时调用，每次一条）；
- 消费方：`profile_service.compute_cognitive_state` 聚合进 cognitive_state
  的 topics（process evidence，与正则/词频识别互补）；
- 红线对齐：掌握度来自对话过程性证据（docs/00 §6），本表是其载体之一，
  永远不直接等于「掌握度分数」。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class LearningEvidence(Base):
    __tablename__ = "learning_evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # 受控词表：self_explained / deep_question / stuck / transferred / prediction_checked
    evidence_type: Mapped[str] = mapped_column(String(32), index=True)
    topic: Mapped[str] = mapped_column(String(128))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)  # 一句话现场描述
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
