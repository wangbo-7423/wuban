"""意图路由（Select 策略·Skills 路由）：按学生诉求动态装配本轮工具。

问题：全功能 Agent 注册了 7~8 个工具 schema，但每轮任务只需要 3~4 个。
全量塞进 tools 字段不仅费 token，还会降低工具选择准确率——
LLM 在 8 个候选里挑对工具，远不如在 3 个候选里挑对。

做法（两阶段，对齐课程 3.1.2.4）：
    LLM 意图分类（一次极小的 flash 调用，thinking 关闭，~50 token）
    → 上下文动态装配（本轮 tools 字段只放该意图的工具子集，
      并在 system 里声明本轮可用工具，让模型"看得见的选择面"与真实一致）

失败语义：分类调用失败 / 消息为空（纯图片）→ 降级为全量工具（旧行为），
路由永远不能把主链路搞挂。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.agent import glm_client

logger = logging.getLogger(__name__)

# 意图 → 工具/skill 白名单。memory_search 每轮都给（很小，且回顾记忆对任何场景都有用）；
# calculator 只在 sympy 不可用时才注册，列在这里无害（过滤时自然跳过未注册的）。
INTENT_TOOLS: dict[str, list[str] | None] = {
    "math": ["calculus", "ode", "calculator", "kg_lookup", "memory_search"],
    "engineering": ["project_guide", "code_runner", "kg_lookup", "calculator", "memory_search"],
    "concept": ["kg_lookup", "memory_search", "web_search"],
    "chat": ["memory_search"],
    # None = 不路由，全量装配（分类失败时的兜底，保持旧行为）
    "all": None,
}

_INTENT_LABELS = {
    "math": "数学计算/解题",
    "engineering": "工程实操/编程",
    "concept": "概念/原理理解",
    "chat": "寒暄/其他",
}

_CLASSIFY_SYSTEM = """你是意图分类器。判断这段学生消息的主要诉求，只输出 JSON：
{"intent": "math" | "engineering" | "concept" | "chat"}

判据：
- math：要算/推导数学题（求积分、求导、解微分方程、极限、证明计算类）；
- engineering：要写/跑/调代码、做工程小项目、看仿真结果；
- concept：问概念、原理、为什么、区别联系（不要求算出具体结果）；
- chat：寒暄、确认、学习规划、其他。
一条消息兼具多个诉求时，选学生当前最想要的那个。只输出 JSON，不要解释。"""

_CLASSIFY_USER_TMPL = """学生消息：{text}

输出 JSON。"""

# 启发式快车道：强特征直接判定，省一次 LLM 调用（约 3s）。
# 特征必须「硬」：宁缺勿滥，命中不了就走 LLM——路由错比路由慢更伤。
_MATH_MARKS = (
    "∫", "∑", "求积分", "定积分", "不定积分", "求导", "偏导", "微分方程",
    "求极限", "泰勒展开", "级数求和", "解方程组", "行列式", "特征值", " prove",
)
_ENGI_MARKS = (
    "代码", "报错", "跑一下", "仿真", "调试", "部署", "编译", "python", "matlab",
    "c++", "java", "写个程序", "实现一个", "跑通", " KeyError", " traceback",
)


def _heuristic_intent(text: str) -> str | None:
    t = text.lower()
    if any(m.lower() in t for m in _MATH_MARKS):
        return "math"
    if any(m.lower() in t for m in _ENGI_MARKS):
        return "engineering"
    return None


def classify_intent(user_text: str) -> str:
    """意图分类：启发式强特征优先（零成本），否则极小 GLM 调用。

    失败或输出不合法 → "all"（全量工具，永不阻塞主链路）。
    注意：GLM 5.3 Flash 无法真正关掉 thinking（省略参数也是默认开），
    max_tokens 必须给 reasoning 留出跑完的空间，否则 content 为空。
    """
    text = (user_text or "").strip()
    if not text:  # 纯图片消息没有文本信号，不冒险路由
        return "all"
    heuristic = _heuristic_intent(text)
    if heuristic:
        return heuristic
    try:
        resp = glm_client.chat(
            messages=[
                {"role": "system", "content": _CLASSIFY_SYSTEM},
                {"role": "user", "content": _CLASSIFY_USER_TMPL.format(text=text[:500])},
            ],
            temperature=0.1,
            max_tokens=800,
        )
        raw = (resp.choices[0].message.content or "").strip()
        m = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(m.group(0)) if m else {}
        intent = str(data.get("intent") or "").strip()
        return intent if intent in INTENT_TOOLS else "all"
    except Exception as e:  # noqa: BLE001
        logger.warning("意图分类失败，降级为全量工具: %s", e)
        return "all"


def allowed_tool_names(intent: str) -> list[str] | None:
    """意图 → 本轮应装配的工具名列表；None 表示全量。未知意图也走全量。"""
    if intent not in INTENT_TOOLS:
        return None
    return INTENT_TOOLS[intent]


def routing_note(intent: str, available: list[str]) -> str:
    """注入 extra_system 的一行说明：让模型知道本轮真实可用的工具面。"""
    label = _INTENT_LABELS.get(intent, "通用")
    return (
        f"本轮场景：{label}。已按场景装配工具：{'、'.join(available) if available else '（无）'}；"
        "提示词里提到但不在清单里的工具本轮不可用，遇到超出场景的诉求直接用文本回应或建议换话题。"
    )


def build_tool_context(user_text: str) -> tuple[str | None, str]:
    """/chat 一次调用拿全：返回 (allowed_tools, 注入说明)。

    allowed_tools 供 orchestrator 过滤 tools 字段；说明文本随动态上下文注入。
    路由关闭（分类失败）时 allowed_tools=None + 空说明，等价旧行为。
    """
    intent = classify_intent(user_text)
    allowed = allowed_tool_names(intent)
    if allowed is None:
        return None, ""
    # 只保留真实注册了的（calculator 可能因 sympy 存在而未注册）
    from app.agent.tools import list_tools
    from app.skills.registry import get_skill
    registered = {t.name for t in list_tools()}
    available = [n for n in allowed if n in registered or get_skill(n) is not None]
    return available, routing_note(intent, available)


def build_scene_context(user_text: str) -> tuple[str | None, str]:
    """/chat 场景装配入口（含渐进式披露）：返回 (allowed_tools, 场景注入文本)。

    注入文本 = 装配说明 + 场景工具指南（prompts/guide_*.md，命中场景才加载；
    走尾部动态消息，不进 system——同一场景内指南内容不变，不伤前缀缓存）。
    """
    intent = classify_intent(user_text)
    allowed = allowed_tool_names(intent)
    if allowed is None:
        return None, ""
    from app.agent.system import load_intent_guide
    from app.agent.tools import list_tools
    from app.skills.registry import get_skill
    registered = {t.name for t in list_tools()}
    available = [n for n in allowed if n in registered or get_skill(n) is not None]
    parts = [p for p in (routing_note(intent, available), load_intent_guide(intent)) if p]
    return available, "\n\n".join(parts)


__all__ = [
    "INTENT_TOOLS",
    "classify_intent",
    "allowed_tool_names",
    "routing_note",
    "build_tool_context",
    "build_scene_context",
]
