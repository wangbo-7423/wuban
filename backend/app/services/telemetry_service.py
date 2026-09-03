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
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.agent.orchestrator import AgentResult
from app.models.telemetry import AgentTelemetry

logger = logging.getLogger(__name__)

_FAIL_NAMES_CAP = 200  # tool_fail_names 字段的截断长度

# 计算类工具：数学意图轮里，这些一个都没调而答案含数字 → 裸算（bare_numeric）
_MATH_TOOLS = {"calculator", "calculus", "ode", "code_runner"}
# 数学场景装配的标志工具（allowed_tools 里出现 → 本轮按数学口径观测）
_MATH_SCENE_MARKS = {"calculator", "calculus", "ode"}
# 检索类调用：web_search 工具与 search_verify skill 的结果同形状
# （results[] 带 url/authority），遥测口径合并统计（docs/11 §6.5）
_SEARCH_TOOLS = {"web_search", "search_verify"}
_DOMAIN_CAP = 6  # search_top_domains 最多记录的域个数
_AUTHORITY_HIGH = 0.85  # 权威结果阈值（官方文档/论文/edu，docs/11 §4）


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
            _fill_search_and_verify_metrics(row, result, set(allowed_tools or []))
        row.latency_ms = int(latency_ms)
        db.add(row)
    except Exception:  # noqa: BLE001
        logger.warning("agent 遥测记录失败（已忽略，不影响主链路）", exc_info=True)


def _fill_search_and_verify_metrics(
    row: AgentTelemetry, result: AgentResult, allowed: set[str]
) -> None:
    """检索与核验指标（docs/11 §4），全部从 AgentResult 确定性推导。

    - search_calls / search_results / search_top_domains：检索类调用
      （web_search 工具 + search_verify skill，结果同形状）的次数、
      返回结果总数、命中域（截断存储）——权威域占比、触发率的原料；
    - bare_numeric：数学口径轮次（装配单里有 calculator/calculus/ode），
      答案含数字但本轮一个计算工具都没调。启发式是有意的粗口径：
      「含数字」会把少量不含计算结果的轮也算进来，作为观测指标宁可
      高估也不漏报——指标只用于对比改动前后趋势，不做个体审判。
    """
    search_calls = 0
    search_results = 0
    authority_hits = 0
    domains: list[str] = []
    used_tools = set()
    for tc in result.tool_calls:
        used_tools.add(tc.get("tool_name"))
        if tc.get("tool_name") not in _SEARCH_TOOLS:
            continue
        search_calls += 1
        r = tc.get("result") or {}
        rows = r.get("results") or []
        search_results += len(rows) if isinstance(rows, list) else 0
        for item in rows if isinstance(rows, list) else []:
            item = item or {}
            try:
                if float(item.get("authority") or 0) >= _AUTHORITY_HIGH:
                    authority_hits += 1
            except (TypeError, ValueError):
                pass
            url = str(item.get("url") or "")
            if not url:
                continue
            try:
                domain = (urlparse(url).hostname or "").lower()
            except ValueError:
                continue
            if domain and domain not in domains:
                domains.append(domain)
    row.search_calls = search_calls
    row.search_results = search_results
    row.search_authority_hits = authority_hits
    row.search_top_domains = ",".join(domains[:_DOMAIN_CAP]) or None

    math_scene = bool(allowed & _MATH_SCENE_MARKS)
    has_math_tool = bool(used_tools & _MATH_TOOLS)
    row.bare_numeric = bool(
        math_scene and not has_math_tool and any(c.isdigit() for c in result.text)
    )
