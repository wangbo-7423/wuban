"""认证/用户接口：注册、登录、当前用户。

对齐 `docs/02-API接口文档.md` §2。安全要点：
- 密码 bcrypt 哈希，绝不存明文；
- 登录失败统一「用户名或密码错误」（防账号枚举）；
- 成功后签发 JWT（sub=user_id, exp, iss）；
- 注册后立刻签发 token 让前端自动登录，避免再次走 login。

注意：本产品不开教师/邮箱注册 —— 任何额外的字段都不要在此文件出现。
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_session
from app.core.errors import BizError, ErrorCode
from app.core.response import ok
from app.core.security import (
    create_token,
    get_current_user,
    hash_password,
    verify_password,
)
from app.models import LearnerProfile, User
from app.repositories import user_repo
from app.schemas.auth import (
    AuthMeOut,
    LoginIn,
    RegisterIn,
    TokenOut,
    UserOut,
)

router = APIRouter()


@router.post("/register", response_model=None)
def register(payload: RegisterIn, db: Session = Depends(get_session)) -> dict:
    if user_repo.get_by_username(db, payload.username):
        raise BizError(ErrorCode.CONFLICT, "用户名已被占用")

    user = user_repo.create_user(
        db,
        username=payload.username,
        nickname=payload.nickname,
        password_hash=hash_password(payload.password),
    )

    # 注册即建 skeleton 学习画像（与 users 解耦）
    profile = LearnerProfile(
        user_id=user.id,
        cognitive_state={},
        profile_confidence=0.0,
    )
    db.add(profile)
    db.commit()

    # 注册成功直接签 token 让前端自动登录
    token = create_token(user.id)
    return ok(
        TokenOut(
            token=token,
            expires_in=settings.jwt_expire_minutes * 60,
            nickname=user.nickname,
            username=user.username,
        ).model_dump(),
        message="注册成功",
    )


@router.post("/login")
def login(payload: LoginIn, db: Session = Depends(get_session)) -> dict:
    user = user_repo.get_by_username(db, payload.username)
    # 统一失败提示，防枚举
    if user is None or not verify_password(payload.password, user.password_hash):
        raise BizError(ErrorCode.UNAUTHORIZED, "用户名或密码错误")
    if user.status != "active":
        raise BizError(ErrorCode.FORBIDDEN, "账号已被禁用")

    user.last_login_at = datetime.now(timezone.utc)
    db.commit()

    token = create_token(user.id)
    return ok(
        TokenOut(
            token=token,
            expires_in=settings.jwt_expire_minutes * 60,
            nickname=user.nickname,
            username=user.username,
        ).model_dump()
    )


@router.get("/me")
def me(user: User = Depends(get_current_user)) -> dict:
    return ok(AuthMeOut.model_validate(user).model_dump())
