"""长期记忆（MCP 知识图谱）API：查看图谱 / 从历史重建。

- GET  /api/student/memory/graph    读当前用户记忆图谱（实体 + 关系 + 统计）；
- POST /api/student/memory/rebuild  清空后从全部对话历史重放重建（同步执行，
  消息多时可能要几十秒——逐批调 GLM 抽取）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.core.response import ok
from app.core.security import get_current_user
from app.models import User
from app.services import memory_service, profile_service

router = APIRouter()


@router.get("/memory/graph")
def memory_graph(user: User = Depends(get_current_user)) -> dict:
    data = memory_service.get_graph(user.id)
    return ok(data)


@router.post("/memory/rebuild")
def memory_rebuild(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    stats = memory_service.rebuild_from_history(db, user.id)
    if stats.get("ok"):
        # 重建完顺带刷新学习画像（概念主题变了）
        profile_service.update_cognitive_state(db, user.id)
    return ok(stats)
