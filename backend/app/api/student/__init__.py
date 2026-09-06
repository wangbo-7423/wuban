"""学生端路由包（原 api/student.py 单文件，按职责拆分）。

- chat          对话（同步 + SSE 流式）+ 工具清单
- practice      练习/选择卡作答闭环
- conversations 会话管理
- insights      学习上下文 / 探索档案
- support       上述模块共用的支撑工具

对外仍是 `from app.api.student import router`（main.py 不感知拆分）。
"""
from fastapi import APIRouter

from app.api.student.chat import router as chat_router
from app.api.student.conversations import router as conversations_router
from app.api.student.insights import router as insights_router
from app.api.student.practice import router as practice_router

router = APIRouter()
router.include_router(chat_router)
router.include_router(conversations_router)
router.include_router(practice_router)
router.include_router(insights_router)
