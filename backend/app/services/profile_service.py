"""学习画像回推：从「消息留痕 + 记忆图谱」计算 cognitive_state 写回 learner_profiles。

数据来源（全部是过程性证据，不打分——docs/00 §6 红线）：
- messages.tool_calls 里的 kg_lookup 命中 → 主题曝光；
- messages.gave_answer / scaffold_level → 脚手架结构（AI 直接给答案的比例）；
- 记忆图谱（MCP）里的 概念/误区/偏好 实体 → 学过什么、错过什么；
- 用户消息里的追问信号 → 认知负荷的粗估。

cognitive_state JSON 结构（docs/04 与本文件为真源）：
{
  "topics": { "<概念名>": {"mentions": int, "course": str, "last_seen": iso,
                            "status": "exploring | deepening | to_review",
                            "review": {"interval_days": int, "due": iso} | 无} },
  "misconceptions": ["..."],            # 记忆图谱里的误区实体
  "preferences": ["..."],               # 偏好实体
  "scaffolding": {"gave_answer_ratio": float, "recent_scaffold_level": str | null},
  "cognitive_load": "low | medium | high",
  "review_queue": [{"topic": str, "course": str, "reason": str,
                    "due": iso, "overdue_days": int}],
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

_REVIEW_START_DAYS = 2  # 首次接触 → 2 天后安排回顾
_REVIEW_MAX_INTERVAL = 60  # 间隔封顶（天）
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
    profile_row = learning_repo.get_profile_by_user(db, user_id)
    prev_state: dict[str, Any] = (
        (profile_row.cognitive_state or {}) if profile_row else {}
    )
    prev_topics: dict[str, Any] = prev_state.get("topics") or {}

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

    # ── 并入 mastery_evidence 工具留痕的过程性证据标签 ──────────
    # 只做计数合并（evidence: {type: count}），不改判状态/不打分——
    # 状态仍由 _schedule_review 的时间轴逻辑决定，证据是给人看和给 AI 查的。
    for etype, etopic, cnt in _evidence_counts(db, user_id):
        t = topics.setdefault(etopic, {"mentions": 0, "course": "general",
                                       "last_seen": None, "_last_dt": None})
        ev = t.setdefault("evidence", {})
        ev[etype] = ev.get(etype, 0) + cnt

    if not topics:
        return None

    # ── 主题状态 + 复习队列（SM-2-lite：间隔随重新提起翻倍，到期进队列）──
    review_queue: list[dict[str, Any]] = []
    for name, t in topics.items():
        last_dt = t.pop("_last_dt", None)
        t["last_seen"] = last_dt.isoformat() if last_dt else None
        status, review, queue_entry = _schedule_review(
            prev_topics.get(name), last_dt, now, t["mentions"]
        )
        t["status"] = status
        if review:
            t["review"] = review
        if queue_entry:
            queue_entry["course"] = t["course"]
            review_queue.append(queue_entry)
    review_queue.sort(key=lambda e: -e.get("overdue_days", 0))
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


def _evidence_counts(db: Session, user_id: str) -> list[tuple[str, str, int]]:
    """mastery_evidence 工具留痕的 (evidence_type, topic, count) 聚合。

    独立兜异常：证据表不存在 / 查询失败时返回空，不影响画像主流程。
    """
    try:
        from sqlalchemy import func

        from app.models.evidence import LearningEvidence
        rows = (
            db.query(
                LearningEvidence.evidence_type,
                LearningEvidence.topic,
                func.count(LearningEvidence.id),
            )
            .filter(LearningEvidence.user_id == user_id)
            .group_by(LearningEvidence.evidence_type, LearningEvidence.topic)
            .all()
        )
        return [(str(a), str(b), int(c)) for a, b, c in rows]
    except Exception:  # noqa: BLE001
        logger.warning("learning_evidence 聚合失败（跳过）", exc_info=True)
        return []


def _schedule_review(
    prev: dict[str, Any] | None,
    last_dt: datetime | None,
    now: datetime,
    mentions: int,
) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
    """间隔复习调度（SM-2-lite，纯函数、可单测、不打分）。

    - 首次接触：2 天后安排回顾；之后每重新提起一次，间隔翻倍（封顶 60 天）
      ——「记得越牢，下次回顾隔得越久」的检索练习节奏；
    - 上一轮排期已到期还没再碰 → to_review，进复习队列（按超期天数排序消费）；
    - 只有 kg_lookup 命中才带时间（last_dt），纯图谱实体没有时间轴，沿用旧状态不排期。

    返回 (status, topics[name].review, review_queue 条目或 None)。
    """
    if last_dt is None:
        prev_review = (prev or {}).get("review")
        return (prev or {}).get("status") or "exploring", prev_review, None

    last_dt = last_dt.replace(tzinfo=timezone.utc)
    prev_review = (prev or {}).get("review") or {}
    try:
        interval = int(prev_review.get("interval_days") or 0)
    except (TypeError, ValueError):
        interval = 0
    prev_last = (prev or {}).get("last_seen")
    try:
        prev_last_dt = datetime.fromisoformat(prev_last) if prev_last else None
    except (TypeError, ValueError):
        prev_last_dt = None
    if prev_last_dt is None:
        re_engaged = True  # 无历史时间视为首见
    else:
        re_engaged = last_dt > prev_last_dt.replace(tzinfo=timezone.utc)

    if re_engaged:
        interval = min(interval * 2 if interval else _REVIEW_START_DAYS,
                       _REVIEW_MAX_INTERVAL)
    if interval <= 0:
        interval = _REVIEW_START_DAYS

    due = last_dt + timedelta(days=interval)
    review = {"interval_days": interval, "due": due.isoformat()}
    days_since = (now - last_dt).days

    if now >= due:
        overdue_days = max(0, days_since - interval)
        entry = {
            "topic": "",
            "reason": f"已经 {days_since} 天没碰了，安排一次回顾吧",
            "due": due.isoformat(),
            "overdue_days": overdue_days,
        }
        status = "to_review"
    else:
        status = "exploring" if mentions <= 1 else "deepening"
        entry = None
    return status, review, entry


def build_learning_context(state: dict[str, Any] | None, course_id: str = "general") -> dict[str, Any]:
    """把 cognitive_state 折算成前端 LearningContext（docs/02 §updated_context 契约）。

    注意措辞红线（docs/00 §6）：`mastery` 字段名是前端既有契约，语义是
    「过程性探索深度估计值」（同 /exploration 的 depth），不是考试分数。
    """
    course_name = _course_name(course_id)
    if not state:
        return {
            "course": {"id": course_id, "name": course_name, "subject": course_name, "goal": ""},
            "path": [],
            "mastery": {},
            "review_due": [],
            "cognitive": {},
        }

    topics: dict[str, Any] = state.get("topics") or {}
    ranked = sorted(
        topics.items(), key=lambda kv: -(kv[1].get("mentions") or 0)
    )[:10]

    path: list[dict[str, Any]] = []
    mastery: dict[str, float] = {}
    for name, t in ranked:
        depth = round(min(1.0, 0.3 + (t.get("mentions") or 0) * 0.15), 2)
        path.append({
            "node": name,
            "status": t.get("status") or "exploring",
            "mastery": depth,
            "reason": f"聊过 {t.get('mentions') or 0} 次",
        })
        mastery[name] = depth

    review_due = [
        {
            "kc": e.get("topic") or "",
            "due": e.get("due") or "",
            "course": e.get("course") or "general",
            "reason": e.get("reason") or "",
            "overdue_days": e.get("overdue_days") or 0,
        }
        for e in (state.get("review_queue") or [])
        if e.get("topic")
    ]

    cognitive: dict[str, Any] = {}
    if state.get("cognitive_load"):
        cognitive["load"] = state["cognitive_load"]

    return {
        "course": {"id": course_id, "name": course_name, "subject": course_name, "goal": ""},
        "path": path,
        "mastery": mastery,
        "review_due": review_due,
        "cognitive": cognitive,
    }


def _course_name(course_id: str) -> str:
    """course_id → 中文名（KG 注册表里有就用它，没有退回原值）。"""
    try:
        from app.kg import COURSES
        cls = COURSES.get(course_id)
        if cls is not None:
            return getattr(cls, "course_name", None) or course_id
    except Exception:  # noqa: BLE001
        pass
    return "综合学习" if course_id == "general" else course_id


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
