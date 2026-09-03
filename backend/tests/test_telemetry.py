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


class TestSearchAndVerifyMetrics:
    """检索与核验指标（docs/11 §4）：搜索留痕 + 裸算率口径。"""

    @staticmethod
    def _search_result() -> AgentResult:
        r = AgentResult()
        r.text = "官方文档确认该 API 已移除"
        r.tool_calls = [
            {
                "tool_name": "web_search", "args": {}, "ok": True, "took_ms": 800,
                "result": {"ok": True, "results": [
                    {"title": "a", "url": "https://docs.python.org/3/", "authority": 1.0},
                    {"title": "b", "url": "https://numpy.org/doc/", "authority": 1.0},
                ]},
            },
            {
                "tool_name": "web_search", "args": {}, "ok": True, "took_ms": 600,
                "result": {"ok": True, "results": [
                    {"title": "c", "url": "https://docs.python.org/3/library/io", "authority": 1.0},
                    {"title": "bad", "url": "::::", "authority": 0},
                ]},
            },
        ]
        return r

    def test_search_metrics_filled(self, _telemetry_on):
        db = _FakeDB()
        record_agent_turn(
            db, user_id="u1", allowed_tools=["web_search"],
            result=self._search_result(), success=True,
        )
        row = db.rows[0]
        assert row.search_calls == 2
        assert row.search_results == 4
        # 域去重且解析失败的 url 不计入
        assert row.search_top_domains == "docs.python.org,numpy.org"

    def test_bare_numeric_true_when_math_scene_no_tool(self, _telemetry_on):
        db = _FakeDB()
        r = AgentResult()
        r.text = "结果约为 0.707，积分收敛"
        r.tool_calls = [{"tool_name": "kg_lookup", "args": {}, "result": {}, "ok": True}]
        record_agent_turn(
            db, user_id="u1", allowed_tools=["calculus", "ode", "kg_lookup"],
            result=r, success=True,
        )
        assert db.rows[0].bare_numeric is True

    def test_bare_numeric_false_with_calc_tool(self, _telemetry_on):
        db = _FakeDB()
        r = AgentResult()
        r.text = "结果为 42"
        r.tool_calls = [{"tool_name": "calculus", "args": {}, "result": {}, "ok": True}]
        record_agent_turn(
            db, user_id="u1", allowed_tools=["calculus"], result=r, success=True,
        )
        assert db.rows[0].bare_numeric is False

    def test_bare_numeric_false_outside_math_scene(self, _telemetry_on):
        db = _FakeDB()
        r = AgentResult()
        r.text = "有 3 个步骤"
        r.tool_calls = []
        record_agent_turn(
            db, user_id="u1", allowed_tools=["kg_lookup", "memory_search"],
            result=r, success=True,
        )
        assert db.rows[0].bare_numeric is False

    def test_authority_hits_counted(self, _telemetry_on):
        """权威域占比的分子：authority ≥ 0.85 的结果条数。"""
        db = _FakeDB()
        r = self._search_result()  # 4 条结果：1.0 / 1.0 / 1.0 / 0（bad url 无 authority）
        record_agent_turn(
            db, user_id="u1", allowed_tools=["web_search"], result=r, success=True,
        )
        row = db.rows[0]
        assert row.search_results == 4
        assert row.search_authority_hits == 3

    def test_search_verify_skill_counted(self, _telemetry_on):
        """search_verify skill 与 web_search 同口径并入检索指标（docs/11 §6.5）。"""
        db = _FakeDB()
        r = AgentResult()
        r.text = "官方迁移指南确认该断言"
        r.tool_calls = [
            {
                "tool_name": "search_verify", "args": {}, "ok": True, "took_ms": 3000,
                "result": {"ok": True, "verdict_hint": "extracts_available", "results": [
                    {"title": "a", "url": "https://numpy.org/release", "authority": 1.0},
                ]},
            },
        ]
        record_agent_turn(
            db, user_id="u1", allowed_tools=["search_verify"], result=r, success=True,
        )
        row = db.rows[0]
        assert row.search_calls == 1
        assert row.search_results == 1
        assert row.search_authority_hits == 1
        assert row.search_top_domains == "numpy.org"
