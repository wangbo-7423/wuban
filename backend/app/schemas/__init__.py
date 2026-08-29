"""Pydantic 请求/响应 DTO 层（参数校验 = class-validator 的等价物）。

本包是前后端**类型契约**真源。任何字段顺序/名称调整都是 breaking change，
前端 `frontend/src/api/types.ts` 需同步更新。
"""
from . import auth as auth
from . import card as card
from . import chat as chat
from . import learning as learning

__all__ = ["auth", "card", "chat", "learning"]
