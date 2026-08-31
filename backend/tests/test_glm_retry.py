"""glm_client 限流/超时重试逻辑的回归测试。

背景：GLM 429 时请求在服务端排队 60~90s，SDK 60s 超时抛 APITimeoutError。
chat() 对这类暂时性错误做指数退避重试；其他错误（鉴权、参数）立即失败。
"""
from __future__ import annotations

from types import SimpleNamespace

import app.agent.glm_client as gc


class _FakeTimeout(Exception):
    pass


class _FakeRateLimit(Exception):
    def __init__(self):
        super().__init__("429 Too Many Requests")
        self.status_code = 429


def _patch_client(monkeypatch, outcomes):
    """monkeypatch get_client，使其按 outcomes 顺序抛异常/返回值。"""
    calls = {"n": 0}
    sleeps: list[float] = []

    class _FakeCompletions:
        @staticmethod
        def create(**kwargs):
            i = calls["n"]
            calls["n"] += 1
            outcome = outcomes[min(i, len(outcomes) - 1)]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=_FakeCompletions())
    )
    monkeypatch.setattr(gc, "get_client", lambda: fake_client)
    monkeypatch.setattr(gc.time, "sleep", lambda s: sleeps.append(s))
    return calls, sleeps


def test_is_retryable_timeout():
    assert gc._is_retryable(_FakeTimeout("request timed out")) is True


def test_is_retryable_rate_limit():
    assert gc._is_retryable(_FakeRateLimit()) is True


def test_is_retryable_auth_error_not_retryable():
    e = Exception("invalid api key")
    assert gc._is_retryable(e) is False


def test_chat_retries_on_timeout_then_succeeds(monkeypatch):
    ok = SimpleNamespace(choices=[SimpleNamespace(message={"content": "hi"})])
    calls, sleeps = _patch_client(monkeypatch, [_FakeTimeout("timed out"), ok])
    resp = gc.chat([{"role": "user", "content": "你好"}], enable_thinking=False)
    assert resp is ok
    assert calls["n"] == 2
    assert len(sleeps) == 1  # 重试了 1 次，退避 1 次


def test_chat_retries_exhausted_raises_unavailable(monkeypatch):
    calls, sleeps = _patch_client(
        monkeypatch,
        [_FakeRateLimit(), _FakeRateLimit(), _FakeRateLimit()],
    )
    try:
        gc.chat([{"role": "user", "content": "你好"}], enable_thinking=False)
        raised = False
    except gc.GLMUnavailable:
        raised = True
    assert raised
    # 默认 glm_rate_limit_retries=2 → 共 3 次尝试、2 次退避
    assert calls["n"] == 3
    assert len(sleeps) == 2
    # 指数退避：第二次退避比第一次长
    assert sleeps[1] > sleeps[0]


def test_chat_non_retryable_fails_fast(monkeypatch):
    calls, sleeps = _patch_client(monkeypatch, [Exception("invalid api key")])
    try:
        gc.chat([{"role": "user", "content": "你好"}], enable_thinking=False)
        raised = False
    except gc.GLMUnavailable:
        raised = True
    assert raised
    assert calls["n"] == 1
    assert sleeps == []
