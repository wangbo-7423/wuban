"""web_search 可插拔后端单测（全部 mock，不真联网）。"""
from __future__ import annotations

import pytest

from app.agent import web_search as ws


class TestProviderPriority:
    def test_tavily_configured_wins(self, monkeypatch):
        monkeypatch.setattr(ws.settings, "tavily_api_key", "tvly-key", raising=False)
        monkeypatch.setattr(
            ws, "_search_tavily",
            lambda q, k: [{"title": "T", "url": "https://a", "snippet": "s"}],
        )
        monkeypatch.setattr(
            ws, "_search_ddgs",
            lambda q, k: (_ for _ in ()).throw(AssertionError("tavily 可用时不应走 ddgs")),
        )
        out = ws.web_search_impl("glm-5.3 release notes", 5)
        assert out["ok"] is True
        assert out["provider"] == "tavily"
        assert out["results"][0]["url"] == "https://a"

    def test_falls_through_to_later_backends_without_key(self, monkeypatch):
        monkeypatch.setattr(ws.settings, "tavily_api_key", "", raising=False)
        monkeypatch.setattr(ws, "_search_tavily", lambda q, k: [])  # 无 key → 空列表（视为未配置）
        # bing/ddgs 的空结果是「合法的 0 条命中」，不降级；这里让 bing 挂掉
        # 以验证 tavily 未配置 → bing 失败 → ddgs 兜底的完整链路
        monkeypatch.setattr(ws, "_search_bing", lambda q, k: (_ for _ in ()).throw(RuntimeError("x")))
        monkeypatch.setattr(
            ws, "_search_ddgs",
            lambda q, k: [{"title": "D", "url": "https://b", "snippet": "x"}],
        )
        out = ws.web_search_impl("query", 3)
        assert out["ok"] is True
        assert out["provider"] == "ddgs"

    def test_all_backends_fail_returns_structured_error(self, monkeypatch):
        monkeypatch.setattr(ws.settings, "tavily_api_key", "", raising=False)

        def _boom(q, k):
            raise RuntimeError("network blocked")

        monkeypatch.setattr(ws, "_search_tavily", _boom)
        monkeypatch.setattr(ws, "_search_bing", _boom)
        monkeypatch.setattr(ws, "_search_ddgs", _boom)
        out = ws.web_search_impl("query", 5)
        assert out["ok"] is False
        for name in ("tavily", "bing", "ddgs"):
            assert name in out["error"]
        assert "hint" in out  # 引导 GLM 用本地知识兜底

    def test_never_raises_on_exception(self, monkeypatch):
        monkeypatch.setattr(ws.settings, "tavily_api_key", "", raising=False)

        def _explode(q, k):
            raise RuntimeError("boom")

        monkeypatch.setattr(ws, "_search_tavily", _explode)
        monkeypatch.setattr(ws, "_search_bing", _explode)
        monkeypatch.setattr(ws, "_search_ddgs", _explode)
        assert ws.web_search_impl("q")["ok"] is False

    def test_keyboard_interrupt_propagates(self, monkeypatch):
        # Ctrl+C 必须放行（中止语义），只吞 Exception
        monkeypatch.setattr(ws.settings, "tavily_api_key", "", raising=False)

        def _abort(q, k):
            raise KeyboardInterrupt

        monkeypatch.setattr(ws, "_search_tavily", _abort)
        monkeypatch.setattr(ws, "_search_ddgs", _abort)
        with pytest.raises(KeyboardInterrupt):
            ws.web_search_impl("q")


class TestBingProvider:
    """Bing HTML 抓取的解析逻辑（mock 网络）。"""

    _HTML = """
    <li class="b_algo">
      <h2><a href="https://www.python.org/downloads/">What's New In <b>Python 3.13</b></a></h2>
      <div class="b_caption"><p>Improved interactive interpreter and <b>free-threaded</b> mode.</p></div>
    </li>
    <li class="b_algo">
      <h2><a href="https://example.com/2">Second Result</a></h2>
      <p>Another snippet here.</p>
    </li>
    <li class="b_algo"><div>没有 h2 的坏块，应被跳过</div></li>
    """

    def test_parses_results(self, monkeypatch):
        def fake_urlopen(req, timeout):
            import io
            ctx = io.BytesIO(self._HTML.encode("utf-8"))
            ctx.__enter__ = lambda s: s
            ctx.__exit__ = lambda s, *a: None
            return ctx

        monkeypatch.setattr(ws.urllib.request, "urlopen", fake_urlopen)
        out = ws._search_bing("python 3.13", 5)
        assert len(out) == 2
        assert out[0]["url"] == "https://www.python.org/downloads/"
        assert "Python 3.13" in out[0]["title"]          # 已剥 <b> 标签
        assert "free-threaded" in out[0]["snippet"]

    def test_markup_change_degrades_gracefully(self, monkeypatch):
        def fake_urlopen(req, timeout):
            raise RuntimeError("blocked")

        monkeypatch.setattr(ws.urllib.request, "urlopen", fake_urlopen)
        monkeypatch.setattr(ws.settings, "tavily_api_key", "", raising=False)

        def _boom(q, k):
            raise RuntimeError("x")

        monkeypatch.setattr(ws, "_search_ddgs", _boom)
        # bing 失败 → 继续 ddgs（也失败）→ 结构化错误，不抛异常
        out = ws.web_search_impl("q")
        assert out["ok"] is False and "bing" in out["error"]


class TestGuards:
    def test_empty_query(self):
        assert ws.web_search_impl("  ")["ok"] is False

    def test_top_k_clamped(self, monkeypatch):
        captured: dict = {}
        monkeypatch.setattr(ws.settings, "tavily_api_key", "k", raising=False)

        def _fake(q, k):
            captured["top_k"] = k
            return [{"title": "t", "url": "u", "snippet": "s"}]

        monkeypatch.setattr(ws, "_search_tavily", _fake)
        ws.web_search_impl("q", top_k=99)
        assert captured["top_k"] == 8  # 封顶，防窗口被塞爆

    def test_snippet_clipped(self, monkeypatch):
        monkeypatch.setattr(ws.settings, "tavily_api_key", "k", raising=False)
        monkeypatch.setattr(ws, "_search_tavily", lambda q, k: [
            {"title": "t" * 300, "url": "u", "snippet": "s" * 500}
        ])
        out = ws.web_search_impl("q")
        assert len(out["results"][0]["snippet"]) == 200
        assert len(out["results"][0]["title"]) == 100


class TestToolsWiring:
    def test_tools_web_search_delegates(self, monkeypatch):
        from app.agent import tools
        monkeypatch.setattr(
            ws, "web_search_impl",
            lambda q, k=5: {"ok": True, "provider": "tavily", "query": q, "results": []},
        )
        out = tools.web_search("hello")
        assert out["ok"] is True and out["query"] == "hello"

    def test_schema_shape(self):
        from app.agent.tools import _web_search_schema
        fn = _web_search_schema()["function"]
        assert fn["name"] == "web_search"
        assert set(fn["parameters"]["required"]) == {"query"}
