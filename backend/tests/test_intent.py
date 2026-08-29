"""意图路由（Select 策略）单测：启发式快车道、白名单映射、LLM 分类与降级。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent import intent
from app.agent.intent import (
    INTENT_TOOLS,
    _heuristic_intent,
    allowed_tool_names,
    classify_intent,
    routing_note,
)


def _fake_llm(content: str):
    """构造一个 GLM 响应形状的最小替身。"""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


class TestHeuristicIntent:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("求 f(x)=x^2 在 [0,1] 上的定积分", "math"),
            ("求极限 lim(x→0) sinx/x", "math"),
            ("解一下这个微分方程", "math"),
            ("帮我跑一下这段 python 代码", "engineering"),
            ("这段代码报错了，帮我调试", "engineering"),
            ("写个程序实现快速排序", "engineering"),
        ],
    )
    def test_marks(self, text, expected):
        assert _heuristic_intent(text) == expected

    @pytest.mark.parametrize(
        "text",
        ["什么是操作系统？", "进程和线程有什么区别", "你好", ""],
    )
    def test_no_false_positive(self, text):
        # 弱特征必须走 LLM，启发式不许瞎判
        assert _heuristic_intent(text) is None


class TestAllowedTools:
    def test_math_whitelist(self):
        tools = allowed_tool_names("math")
        assert tools is not None
        assert "calculus" in tools
        assert "code_runner" not in tools

    def test_chat_only_memory(self):
        assert allowed_tool_names("chat") == ["memory_search"]

    def test_unknown_intent_full(self):
        assert allowed_tool_names("nope") is None


class TestRoutingNote:
    def test_lists_tools(self):
        note = routing_note("math", ["calculus", "kg_lookup"])
        assert "calculus" in note and "kg_lookup" in note
        assert "不可用" in note  # 声明清单外工具不可用


class TestClassifyIntent:
    def test_empty_text_falls_back_to_all(self):
        # 纯图片消息没有文本信号，不冒险路由
        assert classify_intent("") == "all"

    def test_heuristic_short_circuits_llm(self, monkeypatch):
        def _boom(*a, **k):
            raise AssertionError("强特征命中时不应再调 LLM")

        monkeypatch.setattr(intent.glm_client, "chat", _boom)
        assert classify_intent("帮我求导数") == "math"

    def test_llm_path(self, monkeypatch):
        monkeypatch.setattr(
            intent.glm_client,
            "chat",
            lambda *a, **k: _fake_llm('{"intent": "concept"}'),
        )
        assert classify_intent("什么是进程") == "concept"

    def test_llm_invalid_output_degrades_to_all(self, monkeypatch):
        monkeypatch.setattr(
            intent.glm_client,
            "chat",
            lambda *a, **k: _fake_llm("我觉得是概念题"),
        )
        assert classify_intent("什么是进程") == "all"

    def test_llm_out_of_enum_degrades_to_all(self, monkeypatch):
        monkeypatch.setattr(
            intent.glm_client,
            "chat",
            lambda *a, **k: _fake_llm('{"intent": "history"}'),
        )
        assert classify_intent("什么是进程") == "all"

    def test_llm_exception_degrades_to_all(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("network down")

        monkeypatch.setattr(intent.glm_client, "chat", _boom)
        assert classify_intent("什么是进程") == "all"


class TestIntentToolsTable:
    def test_memory_search_always_available(self):
        for name, tools in INTENT_TOOLS.items():
            if tools is None:
                continue
            assert "memory_search" in tools, f"{name} 场景缺 memory_search"
