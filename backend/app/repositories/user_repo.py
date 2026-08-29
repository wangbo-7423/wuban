"""用户数据访问。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User


def get_by_username(db: Session, username: str) -> User | None:
    return db.scalar(select(User).where(User.username == username))


def get_by_id(db: Session, user_id: str) -> User | None:
    return db.get(User, user_id)


def create_user(
    db: Session,
    username: str,
    nickname: str,
    password_hash: str,
    avatar_url: str | None = None,
) -> User:
    """创建用户（role 固定 student；不存 email）。"""
    user = User(
        username=username,
        nickname=nickname,
        password_hash=password_hash,
        avatar_url=avatar_url,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
