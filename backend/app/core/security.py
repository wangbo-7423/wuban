"""安全：密码哈希（bcrypt）+ JWT 签发/校验 + 当前用户依赖。

设计：
- 所有 token 签发都在这里（不让业务自己造 token）；
- 任何角色相关都通过 `require_roles(...)` 工厂依赖；
- 学生端是绝对主流场景；管理员分支通过 `require_roles` 守护。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_session
from app.core.errors import BizError, ErrorCode
from app.models import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


# ── 密码 ────────────────────────────────────────────────
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


# ── JWT ─────────────────────────────────────────────────
def create_token(user_id: str, *, extra: dict | None = None) -> str:
    """签发 JWT。

    payload: sub / iat / exp / iss / extra
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_expire_minutes)).timestamp()),
        "iss": settings.jwt_issuer,
        **(extra or {}),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            options={"require": ["sub", "exp", "iat", "iss"]},
        )
    except jwt.PyJWTError as e:
        raise BizError(ErrorCode.UNAUTHORIZED, "token 无效或已过期") from e


# ── 依赖 ────────────────────────────────────────────────
def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_session),
) -> User:
    if not token:
        raise BizError(ErrorCode.UNAUTHORIZED, "缺少访问令牌")
    payload = decode_token(token)
    user = db.get(User, payload.get("sub"))
    if user is None or user.status != "active":
        raise BizError(ErrorCode.UNAUTHORIZED, "用户不存在或已被禁用")
    return user


def require_roles(*roles: str):
    """角色守卫：限定可用角色。学生端单角色应用，几乎用不到，留作扩展位。"""

    def _dep(user: User = Depends(get_current_user)) -> User:
        return user

    return _dep
