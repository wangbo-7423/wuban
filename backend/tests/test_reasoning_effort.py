"""推理深度档位（reasoning_effort）与工具循环收敛的回归测试。

背景：glm-5.3 / glm-5.3-flash 是「强制思考」模型——thinking 关不掉
（传 disabled 会报错），且思考 token 与正文共用 max_tokens、优先消耗，
导致首字延迟高、正文被吃空。唯一能压缩思考量的开关是 reasoning_effort
（low / high / max），不传时平台默认 max。

另一半是循环收敛：触顶轮若仍装配工具，模型可能一直调工具、正文始终为空。
"""
from __future__ import annotations

from types import SimpleNamespace

import app.agent.glm_client as gc
from app.agent.orchestrator import AgentOrchestrator


def _patch_client(monkeypatch, captured: list[dict]):
    """拦截真实调用，把每次的 kwargs 记进 captured 并返回空响应。"""

    class _FakeCompletions:
        @staticmethod
        def create(**kwargs):
            captured.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                model_dump=lambda: {"role": "assistant", "content": "ok"},
            ))])

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions()))
    monkeypatch.setattr(gc, "get_client", lambda: fake_client)


class TestReasoningEffortPassthrough:
    def test_default_comes_from_settings(self, monkeypatch):
        captured: list[dict] = []
        _patch_client(monkeypatch, captured)
        monkeypatch.setattr(gc.settings, "glm_reasoning_effort", "low")
        gc.chat(messages=[{"role": "user", "content": "hi"}])
        assert captured[0]["reasoning_effort"] == "low"

    def test_explicit_arg_overrides_settings(self, monkeypatch):
        captured: list[dict] = []
        _patch_client(monkeypatch, captured)
        monkeypatch.setattr(gc.settings, "glm_reasoning_effort", "low")
        gc.chat(
            messages=[{"role": "user", "content": "hi"}],
            reasoning_effort="high",
        )
        assert captured[0]["reasoning_effort"] == "high"

    def test_empty_string_omits_param(self, monkeypatch):
        """旧模型不支持该字段时，传 '' 显式不下发。"""
        captured: list[dict] = []
        _patch_client(monkeypatch, captured)
        gc.chat(messages=[{"role": "user", "content": "hi"}], reasoning_effort="")
        assert "reasoning_effort" not in captured[0]


def _resp(content: str = "", reasoning: str = "", tool_calls=None):
    def _dump():
        d = {"role": "assistant", "content": content, "reasoning_content": reasoning}
        if tool_calls:
            d["tool_calls"] = tool_calls
        return d

    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(model_dump=_dump))],
        usage=None,
    )


def _one_tool_call():
    return [{
        "id": "c1",
        "type": "function",
        "function": {"name": "kg_lookup", "arguments": '{"term": "x"}'},
    }]


class TestToolLoopConvergence:
    """触顶轮不再装配工具 + 正文为空时用思维链兜底。"""

    def _patch_agent(self, monkeypatch, responses):
        calls: list[dict] = []

        def fake_chat(**kwargs):
            calls.append(kwargs)
            return responses[min(len(calls) - 1, len(responses) - 1)]

        monkeypatch.setattr("app.agent.orchestrator.glm_client.chat", fake_chat)
        monkeypatch.setattr(
            "app.agent.orchestrator.tool_schemas",
            lambda allowed=None: [{"type": "function", "function": {"name": "kg_lookup"}}],
        )
        monkeypatch.setattr(
            "app.agent.orchestrator.execute_tool",
            lambda name, args: {"ok": True, "value": "查到了"},
        )
        return calls

    def test_final_round_drops_tools(self, monkeypatch):
        """前 3 轮一直调工具 → 第 4 轮必须收走工具，逼出正文。"""
        responses = [
            _resp(tool_calls=_one_tool_call()),
            _resp(tool_calls=_one_tool_call()),
            _resp(tool_calls=_one_tool_call()),
            _resp(content="这是最终回答"),
        ]
        calls = self._patch_agent(monkeypatch, responses)
        result = AgentOrchestrator(allowed_tools=["kg_lookup"]).run(
            history=[], user_text="问个问题"
        )
        assert len(calls) == 4
        assert "tools" in calls[0] and "tools" in calls[1] and "tools" in calls[2]
        assert "tools" not in calls[3], "触顶轮仍带着工具，可能永远收敛不了"
        assert result.text == "这是最终回答"

    def test_empty_content_falls_back_to_thinking(self, monkeypatch):
        """思考 token 吃满 max_tokens 导致 content 为空 → 用思维链草稿兜底。"""
        self._patch_agent(monkeypatch, [_resp(content="", reasoning="草稿正文")])
        result = AgentOrchestrator(allowed_tools=["kg_lookup"]).run(
            history=[], user_text="问个问题"
        )
        assert result.text == "草稿正文"
