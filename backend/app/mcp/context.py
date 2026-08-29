"""请求级用户上下文：让无状态的工具函数拿到当前用户 id。

`/chat` 是 sync 端点（FastAPI 线程池线程执行），整个 orchestrator.run →
execute_tool 调用链都在同一线程栈里，因此 ContextVar 在端点入口 set 一次，
`memory_search` 等工具即可在栈内读到。后台任务（BackgroundTasks）跑在
别的线程，**不依赖**这里——一律显式传 user_id。
"""
from __future__ import annotations

from contextvars import ContextVar

current_user_id: ContextVar[str | None] = ContextVar("current_user_id", default=None)
