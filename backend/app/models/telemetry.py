"""Agent 遥测表（Harness Engineering 警示二的落地：failure log 当一等公民）。

设计动机（docs/09-Harness工程设计借鉴.md §4）：
- 「harness 可以扔，数据不能扔」——每次模型升级 / 编排重构后，这批数据
  是判断新 harness 是否退步的基准（警示一：同类任务成功率降 5pp 即信号）；
- 运维侧：缓存命中率、压缩节省、工具失败率——成本与质量的观测底座
  （对应企业级架构的「新可观测性体系」）；
- 教育侧：学生卡壳证据链的原始数据——哪轮工具失败、AI 引导了几步收敛。

字段都是「一轮 Agent 调用」的聚合值，真源是 orchestrator 的 AgentResult。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class AgentTelemetry(Base):
    __tablename__ = "agent_telemetry"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    course_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # 本轮意图路由装配的工具子集（逗号连接；Select 策略的留痕）
    allowed_tools: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ── 成败与错误（failure log 核心）────────────────────
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── 工具循环指标 ────────────────────────────────────
    tool_steps: Mapped[int] = mapped_column(Integer, default=0)     # 工具循环轮数
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)     # 工具调用总次数
    tool_failures: Mapped[int] = mapped_column(Integer, default=0)  # 失败次数
    tool_fail_names: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ── 上下文工程指标（成本 / 质量观测）────────────────
    compressed_chars: Mapped[int] = mapped_column(Integer, default=0)  # 压缩节省字符
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)     # 输入 token
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)     # 命中缓存 token
    thinking_chars: Mapped[int] = mapped_column(Integer, default=0)    # 思维链字符
    text_chars: Mapped[int] = mapped_column(Integer, default=0)        # 最终回答字符

    latency_ms: Mapped[int] = mapped_column(Integer, default=0)        # 端到端耗时
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
