"""AI 伴学 · FastAPI 入口。

启动：
    uv run uvicorn app.main:app --reload --port 8000

部署：本机或容器皆可；PostgreSQL 由 docker-compose.yml 起。
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.api.learning import router as learning_router
from app.api.memory import router as memory_router
from app.api.student import router as student_router
from app.api.upload import router as upload_router
from app.core.config import settings
from app.core.db import init_db
from app.core.exceptions import register_exception_handlers


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动钩子：建表（仅本地）。生产请用 Alembic 迁移。
    try:
        init_db()
    except Exception as e:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning("init_db() 失败（可能是迁移期）：%s", e)
    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    debug=settings.debug,
    lifespan=lifespan,
)

# ── CORS（开放前端 dev 来源） ────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 全局异常处理：所有错误统一转响应体 ────────────────────
register_exception_handlers(app)

# ── 路由 ────────────────────────────────────────────────
app.include_router(health_router, prefix="/api", tags=["demo"])
app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
app.include_router(learning_router, prefix="/api", tags=["learning"])
app.include_router(student_router, prefix="/api/student", tags=["agent"])
app.include_router(memory_router, prefix="/api/student", tags=["memory"])
app.include_router(upload_router, prefix="/api/student", tags=["upload"])

# ── 图片静态服务（upload-image 落盘后通过此路径访问）────
os.makedirs(settings.upload_dir, exist_ok=True)
app.mount("/api/uploads-image", StaticFiles(directory=settings.upload_dir), name="uploads-image")
