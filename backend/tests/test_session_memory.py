"""会话级记忆（Write 策略）单测：Scratchpad 启发式与注入渲染（纯逻辑，不碰 DB/LLM）。"""
from __future__ import annotations

from types import SimpleNamespace

from app.services.session_memory import (
    _pending_text,
    _progress_text,
    _scratchpad_from_messages,
    render_session_context,
)


def _msg(role: str, card_type: str = "text", text: str = "", payload: dict | None = None):
    return SimpleNamespace(role=role, card_type=card_type, text=text, payload=payload)


class TestScratchpad:
    def test_no_assistant_message_returns_none(self):
        msgs = [_msg("user", text="什么是进程？")]
        assert _scratchpad_from_messages(msgs) is None

    def test_topic_from_last_user_message(self):
        msgs = [
            _msg("user", text="什么是进程？"),
            _msg("assistant", card_type="understand", text="进程是资源分配的基本单位"),
        ]
        pad = _scratchpad_from_messages(msgs)
        assert pad["topic"] == "什么是进程？"
        assert "概念讲解" in pad["progress"]

    def test_question_card_sets_pending(self):
        msgs = [
            _msg("user", text="讲讲线程"),
            _msg(
                "assistant",
                card_type="question",
                text="",
                payload={"stem": "你觉得两个线程共享哪些资源？"},
            ),
        ]
        pad = _scratchpad_from_messages(msgs)
        assert "等学生回应反问" in pad["pending"]
        assert "共享哪些资源" in pad["pending"]

    def test_practice_card_sets_pending(self):
        msgs = [
            _msg("user", text="来个预测题"),
            _msg(
                "assistant",
                card_type="practice",
                text="",
                payload={"stem": "如果把条件去掉会怎样？"},
            ),
        ]
        pad = _scratchpad_from_messages(msgs)
        assert "预测→验证" in pad["pending"]

    def test_math_progress_counts_steps(self):
        msgs = [
            _msg("user", text="算 ∫x²dx"),
            _msg(
                "assistant",
                card_type="math",
                text="",
                payload={"math": {"problem": "∫x²dx", "steps": [
                    {"expr": "x^3/3", "note": "幂函数积分"},
                    {"expr": "+C", "note": "加常数"},
                ]}},
            ),
        ]
        pad = _scratchpad_from_messages(msgs)
        assert "第 2 步" in pad["progress"]

    def test_engineering_pending(self):
        msgs = [
            _msg("user", text="写个爬虫"),
            _msg("assistant", card_type="engineering", text="步骤如下"),
        ]
        pad = _scratchpad_from_messages(msgs)
        assert pad["pending"] is not None
        assert "贴出代码" in pad["pending"] or "动手" in pad["pending"]

    def test_plain_statement_no_pending(self):
        msgs = [
            _msg("user", text="你好"),
            _msg("assistant", text="你好呀，今天想学点什么"),
        ]
        pad = _scratchpad_from_messages(msgs)
        assert "pending" not in pad


class TestPendingText:
    def test_text_ending_with_question(self):
        m = _msg("assistant", text="先讲到这里。你还想到什么场景？")
        assert _pending_text(m) is not None

    def test_statement_returns_none(self):
        assert _pending_text(_msg("assistant", text="这就是全部内容")) is None


class TestProgressText:
    def test_feedback_card(self):
        assert _progress_text(_msg("assistant", card_type="feedback")) == "已给审阅式反馈"

    def test_fallback_uses_text_head(self):
        out = _progress_text(_msg("assistant", text="很长的一段话" + "x" * 100))
        assert out.startswith("最近输出：")


class TestRenderSessionContext:
    def test_empty_returns_empty(self):
        assert render_session_context(None, None) == ""

    def test_renders_summary_and_pad(self):
        out = render_session_context(
            "学生在学傅里叶变换",
            {"topic": "傅里叶变换", "progress": "已做概念讲解", "pending": "等学生回应反问：…"},
        )
        assert "早期进展摘要" in out
        assert "傅里叶变换" in out
        assert "待学生回应" in out

    def test_pad_without_pending_omits_line(self):
        out = render_session_context(None, {"topic": "爬虫", "progress": "已讲"})
        assert "待学生回应" not in out
