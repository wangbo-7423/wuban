"""Alembic 迁移环境：与 app.core.config 单一配置源对齐。

设计要点（与项目铁律一致）：
- **URL 只来自 settings.database_url**（backend/.env），不在本文件或 alembic.ini 写死，
  避免「alembic 连 A 库、应用连 B 库」的配置漂移；
- **target_metadata 来自 app.core.db.Base**，并 `import app.models` 触发全部 ORM 注册，
  autogenerate 才能比对出完整 schema；
- compare_type=True：列类型变更（如 String(50)→String(100)）也要被 autogenerate 捕获
  （默认只比对存在性，不比对类型）。
"""
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# 确保 backend/ 在 sys.path（alembic.ini 的 prepend_sys_path=. 已覆盖，
# 这里兜底处理从其他目录调用 alembic.ini 的情况）
import sys
from pathlib import Path

BACKEND_DIR = str(Path(__file__).resolve().parents[1])
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.core.config import settings  # noqa: E402
from app.core.db import Base  # noqa: E402
import app.models  # noqa: F401,E402  触发全部 ORM 模型注册到 Base.metadata

config = context.config

# 单一配置源：URL 从 settings 注入（可用 ALEMBIC_DATABASE_URL 临时覆盖，
# 例如指向一次性验证库做 autogenerate / schema 对比）
config.set_main_option(
    "sqlalchemy.url",
    __import__("os").environ.get("ALEMBIC_DATABASE_URL") or settings.database_url,
)

# fileConfig 会重置全局 logging 配置；被应用代码以 Python API 调用时
# （cfg.attributes["configure_logger"] = False）跳过，避免冲掉应用日志配置
if config.config_file_name is not None and config.attributes.get(
    "configure_logger", True
):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL，不连库（--sql）。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连接数据库执行迁移。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
