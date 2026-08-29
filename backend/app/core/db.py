"""数据库与会话：统一 PostgreSQL（SQLAlchemy 2.x + psycopg2）。

设计要点：
- **只连 PostgreSQL**：不提供 SQLite 兜底，避免开发用 SQLite / 生产错连；
- 启动期 `init_db()` 仅用于本地开发（`create_all`），生产请用 Alembic 迁移；
- `get_session` 是 FastAPI 依赖项，每个请求一个 Session，结束关闭。
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    """所有 ORM 模型基类。"""


def _build_engine_url(database_url: str) -> URL | str:
    """构造 SQLAlchemy 连接 URL。

    SQLAlchemy 2.x 同时支持 str 和 URL；保持 str 与原有调用方式一致。
    """
    return database_url


engine = create_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_pre_ping=True,           # 连接前 SELECT 1，断线自动重连
    echo=settings.db_echo,
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


def init_db() -> None:
    """建表（仅本地开发用）。生产请改 Alembic 迁移。"""
    # 触发 ORM 模型注册到 Base.metadata
    import app.models  # noqa: F401

    Base.metadata.create_all(bind=engine)


def get_session():
    """FastAPI 依赖：每个请求一个 session。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
