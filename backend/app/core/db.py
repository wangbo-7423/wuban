"""数据库与会话：统一 PostgreSQL（SQLAlchemy 2.x + psycopg2）。

设计要点：
- **只连 PostgreSQL**：不提供 SQLite 兜底，避免开发用 SQLite / 生产错连；
- **schema 演进唯一路径是 Alembic**：启动期 `run_migrations()` 等价于
  `alembic upgrade head`（2026-09-02 起取代旧 `init_db()` 的 create_all +
  手写补列 hack——那套做法在 agent_telemetry 上攒出了 nullable 漂移和
  messages 外键丢 CASCADE 的真实债务，见迁移 b128f66432fa）；
- `get_session` 是 FastAPI 依赖项，每个请求一个 Session，结束关闭。
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings

# backend/ 目录（app/core/db.py 上两级）
BACKEND_DIR = Path(__file__).resolve().parents[2]


class Base(DeclarativeBase):
    """所有 ORM 模型基类。"""


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


def run_migrations() -> None:
    """把数据库升级到最新 schema（等价 CLI：`alembic upgrade head`）。

    用 Alembic 的 Python API 在应用启动时执行，dev / 生产同一条路径。
    URL 与 target_metadata 都由 migrations/env.py 从 settings / Base 读取，
    这里只负责触发，不重复配置（单一配置源）。
    """
    from alembic import command
    from alembic.config import Config

    alembic_ini = BACKEND_DIR / "alembic.ini"
    cfg = Config(str(alembic_ini))
    # ini 用 %(here)s 定位 migrations/，显式兜底防止工作目录漂移
    cfg.set_main_option(
        "script_location", str((BACKEND_DIR / "migrations").resolve())
    )
    # 由应用自身调用时不要让 env.py 的 fileConfig 重置全局日志配置
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")


def get_session():
    """FastAPI 依赖：每个请求一个 session。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
