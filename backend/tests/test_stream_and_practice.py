"""流式编排与练习闭环的纯逻辑单测（不连 GLM / 不连 DB）。"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.agent.orchestrator import AgentOrchestrator, _accumulate_tool_deltas
from app.api.student import _options_block, _sse


def _chunk(content=None, reasoning=None, tool_calls=None, usage=None):
    """构造 GLM 流式 chunk 的最小替身（iter_stream_chunks 兼容的形状）。"""
    delta = SimpleNamespace(
        content=content, reasoning_content=reasoning, tool_calls=tool_calls
    )
    return SimpleNamespace(usage=usage, choices=[SimpleNamespace(delta=delta)])


class TestAccumulateToolDeltas:
    def test_merges_argument_shards(self):
        acc: dict = {}
        _accumulate_tool_deltas(acc, [
            {"index": 0, "id": "call_1", "function": {"name": "calculator", "arguments": "{\"expr"}},
        ])
        _accumulate_tool_deltas(acc, [
            {"index": 0, "function": {"arguments": "\": \"1+1\"}"}},
        ])
        assert acc[0] == {
            "id": "call_1",
            "type": "function",
            "function": {"name": "calculator", "arguments": '{"expr": "1+1"}'},
        }

    def test_multiple_calls_by_index(self):
        acc: dict = {}
        _accumulate_tool_deltas(acc, [
            {"index": 0, "id": "a", "function": {"name": "kg_lookup", "arguments": "{}"}},
            {"index": 1, "id": "b", "function": {"name": "calculator", "arguments": "{}"}},
        ])
        assert {acc[i]["function"]["name"] for i in (0, 1)} == {"kg_lookup", "calculator"}

    def test_missing_index_takes_next_slot(self):
        acc: dict = {}
        _accumulate_tool_deltas(acc, [
            {"id": "x", "function": {"name": "calculator", "arguments": "{}"}},
        ])
        assert acc[0]["function"]["name"] == "calculator"


class TestRunStreamContentOnly:
    """无工具链路：reasoning/delta 事件按序产出，StopIteration.value 为 AgentResult。"""

    def _orch(self, monkeypatch, chunks):
        from app.agent import glm_client

        def fake_chat(**kwargs):
            assert kwargs.get("stream") is True
            return iter(chunks)

        monkeypatch.setattr(glm_client, "chat", fake_chat)
        return AgentOrchestrator(allowed_tools=[], user_id="u-1")

    def test_events_and_result(self, monkeypatch):
        orch = self._orch(monkeypatch, [
            _chunk(reasoning="先想一下"),
            _chunk(content="你好"),
            _chunk(content="，同学"),
            _chunk(usage=SimpleNamespace(
                prompt_tokens=100,
                prompt_tokens_details=SimpleNamespace(cached_tokens=40),
            )),
        ])
        gen = orch.run_stream(history=[], user_text="打个招呼")
        events = []
        while True:
            try:
                events.append(next(gen))
            except StopIteration as stop:
                result = stop.value
                break

        kinds = [e["type"] for e in events]
        # usage chunk 只累计 token，不产生前端事件
        assert kinds == ["reasoning", "delta", "delta"]
        assert result.text == "你好，同学"
        assert result.thinking == "先想一下"
        assert result.prompt_tokens == 100
        assert result.cached_tokens == 40


class TestSse:
    def test_frame_shape(self):
        frame = _sse("delta", {"text": "hi"})
        assert frame == 'event: delta\ndata: {"text": "hi"}\n\n'

    def test_chinese_not_escaped(self):
        frame = _sse("meta", {"title": "新对话"})
        assert "新对话" in frame


class TestOptionsBlock:
    def test_marks_selected_option(self):
        payload = {"options": [
            {"value": "A", "label": "进程是资源分配单位"},
            {"value": "B", "label": "进程是调度单位"},
        ]}
        out = _options_block(payload, "B")
        assert "← 学生选择" in out
        assert out.index("B") < out.index("A") or "A" in out

    def test_empty_payload(self):
        assert _options_block(None, "A") == ""
        assert _options_block({}, "A") == ""
