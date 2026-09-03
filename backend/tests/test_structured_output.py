"""chat_structured 单测：「提示词|模型|输出解析器」原生源（mock chat，不连网）。

覆盖：裸 JSON / 围栏、校验失败带错误反馈重试、重试耗尽抛最后异常、
response_format 透传。API 层异常不进 fix 循环的语义由 chat() 的既有
测试（test_glm_retry）保证，这里不重复。
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Literal

import pytest
from pydantic import BaseModel

from app.agent import glm_client


class _Out(BaseModel):
    intent: Literal["math", "concept"]
    note: str = ""


def _resp(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


class TestExtractJsonObject:
    def test_bare(self):
        assert glm_client.extract_json_object('{"a": 1}') == {"a": 1}

    def test_fenced(self):
        assert glm_client.extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}


class TestChatStructured:
    def test_plain_json(self, monkeypatch):
        monkeypatch.setattr(glm_client, "chat", lambda *a, **k: _resp('{"intent": "math"}'))
        out = glm_client.chat_structured([{"role": "user", "content": "x"}], _Out)
        assert out.intent == "math"

    def test_fenced_json(self, monkeypatch):
        monkeypatch.setattr(
            glm_client, "chat", lambda *a, **k: _resp('```json\n{"intent": "concept"}\n```')
        )
        out = glm_client.chat_structured([{"role": "user", "content": "x"}], _Out)
        assert out.intent == "concept"

    def test_json_mode_and_kwargs_passed_through(self, monkeypatch):
        captured: dict = {}

        def _fake(messages, **kwargs):
            captured.update(kwargs, messages=messages)
            return _resp('{"intent": "math"}')

        monkeypatch.setattr(glm_client, "chat", _fake)
        glm_client.chat_structured(
            [{"role": "user", "content": "x"}], _Out,
            temperature=0.1, reasoning_effort="low",
        )
        assert captured["response_format"] == {"type": "json_object"}
        assert captured["temperature"] == 0.1
        assert captured["reasoning_effort"] == "low"

    def test_fix_retry_appends_error_feedback(self, monkeypatch):
        calls: list[list[dict]] = []

        def _fake(messages, **kwargs):
            calls.append([dict(m) for m in messages])
            if len(calls) == 1:
                return _resp('{"intent": "history"}')  # 枚举外 → 校验失败
            return _resp('{"intent": "math", "note": "fixed"}')

        monkeypatch.setattr(glm_client, "chat", _fake)
        out = glm_client.chat_structured([{"role": "user", "content": "x"}], _Out)
        assert out.intent == "math" and out.note == "fixed"
        # 第二次调用带上了 assistant 原文 + user 修正指令（system 未动，缓存前缀安全）
        assert len(calls[1]) == 3
        assert calls[1][1]["role"] == "assistant"
        assert "不符合要求的 JSON 结构" in calls[1][2]["content"]
        assert "Literal" in calls[1][2]["content"] or "intent" in calls[1][2]["content"]

    def test_fix_exhausted_raises_last_error(self, monkeypatch):
        monkeypatch.setattr(glm_client, "chat", lambda *a, **k: _resp("不是 JSON"))
        with pytest.raises(ValueError):
            glm_client.chat_structured(
                [{"role": "user", "content": "x"}], _Out, fix_attempts=1,
            )

    def test_zero_fix_attempts_single_call(self, monkeypatch):
        calls: list = []

        def _fake(messages, **kwargs):
            calls.append(messages)
            return _resp("不是 JSON")

        monkeypatch.setattr(glm_client, "chat", _fake)
        with pytest.raises(Exception):
            glm_client.chat_structured(
                [{"role": "user", "content": "x"}], _Out, fix_attempts=0,
            )
        assert len(calls) == 1
