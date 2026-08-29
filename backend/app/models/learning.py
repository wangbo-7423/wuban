"""个人伴学核心模型：学习画像 / 学习域 / 学习项目。

个人伴学定位下，`学习域(domain)` 与 `学习项目(project)` 替代「课程/班级」成为核心；
`learner_profiles` 存学习画像与认知状态，与认证身份 `users` 解耦。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class LearnerProfile(Base):
    __tablename__ = "learner_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    learner_type: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 高中/大学/在职
    goal_type: Mapped[str | None] = mapped_column(String(32), nullable=True)      # 应试/认证/技能/兴趣/发展
    learning_style: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cognitive_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    profile_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class LearningDomain(Base):
    __tablename__ = "learning_domains"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(128))
    subject: Mapped[str | None] = mapped_column(String(64), nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LearningProject(Base):
    __tablename__ = "learning_projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    domain_id: Mapped[str | None] = mapped_column(ForeignKey("learning_domains.id"), nullable=True)
    goal: Mapped[str | None] = mapped_column(String(512), nullable=True)
    start_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active")  # active/paused/done
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
