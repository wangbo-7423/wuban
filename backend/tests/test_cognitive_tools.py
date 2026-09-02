"""认知状态工具单测（不连库 / 不连 GLM）。

覆盖：注册表声明（scenes/digest 开关）、证据词表校验、无用户身份的结构化降级。
DB 写入路径由 test_telemetry 同款的「绝不抛异常」约定保证——工具层统一在
execute_tool 里兜异常，这里只测纯逻辑分支。
"""
from __future__ import annotations

import pytest

from app.agent import tools
from app.core.config import settings
from app.mcp import current_user_id
from app.agent.tools import (
    _EVIDENCE_TYPES,
    _normalize_evidence_type,
    mastery_evidence,
    scaffold_state,
)


@pytest.fixture()
def _registry(monkeypatch):
    """强制重建工具注册表（默认开关全开）。"""
    monkeypatch.setattr(settings, "enable_scaffold_state", True)
    monkeypatch.setattr(settings, "enable_mastery_evidence", True)
    monkeypatch.setattr(tools, "_TOOLS", None)
    yield
    monkeypatch.setattr(tools, "_TOOLS", None)  # 防污染其他用例


class TestRegistry:
    def test_both_tools_registered_with_scenes(self, _registry):
        s = tools.get_tool("scaffold_state")
        m = tools.get_tool("mastery_evidence")
        assert s is not None and m is not None
        # 全场景装配（"*"）：任何意图路由都带得上
        assert "*" in s.scenes and "*" in m.scenes
        # 声明式压缩摘要字段就位
        assert s.digest_fields and m.digest_fields

    def test_disabled_tools_absent(self, monkeypatch):
        monkeypatch.setattr(settings, "enable_scaffold_state", False)
        monkeypatch.setattr(settings, "enable_mastery_evidence", False)
        monkeypatch.setattr(tools, "_TOOLS", None)
        assert tools.get_tool("scaffold_state") is None
        assert tools.get_tool("mastery_evidence") is None


class TestEvidenceTypeValidation:
    def test_accepts_controlled_vocabulary(self):
        for et in _EVIDENCE_TYPES:
            assert _normalize_evidence_type(et) == et

    def test_rejects_unknown_type(self):
        assert _normalize_evidence_type("score_85") is None
        assert _normalize_evidence_type("mastered") is None
        assert _normalize_evidence_type("") is None
        assert _normalize_evidence_type(None) is None

    def test_rejects_before_db_or_user_access(self):
        """词表外类型在连库/取身份之前就被拒——防 GLM 发明新类型。"""
        r = mastery_evidence(evidence_type="gave_up", topic="PID")
        assert r["ok"] is False and "evidence_type 必须是" in r["error"]


class TestNoUserContextDegradation:
    """无用户身份（后台/裸调用）→ 结构化错误，绝不抛异常。"""

    def test_mastery_evidence(self):
        token = current_user_id.set(None)
        try:
            r = mastery_evidence(evidence_type="deep_question", topic="卷积")
        finally:
            current_user_id.reset(token)
        assert r["ok"] is False and "用户身份" in r["error"]

    def test_scaffold_state(self):
        token = current_user_id.set(None)
        try:
            r = scaffold_state(topic="傅里叶")
        finally:
            current_user_id.reset(token)
        assert r["ok"] is False and "用户身份" in r["error"]

    def test_mastery_evidence_empty_topic(self):
        token = current_user_id.set("u1")
        try:
            r = mastery_evidence(evidence_type="stuck", topic="  ")
        finally:
            current_user_id.reset(token)
        assert r["ok"] is False and "topic" in r["error"]
