"""对话卡片协议（前后端契约核心）。

对齐：
- `docs/03-卡片协议.md`（CardMessage / CardType / CardPayload）
- `docs/05-前端设计与组件规范.md` §6（CardRenderer 分发与组件映射）

后端 GLM Agent 把回应打成「CardMessage」向前端流式/整体输出；
前端 `<CardRenderer>` 按 `card_type` 渲染。
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


# ────────────────────────────────────────────────────────────
# 1. 卡片类型（前端按这个字符串路由到对应渲染器）
# ────────────────────────────────────────────────────────────

CardType = Literal[
    "text",                   # 纯文本
    "choice",                 # 单/多选 → 概念辨析、反例题
    "question",               # 反问（引导式：先问再答）
    "practice",               # 练习题（含 stem / options / answer / explanation）
    "understand",             # 概念解释（公式 / 定理 / 定义）
    "math",                   # 数学计算过程（行间公式 / 步骤）
    "engineering",            # 工程实操（步骤 + 命令 + 注意事项）
    "feedback",               # 反馈（自评/测评）
    "recommendation",         # 推荐（下一步学习资源、路径节点）
    "evidence",               # 引证（源码 / 文档片段 + 链接）
    "progress",               # 学习进度更新
    "warning",                # 风险提示（认知负荷过高/前置缺失）
    "metacog",                # 元认知提示（鼓励自查）
    "motivation",             # 学习动机反馈
    "scaffold_progress",      # 脚手架推进（example → faded → hint → independent）
    "tool_call",              # Agent 工具调用中间态（前端可显示加载）
]


ScaffoldLevel = Literal["example", "faded", "hint", "independent"]
NextAction = Literal["accept", "adjust", "reject", "retry", "ask", "none"]


# ────────────────────────────────────────────────────────────
# 2. 卡片载荷（按 card_type 不同，payload 内容不一样）
# ────────────────────────────────────────────────────────────


class CardEvidence(BaseModel):
    """引证来源（KG 节点 / 教材章节 / 网页等）。"""

    source: str
    page: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class MathStep(BaseModel):
    """数学计算的一步：公式 + 说明。"""

    expr: str = Field(description="LaTeX 或纯文本，如 r'\\frac{a}{b}' 或 'a/b'")
    note: str | None = None


class MathBlock(BaseModel):
    """数学块：原题 + 步骤 + 最终答案。

    前端用 KaTeX 渲染 `expr`，旁边显示 `note`。
    """

    problem: str
    steps: list[MathStep]
    answer: str | None = None
    unit: str | None = None


class EngineeringStep(BaseModel):
    """工程实操的一步。"""

    title: str
    command: str | None = None
    expected: str | None = None
    pitfalls: list[str] | None = None  # 常见坑
    next_step_hint: str | None = None


class CardPayload(BaseModel):
    """卡片动态载荷（不同 card_type 字段不同，全为可空）。"""

    # choice
    options: list[dict] | None = None
    # question / practice
    stem: str | None = None
    hint_level: int | None = None
    max_hints: int | None = None
    # math
    math: MathBlock | None = None
    # engineering
    engineering_steps: list[EngineeringStep] | None = None
    engineering_artifacts: list[dict] | None = None  # 截图/链接
    # 通用
    mastery: dict[str, float] | None = None  # 各知识点掌握度
    risk: list[dict] | None = None
    meta: dict | None = None


# ────────────────────────────────────────────────────────────
# 3. 工具调用记录（Agent 工具/MCP 调用痕迹）
# ────────────────────────────────────────────────────────────


class ToolCallRecord(BaseModel):
    """一次工具调用的留痕（前端可显示「正在调用 XX」）。"""

    tool_name: str
    args: dict
    result: str | None = None
    ok: bool = True
    error: str | None = None
    took_ms: int | None = None


# ────────────────────────────────────────────────────────────
# 4. 卡片消息（一次 Agent 输出 = 一张或多张 CardMessage）
# ────────────────────────────────────────────────────────────


class CardMessage(BaseModel):
    """前端可消费的最小单元；持久化用同一结构入库。"""

    id: str
    role: Literal["user", "assistant"] = "assistant"
    card_type: CardType = "text"
    text: str = ""
    payload: CardPayload | None = None
    evidence: list[CardEvidence] | None = None
    confidence: float | None = None
    gave_answer: bool | None = None
    scaffold_level: ScaffoldLevel | None = None
    reason: str | None = None
    next_action: NextAction | None = None
    strategy: list[str] | None = None   # 引导策略标签：类比/分解/反例/可视化
    thinking: str | None = None         # Agent 思考过程（GLM 启用 thinking 时回填）
    tool_calls: list[ToolCallRecord] | None = None
    created_at: datetime | None = None


__all__ = [
    "CardType",
    "ScaffoldLevel",
    "NextAction",
    "CardEvidence",
    "MathStep",
    "MathBlock",
    "EngineeringStep",
    "CardPayload",
    "ToolCallRecord",
    "CardMessage",
]
