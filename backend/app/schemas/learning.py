"""学习画像 / 学习域 / 学习项目 DTO。"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LearnerProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    learner_type: str | None = None
    goal_type: str | None = None
    learning_style: str | None = None
    cognitive_state: dict | None = None
    profile_confidence: float = 0.0
    updated_at: datetime


class LearnerProfileUpdate(BaseModel):
    learner_type: str | None = None
    goal_type: str | None = None
    learning_style: str | None = None
    cognitive_state: dict | None = None


# ── 学习域 ─────────────────────────────────────────────
class DomainIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    subject: str | None = None
    meta: dict | None = None


class DomainOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    subject: str | None = None
    meta: dict | None = None
    created_at: datetime


# ── 学习项目 ───────────────────────────────────────────
class ProjectIn(BaseModel):
    domain_id: str | None = None
    goal: str | None = Field(default=None, max_length=512)
    start_state: dict | None = None
    status: Literal["active", "paused", "done"] = "active"


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    domain_id: str | None = None
    goal: str | None = None
    start_state: dict | None = None
    status: str = "active"
    created_at: datetime
