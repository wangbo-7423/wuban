"""profile_service 纯逻辑单测：SM-2-lite 间隔复习调度 + LearningContext 折算。

不连库、不连 GLM——只测 _schedule_review（调度纯函数）与
build_learning_context（updated_context 契约折算）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.profile_service import _schedule_review, build_learning_context

NOW = datetime(2026, 8, 30, 12, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


class TestScheduleReview:
    def test_first_mention_schedules_two_days_later(self):
        last = NOW - timedelta(days=1)
        status, review, entry = _schedule_review(None, last, NOW, mentions=1)
        # 首次接触：2 天后回顾；还没到期 → exploring，不进队列
        assert status == "exploring"
        assert review == {"interval_days": 2,
                          "due": (last + timedelta(days=2)).isoformat()}
        assert entry is None

    def test_overdue_goes_to_review_queue(self):
        last = NOW - timedelta(days=10)
        status, review, entry = _schedule_review(None, last, NOW, mentions=3)
        assert status == "to_review"
        assert review is not None and review["interval_days"] == 2
        assert entry is not None
        assert entry["overdue_days"] == 8  # 10 天没碰 − 2 天间隔
        assert "10 天没碰" in entry["reason"]
        assert entry["topic"] == ""  # topic 由调用方回填

    def test_re_engagement_doubles_interval(self):
        prev_last = NOW - timedelta(days=9)
        prev = {
            "last_seen": _iso(prev_last),
            "review": {"interval_days": 4, "due": _iso(prev_last + timedelta(days=4))},
        }
        last = NOW - timedelta(days=1)
        status, review, _ = _schedule_review(prev, last, NOW, mentions=3)
        # 重新提起 → 间隔 4 → 8；1 天前刚聊过，未到期
        assert status == "deepening"
        assert review["interval_days"] == 8
        assert review["due"] == (last + timedelta(days=8)).isoformat()

    def test_interval_caps_at_60(self):
        prev_last = NOW - timedelta(days=61)
        prev = {
            "last_seen": _iso(prev_last),
            "review": {"interval_days": 60, "due": _iso(prev_last + timedelta(days=60))},
        }
        status, review, _ = _schedule_review(prev, NOW - timedelta(days=1), NOW, mentions=5)
        assert review["interval_days"] == 60
        assert status == "deepening"

    def test_same_last_seen_keeps_interval(self):
        prev_last = NOW - timedelta(days=1)
        prev = {
            "last_seen": _iso(prev_last),
            "review": {"interval_days": 4, "due": _iso(prev_last + timedelta(days=4))},
        }
        _, review, entry = _schedule_review(prev, prev_last, NOW, mentions=2)
        # last_seen 没前移（同一条消息重算）→ 间隔不翻倍
        assert review["interval_days"] == 4
        assert entry is None

    def test_graph_topic_without_time_keeps_prev_status(self):
        prev = {"status": "deepening", "last_seen": None,
                "review": {"interval_days": 8, "due": _iso(NOW)}}
        status, review, entry = _schedule_review(prev, None, NOW, mentions=4)
        # 纯图谱实体没有时间轴：沿用旧状态与旧排期，不排新队
        assert status == "deepening"
        assert review == prev["review"]
        assert entry is None

    def test_graph_topic_without_time_nor_prev(self):
        status, review, entry = _schedule_review(None, None, NOW, mentions=1)
        assert status == "exploring"
        assert review is None
        assert entry is None


class TestBuildLearningContext:
    def test_topics_folded_into_path_and_mastery(self):
        state = {
            "topics": {
                "傅里叶变换": {"mentions": 5, "course": "signals", "status": "deepening",
                              "last_seen": _iso(NOW)},
                "进程调度": {"mentions": 2, "course": "os", "status": "exploring",
                            "last_seen": _iso(NOW)},
            },
            "review_queue": [
                {"topic": "进程调度", "course": "os", "reason": "已经 9 天没碰了",
                 "due": _iso(NOW - timedelta(days=1)), "overdue_days": 7},
                {"topic": "", "reason": "残缺条目", "due": "", "overdue_days": 3},
            ],
            "cognitive_load": "medium",
        }
        ctx = build_learning_context(state, "signals")
        # path 按 mentions 降序；mastery 是探索深度估计值（0.3 + n*0.15 封顶 1.0）
        assert [p["node"] for p in ctx["path"]] == ["傅里叶变换", "进程调度"]
        assert ctx["mastery"] == {"傅里叶变换": 1.0, "进程调度": 0.6}
        assert ctx["path"][0]["status"] == "deepening"
        assert "聊过 5 次" in ctx["path"][0]["reason"]
        # review_queue：无 topic 的残缺条目被过滤
        assert len(ctx["review_due"]) == 1
        item = ctx["review_due"][0]
        assert item["kc"] == "进程调度"
        assert item["course"] == "os"
        assert item["overdue_days"] == 7
        assert ctx["cognitive"] == {"load": "medium"}

    def test_empty_state_returns_empty_context(self):
        ctx = build_learning_context(None, "os")
        assert ctx["path"] == [] and ctx["mastery"] == {} and ctx["review_due"] == []
        assert ctx["course"]["id"] == "os"

    def test_course_name_resolved_from_kg_registry(self):
        from app.kg import COURSES
        ctx = build_learning_context(None, "os")
        assert ctx["course"]["name"] == COURSES["os"].course_name
        # 未注册课程退回原值
        assert build_learning_context(None, "whatever")["course"]["name"] == "whatever"
