"""Agent 遥测落库（Harness Engineering 警示二的落地：failure log 当一等公民）。

> "The dataset of failures from your current harness is more valuable than
>   the harness itself." —— Phil Schmid, Bitter Lesson 三原则之一

每轮 Agent 调用（同步/流式、成功/失败）写一行 agent_telemetry：
- **迭代侧**：换模型 / 重构编排后，这批数据是判断新 harness 是否退步的基准
  （警示一：同类任务成功率连续降 5pp 即重写信号）；
- **运维侧**：缓存命中率、压缩节省、工具失败率——成本与质量的观测底座；
- **教育侧**：学生卡壳证据链的原始数据——哪轮工具失败、AI 引导几步收敛。

设计约束：
1. **绝不抛异常**——遥测挂了不能影响聊天主链路，任何错误只打 warning；
2. **随调用方事务提交**——本模块只 db.add() 不 commit，交给端点既有的
   commit 点；错误路径（先 rollback 再落错误卡再 commit）里在 commit 前
   追加即可，遥测随错误卡一起持久化；
3. 单行聚合——不存逐 token 明细，一行 = 一轮调用的完整画像，查询友好。
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.agent.orchestrator import AgentResult
from app.models.telemetry import AgentTelemetry

logger = logging.getLogger(__name__)

_FAIL_NAMES_CAP = 200  # tool_fail_names 字段的截断长度


def record_agent_turn(
    db: Session,
    *,
    user_id: str | None = None,
    conversation_id: str | None = None,
    course_id: str | None = None,
    allowed_tools: list[str] | None = None,
    result: AgentResult | None = None,
    success: bool = True,
    error: str | None = None,
    latency_ms: int = 0,
) -> None:
    """把一轮 Agent 调用的聚合指标追加到当前事务（不 commit）。

    成功路径传 result=AgentResult；失败路径 result 可为 None，
    传 success=False + error=「code: message」。
    """
    from app.core.config import settings
    if not settings.enable_agent_telemetry:
        return
    try:
        row = AgentTelemetry(
            user_id=user_id,
            conversation_id=conversation_id,
            course_id=course_id,
            allowed_tools=(
                ",".join(allowed_tools)[:255] if allowed_tools else None
            ),
            ok=success,
            error=(error or None),
            # 显式补零：列 default 只在 flush 时生效，构造期读到的才是确定值
            tool_steps=0,
            tool_calls=0,
            tool_failures=0,
            compressed_chars=0,
            prompt_tokens=0,
            cached_tokens=0,
            thinking_chars=0,
            text_chars=0,
            latency_ms=0,
        )
        if result is not None:
            fails = [tc for tc in result.tool_calls if not tc.get("ok")]
            fail_names = []
            for tc in fails:
                if tc["tool_name"] not in fail_names:
                    fail_names.append(tc["tool_name"])
            row.tool_steps = result.tool_steps
            row.tool_calls = len(result.tool_calls)
            row.tool_failures = len(fails)
            row.tool_fail_names = (
                ",".join(fail_names)[:_FAIL_NAMES_CAP] if fails else None
            )
            row.compressed_chars = result.compressed_chars
            row.prompt_tokens = result.prompt_tokens
            row.cached_tokens = result.cached_tokens
            row.thinking_chars = len(result.thinking or "")
            row.text_chars = len(result.text or "")
        row.latency_ms = int(latency_ms)
        db.add(row)
    except Exception:  # noqa: BLE001
        logger.warning("agent 遥测记录失败（已忽略，不影响主链路）", exc_info=True)
