"""学习上下文与探索档案：从既有数据聚合，刻意不建新表。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.core.response import ok
from app.core.security import get_current_user
from app.models import User
from app.models.conversation import Message
from app.repositories import chat_repo

router = APIRouter()

# 深度提问的信号：学生从「怎么算」走向「为什么」，是理解深化的标志
_DEEP_Q_MARKS = (
    "为什么", "为何", "怎么会", "怎么判断", "如何判断",
    "如果", "假如", "是不是", "会不会", "能不能", "能否", "难道",
)
# AI 抛出过、学生可能还没接住的引申疑问
_OPEN_THREAD_MARKS = (
    "你有没有想过", "有没有想过", "你想过", "不妨想想", "不妨思考",
    "试想", "你会怎么", "留给你", "可以想想",
)


@router.get("/context")
def learning_context(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    """学习上下文（前端 LearningContext）：路径 / 探索深度 / 复习到期。

    与 ChatOut.updated_context 同一份数据形状（build_learning_context）：
    - 进入主界面时拉一次（顶栏进度、侧栏「我的路径」「该回顾了」的首次数据源）；
    - 练习提交等不带回推的入口之后，前端可再调它刷新。
    内部会顺带重算 cognitive_state（无消息记录时返回空上下文，不报错）。
    """
    from app.services import profile_service
    state = profile_service.update_cognitive_state(db, user.id)
    return ok(profile_service.build_learning_context(state, "general"))


@router.get("/exploration")
def get_exploration(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
    limit: int = 12,
) -> dict:
    """探索档案：把「提过的好问题 / 未展开的线索 / 探索过的主题」从历史消息里读出来。

    **刻意不建新表** —— 这些都是对话里已经存在的信号，聚合即可。
    依据 docs/00 §6：这里的「探索深度」来自**过程性证据**（提问深度、提及次数），
    **不是判分结果**；本产品不给学生打分。
    """
    limit = max(1, min(int(limit or 12), 50))

    convs = chat_repo.list_conversations(db, user.id)
    if not convs:
        return ok(
            {
                "good_questions": [],
                "open_threads": [],
                "topics": [],
                "works": [],
                "stats": {"conversations": 0, "good_question_count": 0, "topic_count": 0},
            }
        )

    conv_ids = [c.id for c in convs]
    course_by_conv = {c.id: c.course_id for c in convs}

    # 一次查完，避免逐会话 N+1
    msgs = (
        db.query(Message)
        .filter(Message.conversation_id.in_(conv_ids))
        .order_by(Message.created_at.desc())
        .limit(1000)
        .all()
    )

    good_questions: list[dict[str, Any]] = []
    open_threads: list[dict[str, Any]] = []
    topics: dict[str, dict[str, Any]] = {}

    for m in msgs:
        text = (m.text or "").strip()
        created = m.created_at.isoformat() if m.created_at else None

        if text and m.role == "user":
            # 深度提问：含追问信号 + 长度不过短（排除「嗯」「好的」这类应答）
            if len(text) >= 8 and any(k in text for k in _DEEP_Q_MARKS):
                good_questions.append(
                    {
                        "text": text[:160],
                        "conversation_id": m.conversation_id,
                        "created_at": created,
                    }
                )
        elif text and m.role == "assistant":
            if any(k in text for k in _OPEN_THREAD_MARKS):
                open_threads.append(
                    {
                        "text": text[:160],
                        "conversation_id": m.conversation_id,
                        "created_at": created,
                    }
                )

        # 主题：优先取 kg_lookup 真正命中过的概念（有知识底座支撑，比瞎切关键词靠谱）
        for tc in m.tool_calls or []:
            if not isinstance(tc, dict) or tc.get("tool_name") != "kg_lookup":
                continue
            args = tc.get("args") or {}
            query = str(args.get("query", "")).strip()
            if not query:
                continue
            item = topics.setdefault(
                query,
                {
                    "topic": query,
                    "mentions": 0,
                    "course": course_by_conv.get(m.conversation_id, "general"),
                },
            )
            item["mentions"] += 1

    topic_list = sorted(topics.values(), key=lambda x: -x["mentions"])[:limit]
    for t in topic_list:
        # 探索深度：聊得越多次、越深入 → 越接近 1。
        # 这是「过程性证据的估计值」，不是考试分数。
        t["depth"] = round(min(1.0, 0.3 + t["mentions"] * 0.15), 2)

    return ok(
        {
            "good_questions": good_questions[:limit],
            "open_threads": open_threads[:limit],
            "topics": topic_list,
            "works": [],  # 作品：需学生主动提交，待 StudentWork 表（见 docs/01 §8）
            "stats": {
                "conversations": len(convs),
                "good_question_count": len(good_questions),
                "topic_count": len(topics),
            },
        }
    )
