"""学习画像回推：从「消息留痕 + 记忆图谱」计算 cognitive_state 写回 learner_profiles。

数据来源（全部是过程性证据，不打分——docs/00 §6 红线）：
- messages.tool_calls 里的 kg_lookup 命中 → 主题曝光；
- messages.gave_answer / scaffold_level → 脚手架结构（AI 直接给答案的比例）；
- 记忆图谱（MCP）里的 概念/误区/偏好 实体 → 学过什么、错过什么；
- 用户消息里的追问信号 → 认知负荷的粗估。

cognitive_state JSON 结构（docs/04 与本文件为真源）：
{
  "topics": { "<概念名>": {"mentions": int, "course": str, "last_seen": iso,
                            "status": "exploring | deepening | to_review"} },
  "misconceptions": ["..."],            # 记忆图谱里的误区实体
  "preferences": ["..."],               # 偏好实体
  "scaffolding": {"gave_answer_ratio": float, "recent_scaffold_level": str | null},
  "cognitive_load": "low | medium | high",
  "review_queue": [{"topic": str, "reason": str}],
  "updated_at": iso
}
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models import LearnerProfile
from app.models.conversation import Conversation, Message
from app.repositories import learning_repo

logger = logging.getLogger(__name__)

_REVIEW_AFTER_DAYS = 7
_RECENT_FOR_SCAFFOLD = 20

# 追问信号（与 student.py 探索档案同源的简化版）：衡量「正在较劲」的程度
_LOAD_MARKS = ("为什么", "怎么", "如何", "如果", "不懂", "还是", "没明白", "?", "？")


def update_cognitive_state(db: Session, user_id: str) -> dict[str, Any] | None:
    """重算并写回某用户的 cognitive_state。后台调用，失败只记日志。"""
    try:
        state = compute_cognitive_state(db, user_id)
        if state is None:
            return None
        _upsert(db, user_id, state)
        return state
    except Exception:  # noqa: BLE001
        logger.exception("cognitive_state 回推失败（不影响主链路）")
        return None


def compute_cognitive_state(db: Session, user_id: str) -> dict[str, Any] | None:
    rows = (
        db.query(Message, Conversation.course_id)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .filter(Conversation.user_id == user_id)
        .order_by(Message.created_at.desc())
        .limit(400)
        .all()
    )
    if not rows:
        return None
    rows.reverse()  # 时间正序

    now = datetime.now(timezone.utc)

    # ── 主题：kg_lookup 命中 ──────────────────────────────
    topics: dict[str, dict[str, Any]] = {}
    scaffold_recent: list[Message] = []
    user_recent: list[Message] = []

    for m, course in rows:
        if m.role == "assistant":
            scaffold_recent.append(m)
        elif m.role == "user":
            user_recent.append(m)
        for tc in m.tool_calls or []:
            if not (isinstance(tc, dict) and tc.get("tool_name") == "kg_lookup"):
                continue
            query = str((tc.get("args") or {}).get("query", "")).strip()
            if not query:
                continue
            t = topics.setdefault(
                query, {"mentions": 0, "course": course or "general",
                        "last_seen": None, "_last_dt": None}
            )
            t["mentions"] += 1
            if m.created_at and (t["_last_dt"] is None or m.created_at > t["_last_dt"]):
                t["_last_dt"] = m.created_at

    # ── 并入记忆图谱的概念实体（交流次数 = observations 条数）────
    graph_topics = _graph_topics(user_id)
    for name, info in graph_topics.items():
        t = topics.setdefault(name, {"mentions": 0, "course": info.get("course") or "general",
                                     "last_seen": None, "_last_dt": None})
        t["mentions"] = max(t["mentions"], info["mentions"])

    if not topics:
        return None

    # ── 主题状态 + 复习队列 ───────────────────────────────
    review_queue: list[dict[str, str]] = []
    for name, t in topics.items():
        last_dt = t.pop("_last_dt", None)
        t["last_seen"] = last_dt.isoformat() if last_dt else None
        if last_dt is None or (now - last_dt.replace(tzinfo=timezone.utc)).days >= _REVIEW_AFTER_DAYS:
            t["status"] = "to_review"
            if t["mentions"] >= 1:
                review_queue.append({"topic": name, "reason": "超过一周没碰过了"})
        elif t["mentions"] <= 1:
            t["status"] = "exploring"
        else:
            t["status"] = "deepening"
    review_queue = review_queue[:8]

    # ── 脚手架结构：AI 直接给答案的比例（越低越符合产品定位）────
    ga_rows = [m for m in scaffold_recent if m.gave_answer is not None][-_RECENT_FOR_SCAFFOLD:]
    gave_ratio = (
        round(sum(1 for m in ga_rows if m.gave_answer) / len(ga_rows), 2) if ga_rows else None
    )
    scaffold_levels = [m.scaffold_level for m in scaffold_recent if m.scaffold_level]
    recent_scaffold = scaffold_levels[-1] if scaffold_levels else None

    # ── 认知负荷（粗估）：最近 10 条用户消息的追问密度 ──────
    load = None
    recent_user = user_recent[-10:]
    if recent_user:
        hits = sum(1 for m in recent_user if any(k in (m.text or "") for k in _LOAD_MARKS))
        load = "low" if hits < 3 else "medium" if hits < 6 else "high"

    # ── 练习闭环：practice/choice 卡的作答提交（过程性证据，不打分）──
    submissions = 0
    last_submit: datetime | None = None
    for m, _course in rows:
        if m.role != "user" or not isinstance(m.payload, dict):
            continue
        ref = (m.payload.get("meta") or {}).get("practice_ref")
        if not ref:
            continue
        submissions += 1
        if m.created_at and (last_submit is None or m.created_at > last_submit):
            last_submit = m.created_at

    misconceptions, preferences = _graph_traits(user_id)

    return {
        "topics": topics,
        "misconceptions": misconceptions,
        "preferences": preferences,
        "scaffolding": {
            "gave_answer_ratio": gave_ratio,
            "recent_scaffold_level": recent_scaffold,
        },
        "cognitive_load": load,
        "review_queue": review_queue,
        # 练习闭环：练习/选择卡的主动作答次数（自我验证意愿的证据）
        "practice": {
            "submissions": submissions,
            "last_submit_at": last_submit.isoformat() if last_submit else None,
        },
        "updated_at": now.isoformat(),
    }


# ── 内部 ─────────────────────────────────────────────────


def _graph_topics(user_id: str) -> dict[str, dict[str, Any]]:
    """记忆图谱里的「概念」实体 → {name: {mentions, course}}。图谱不可用就跳过。"""
    try:
        from app.mcp import bridge as _b
        if not _b.available():
            return {}
        g = _b.read_graph(user_id)
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, dict[str, Any]] = {}
    for e in g.get("entities", []):
        if (e.get("entityType") or "") == "概念":
            out[e["name"]] = {"mentions": len(e.get("observations") or [])}
    return out


def _graph_traits(user_id: str) -> tuple[list[str], list[str]]:
    """记忆图谱里的「误区」「偏好」实体名。"""
    try:
        from app.mcp import bridge as _b
        if not _b.available():
            return [], []
        g = _b.read_graph(user_id)
    except Exception:  # noqa: BLE001
        return [], []
    mis = [e["name"] for e in g.get("entities", []) if e.get("entityType") == "误区"][:10]
    pref = [e["name"] for e in g.get("entities", []) if e.get("entityType") == "偏好"][:6]
    return mis, pref


def _upsert(db: Session, user_id: str, state: dict[str, Any]) -> None:
    profile = learning_repo.get_profile_by_user(db, user_id)
    if profile is None:
        profile = LearnerProfile(user_id=user_id)
        db.add(profile)
    profile.cognitive_state = state
    # 置信度：证据越多越可信（会话里的主题数 + 有图谱实体加分），封顶 0.95
    evidence = len(state.get("topics", {})) + 2 * len(state.get("misconceptions", []))
    profile.profile_confidence = round(min(0.95, 0.1 + 0.08 * min(evidence, 10)), 2)
    db.commit()
