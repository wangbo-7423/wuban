"""telemetry_service 纯逻辑单测（不连库）：聚合指标、失败留痕、绝不抛异常。

Harness 警示二的守门测试：遥测挂了绝不能影响聊天主链路。
"""
from __future__ import annotations

import pytest

from app.agent.orchestrator import AgentResult
from app.core.config import settings
from app.services.telemetry_service import record_agent_turn


class _FakeDB:
    """只实现 add() 的 Session 替身：捕获写入对象，供断言。"""

    def __init__(self, *, broken: bool = False):
        self.rows: list = []
        self._broken = broken

    def add(self, obj):
        if self._broken:
            raise RuntimeError("db down")
        self.rows.append(obj)


@pytest.fixture()
def _telemetry_on(monkeypatch):
    monkeypatch.setattr(settings, "enable_agent_telemetry", True)


def _result() -> AgentResult:
    r = AgentResult()
    r.text = "回答正文"
    r.thinking = "思考链"
    r.tool_steps = 2
    r.tool_calls = [
        {"tool_name": "kg_lookup", "args": {}, "result": {}, "ok": True, "took_ms": 5},
        {"tool_name": "code_runner", "args": {}, "result": {}, "ok": False, "took_ms": 7},
        {"tool_name": "code_runner", "args": {}, "result": {}, "ok": False, "took_ms": 9},
    ]
    r.compressed_chars = 1200
    r.prompt_tokens = 3000
    r.cached_tokens = 2100
    return r


class TestRecordAgentTurn:
    def test_success_row_aggregates_metrics(self, _telemetry_on):
        db = _FakeDB()
        record_agent_turn(
            db, user_id="u1", conversation_id="c1", course_id="physics",
            allowed_tools=["kg_lookup", "code_runner"],
            result=_result(), success=True, latency_ms=1234.6,
        )
        assert len(db.rows) == 1
        row = db.rows[0]
        assert row.ok is True and row.error is None
        assert row.tool_steps == 2 and row.tool_calls == 3
        # 两次失败都是同一个工具：去重后只记一个名字
        assert row.tool_failures == 2
        assert row.tool_fail_names == "code_runner"
        assert row.compressed_chars == 1200
        assert row.prompt_tokens == 3000 and row.cached_tokens == 2100
        assert row.thinking_chars == 3 and row.text_chars == 4
        assert row.latency_ms == 1234  # float 截整

    def test_failure_row_without_result(self, _telemetry_on):
        db = _FakeDB()
        record_agent_turn(
            db, user_id="u1", success=False, error="1002: AI 模型调用失败",
        )
        row = db.rows[0]
        assert row.ok is False
        assert "1002" in row.error
        # 失败路径没有 result，指标全部保持默认 0
        assert row.tool_calls == 0 and row.prompt_tokens == 0

    def test_disabled_flag_skips_write(self, monkeypatch):
        monkeypatch.setattr(settings, "enable_agent_telemetry", False)
        db = _FakeDB()
        record_agent_turn(db, user_id="u1", result=_result(), success=True)
        assert db.rows == []  # 开关关掉 → 一行不写

    def test_never_raises_when_db_broken(self, _telemetry_on):
        """核心约束：遥测挂了绝不能把异常抛回聊天主链路。"""
        db = _FakeDB(broken=True)
        record_agent_turn(
            db, user_id="u1", result=_result(), success=True, latency_ms=1
        )  # 不抛即通过

    def test_allowed_tools_truncated(self, _telemetry_on):
        db = _FakeDB()
        record_agent_turn(
            db, user_id="u1", allowed_tools=[f"tool_{i}" for i in range(100)],
            success=True,
        )
        assert len(db.rows[0].allowed_tools) <= 255
