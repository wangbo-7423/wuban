"""学习域 / 学习项目 CRUD（替代课程/班级，服务「个人伴学」主流程）。

默认仅认证用户可用；`/me/profile` 查看/更新自己的学习画像。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.core.errors import BizError, ErrorCode
from app.core.response import ok, page
from app.core.security import get_current_user
from app.models import LearningDomain, LearningProject, User
from app.repositories import learning_repo
from app.schemas.learning import (
    DomainIn,
    DomainOut,
    LearnerProfileOut,
    LearnerProfileUpdate,
    ProjectIn,
    ProjectOut,
)

router = APIRouter()


# ── 学习画像 ──────────────────────────────────────────
@router.get("/me/profile")
def my_profile(user: User = Depends(get_current_user), db: Session = Depends(get_session)) -> dict:
    profile = learning_repo.get_profile_by_user(db, user.id)
    if profile is None:
        raise BizError(ErrorCode.NOT_FOUND, "学习画像不存在")
    return ok(LearnerProfileOut.model_validate(profile).model_dump())


@router.patch("/me/profile")
def update_profile(
    payload: LearnerProfileUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    profile = learning_repo.get_profile_by_user(db, user.id)
    if profile is None:
        raise BizError(ErrorCode.NOT_FOUND, "学习画像不存在")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(profile, k, v)
    db.commit()
    db.refresh(profile)
    return ok(LearnerProfileOut.model_validate(profile).model_dump())


# ── 学习域 ────────────────────────────────────────────
@router.post("/domains")
def create_domain(
    payload: DomainIn, user: User = Depends(get_current_user), db: Session = Depends(get_session)
) -> dict:
    obj = learning_repo.create_domain(db, **payload.model_dump())
    return ok(DomainOut.model_validate(obj).model_dump())


@router.get("/domains")
def list_domains(
    keyword: str | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    items = learning_repo.list_domains(db, keyword)
    return ok([DomainOut.model_validate(i).model_dump() for i in items])


@router.get("/domains/{domain_id}")
def get_domain(
    domain_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_session)
) -> dict:
    obj = learning_repo.get_domain(db, domain_id)
    if obj is None:
        raise BizError(ErrorCode.NOT_FOUND, "学习域不存在")
    return ok(DomainOut.model_validate(obj).model_dump())


# ── 学习项目 ──────────────────────────────────────────
@router.post("/projects")
def create_project(
    payload: ProjectIn, user: User = Depends(get_current_user), db: Session = Depends(get_session)
) -> dict:
    obj = learning_repo.create_project(db, user.id, **payload.model_dump())
    return ok(ProjectOut.model_validate(obj).model_dump())


@router.get("/projects")
def my_projects(user: User = Depends(get_current_user), db: Session = Depends(get_session)) -> dict:
    items = learning_repo.list_projects(db, user.id)
    return ok([ProjectOut.model_validate(i).model_dump() for i in items])


@router.get("/projects/{project_id}")
def get_project(
    project_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_session)
) -> dict:
    obj = learning_repo.get_project(db, project_id, user.id)
    if obj is None:
        raise BizError(ErrorCode.NOT_FOUND, "学习项目不存在")
    return ok(ProjectOut.model_validate(obj).model_dump())
