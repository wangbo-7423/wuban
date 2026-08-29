"""学习画像 / 学习域 / 学习项目 数据访问。"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import LearnerProfile, LearningDomain, LearningProject


# ── LearnerProfile ─────────────────────────────────────
def get_profile_by_user(db: Session, user_id: str) -> LearnerProfile | None:
    return db.scalar(select(LearnerProfile).where(LearnerProfile.user_id == user_id))


# ── Domain ─────────────────────────────────────────────
def list_domains(db: Session, keyword: str | None = None) -> list[LearningDomain]:
    stmt = select(LearningDomain).order_by(LearningDomain.created_at.desc())
    if keyword:
        stmt = stmt.where(LearningDomain.name.ilike(f"%{keyword}%"))
    return list(db.scalars(stmt).all())


def get_domain(db: Session, domain_id: str) -> LearningDomain | None:
    return db.get(LearningDomain, domain_id)


def create_domain(db: Session, **kwargs) -> LearningDomain:
    obj = LearningDomain(**kwargs)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


# ── Project ────────────────────────────────────────────
def list_projects(db: Session, user_id: str) -> list[LearningProject]:
    stmt = (
        select(LearningProject)
        .where(LearningProject.user_id == user_id)
        .order_by(LearningProject.created_at.desc())
    )
    return list(db.scalars(stmt).all())


def get_project(db: Session, project_id: str, user_id: str | None = None) -> LearningProject | None:
    obj = db.get(LearningProject, project_id)
    if obj is None or (user_id is not None and obj.user_id != user_id):
        return None
    return obj


def create_project(db: Session, user_id: str, **kwargs) -> LearningProject:
    obj = LearningProject(user_id=user_id, **kwargs)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj
