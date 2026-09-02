"""意图路由（Select 策略）单测：启发式快车道、场景声明推导、LLM 分类与降级。

白名单不再集中维护——每个 ToolSpec / SkillSpec 用 scenes 字段就地声明，
本文件用受控替身测推导逻辑（不依赖 MCP 记忆桥等运行时条件的注册与否），
另有一条真实注册表的元数据完整性回归。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent import intent
from app.agent.intent import (
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


def _patch_registry(monkeypatch, tools=(), skills=()):
    """把注册表替身成给定 spec 列表（鸭子类型：只需 name/scenes）。"""
    monkeypatch.setattr("app.agent.tools.list_tools", lambda: tuple(tools))
    monkeypatch.setattr("app.skills.registry.list_skills", lambda: tuple(skills))


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
    def test_math_assembly(self):
        """真实注册表：math 场景应装配 calculus skill，且不含工程专属工具。"""
        tools = allowed_tool_names("math")
        assert tools is not None
        assert "calculus" in tools
        assert "code_runner" not in tools
        assert "project_guide" not in tools

    def test_chat_only_memory(self, monkeypatch):
        """chat 场景只有声明了全场景（"*"）的工具。"""
        _patch_registry(
            monkeypatch,
            tools=[
                SimpleNamespace(name="memory_search", scenes=("*",)),
                SimpleNamespace(name="code_runner", scenes=("engineering",)),
                SimpleNamespace(name="kg_lookup", scenes=("math", "engineering", "concept")),
            ],
        )
        assert allowed_tool_names("chat") == ["memory_search"]

    def test_unknown_intent_full(self):
        assert allowed_tool_names("nope") is None

    def test_all_intent_full(self):
        assert allowed_tool_names("all") is None

    def test_empty_assembly_falls_back_to_full(self, monkeypatch):
        """场景里没有任何工具时兜底全量，路由永远不能把主链路搞挂。"""
        _patch_registry(monkeypatch, tools=[SimpleNamespace(name="odd", scenes=())])
        assert allowed_tool_names("math") is None


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


class TestSceneDeclarations:
    def test_star_scenes_in_every_intent(self, monkeypatch):
        """声明全场景的工具应出现在每个意图的装配名单里。"""
        _patch_registry(
            monkeypatch,
            tools=[
                SimpleNamespace(name="memory_search", scenes=("*",)),
                SimpleNamespace(name="web_search", scenes=("concept",)),
            ],
        )
        for intent_name in ("math", "engineering", "concept", "chat"):
            assert "memory_search" in allowed_tool_names(intent_name), (
                f"{intent_name} 场景缺全场景工具"
            )
        assert allowed_tool_names("concept") == ["memory_search", "web_search"]

    def test_registered_specs_declare_scenes(self):
        """真实注册表回归：任何已注册工具/skill 必须声明 scenes。

        这是对「声明式注册」的约束——新增工具忘写元数据时在这里爆，
        而不是在线上表现为「路由后凭空消失」。
        """
        from app.agent.tools import list_tools
        from app.skills.registry import list_skills

        for t in list_tools():
            assert getattr(t, "scenes", ()), f"工具 {t.name} 未声明 scenes"
        for s in list_skills():
            assert s.scenes, f"skill {s.name} 未声明 scenes"
            if s.guide:
                assert s.guide.startswith("guide_") and s.guide.endswith(".md")
