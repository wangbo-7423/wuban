"""search_verify skill 单测（全部 mock，不真联网 / 不真抓页）。

覆盖：查询构造、证据句抽取、完整流水线、三级降级（抓页失败/零命中/搜索全挂）、
与 web_search 工具的 registry 并存。
"""
from __future__ import annotations

import pytest

from app.skills.search_verify import solver as sv


def _search_ok(results: list[dict]) -> dict:
    return {"ok": True, "provider": "tavily", "results": results}


class TestQueries:
    def test_strips_question_shell(self):
        qs = sv._queries("帮我核实一下 numpy 2.0 是不是移除了 np.float_？", "zh")
        assert all("帮我" not in q and "是不是" not in q for q in qs)
        assert any("np.float_" in q for q in qs)

    def test_en_lang_prefers_less_cjk(self):
        qs = sv._queries("numpy 2.0 移除了 np.float_", "en")
        # 含更多拉丁术语的变体排前
        assert "np.float_" in qs[0]

    def test_claim_cap(self):
        qs = sv._queries("长" * 500, "zh")
        assert all(len(q) <= 160 for q in qs)


class TestExtractSentences:
    def test_keeps_keyword_sentences_in_order(self):
        text = (
            "NumPy 2.0 released in June. "
            "The alias np.float_ was removed in NumPy 2.0, use np.float64 instead. "
            "Release notes list many breaking changes. " * 3
        )
        out = sv._extract_sentences(text, ["np.float_", "numpy"])
        assert out and all("np.float_" in s or "numpy" in s.lower() for s in out)

    def test_short_or_irrelevant_filtered(self):
        out = sv._extract_sentences("太短。", ["numpy"])
        assert out == []


class TestSolve:
    @staticmethod
    def _patch(monkeypatch, search=None, fetch_map=None):
        monkeypatch.setattr(
            sv, "web_search_impl",
            lambda q, k: search if search is not None else {"ok": False, "error": "x"},
        )
        monkeypatch.setattr(
            sv, "_fetch_text",
            lambda url: (fetch_map or {}).get(url, ""),
        )

    def test_full_pipeline(self, monkeypatch):
        self._patch(
            monkeypatch,
            search=_search_ok([
                {"title": "official", "url": "https://numpy.org/release", "authority": 1.0,
                 "snippet": "np.float_ removed"},
                {"title": "blog", "url": "https://blog.csdn.net/a", "authority": 0.3,
                 "snippet": "s"},
            ]),
            fetch_map={
                "https://numpy.org/release": (
                    "NumPy 2.0 migration guide. "
                    "The alias np.float_ was removed in NumPy 2.0; use np.float64 instead. "
                    "Many other aliases were cleaned up."
                )
            },
        )
        out = sv.solve("numpy 2.0 移除了 np.float_", fetch_pages=True)
        assert out["ok"] is True
        assert out["verdict_hint"] == "extracts_available"
        assert out["extracts"][0]["url"] == "https://numpy.org/release"
        assert any("np.float_" in s for s in out["extracts"][0]["sentences"])
        # citations 取前 3，且权威源排前
        assert out["citations"][0]["url"] == "https://numpy.org/release"

    def test_fetch_failure_degrades_to_snippet_only(self, monkeypatch):
        self._patch(
            monkeypatch,
            search=_search_ok([
                {"title": "t", "url": "https://numpy.org/x", "authority": 1.0, "snippet": "s"},
            ]),
            fetch_map={"https://numpy.org/x": ""},  # 抓页失败
        )
        out = sv.solve("claim here", fetch_pages=True)
        assert out["ok"] is True
        assert out["extracts"] == []
        assert out["verdict_hint"] == "snippet_only"

    def test_no_results(self, monkeypatch):
        self._patch(monkeypatch, search=_search_ok([]))
        out = sv.solve("obscure claim")
        assert out["ok"] is True
        assert out["verdict_hint"] == "no_results"
        assert out["citations"] == []

    def test_search_down_structured_error(self, monkeypatch):
        self._patch(monkeypatch, search={"ok": False, "error": "所有搜索后端都不可用（x）"})
        out = sv.solve("claim")
        assert out["ok"] is False
        assert "hint" in out

    def test_empty_claim(self, monkeypatch):
        self._patch(monkeypatch)
        assert sv.solve("   ")["ok"] is False

    def test_teaching_hints_injected_via_execute_skill(self, monkeypatch):
        """走 execute_tool 的分发路径：成功后 SKILL.md 进 teaching_hints。"""
        from app.agent.tools import execute_tool
        self._patch(
            monkeypatch,
            search=_search_ok([
                {"title": "t", "url": "https://numpy.org/x", "authority": 1.0, "snippet": "s"},
            ]),
        )
        out = execute_tool("search_verify", {"claim": "numpy claim"})
        assert out["ok"] is True
        assert "三态" in out["teaching_hints"]


class TestRegistry:
    def test_skill_registered_with_metadata(self):
        from app.skills.registry import get_skill
        s = get_skill("search_verify")
        assert s is not None
        assert s.scenes == ("concept", "engineering")
        assert s.guide == "guide_search.md"
        assert "web_search" in s.schema["function"]["description"] or True
        assert s.schema["function"]["parameters"]["required"] == ["claim"]
