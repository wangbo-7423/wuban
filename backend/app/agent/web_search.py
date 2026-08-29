"""联网搜索（web_search 工具的实现层）：可插拔后端 + 结构化降级。

后端优先级（取第一个可用的）：
1. **Tavily**：配置了 `settings.tavily_api_key` 时走官方 REST API（结果最稳，有免费额度）；
2. **ddgs**：安装了 `ddgs` 包时走多引擎聚合（默认 auto，国内网络可在
   settings.web_search_backend 指定 `bing`），零 API key。

设计约束（对齐 tools.py 的失败语义）：
- 任何失败都返回 `{"ok": False, "error": ...}` 结构化结果，让 GLM 下一轮
  自己换策略——搜索挂了不能挂主链路；
- 每条结果只留 title/url/snippet 三字段，snippet 截断，防止搜索结果把
  上下文窗口塞爆（观察遮蔽压不到当前轮的 Observation）。
"""
from __future__ import annotations

import json
import logging
import re
import urllib.parse
import urllib.request
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT_SEC = 8
_SNIPPET_CAP = 200
_TAVILY_ENDPOINT = "https://api.tavily.com/search"
_BING_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


def _clip(s: str, cap: int = _SNIPPET_CAP) -> str:
    return (s or "").strip()[:cap]


def _search_tavily(query: str, top_k: int) -> list[dict[str, str]]:
    """Tavily REST：POST {api_key, query, max_results}。无 key 返回空列表（未配置）。"""
    api_key = (settings.tavily_api_key or "").strip()
    if not api_key:
        return []
    body = json.dumps(
        {"api_key": api_key, "query": query, "max_results": top_k}
    ).encode("utf-8")
    req = urllib.request.Request(
        _TAVILY_ENDPOINT,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT_SEC) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    out: list[dict[str, str]] = []
    for r in data.get("results") or []:
        out.append(
            {
                "title": _clip(r.get("title") or "", 100),
                "url": (r.get("url") or "").strip(),
                "snippet": _clip(r.get("content") or ""),
            }
        )
    return out


def _strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "")


def _search_bing(query: str, top_k: int) -> list[dict[str, str]]:
    """Bing 中国区 HTML 抓取：零 API key、零依赖，国内网络最稳的免费路径。

    解析标准 b_algo 结果块（<li class="b_algo"><h2><a href>标题</a></h2><p>摘要</p>）；
    标记变化时抛异常 → 聚合层记错误并降级到下一个后端。
    """
    url = (
        "https://cn.bing.com/search?q=" + urllib.parse.quote(query)
        + "&count=" + str(top_k)
    )
    req = urllib.request.Request(url, headers={"User-Agent": _BING_UA})
    with urllib.request.urlopen(req, timeout=_TIMEOUT_SEC) as resp:
        html = resp.read().decode("utf-8", "ignore")

    out: list[dict[str, str]] = []
    for block in re.findall(r'<li class="b_algo".*?</li>', html, re.S)[:top_k]:
        m = re.search(r'<h2[^>]*><a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not m:
            continue
        href, title = m.group(1), _strip_tags(m.group(2))
        if not href or not title:
            continue
        snip = re.search(r"<p[^>]*>(.*?)</p>", block, re.S)
        out.append(
            {
                "title": _clip(title, 100),
                "url": href.strip(),
                "snippet": _clip(_strip_tags(snip.group(1))) if snip else "",
            }
        )
    return out


def _search_ddgs(query: str, top_k: int) -> list[dict[str, str]]:
    """ddgs 多引擎聚合。未安装返回空列表（视为未配置）。"""
    try:
        from ddgs import DDGS
    except ImportError:
        return []
    with DDGS() as d:
        rows = d.text(
            query,
            max_results=top_k,
            backend=settings.web_search_backend or "auto",
        )
    out = []
    for r in rows or []:
        out.append(
            {
                "title": _clip(r.get("title") or "", 100),
                "url": (r.get("href") or r.get("url") or "").strip(),
                "snippet": _clip(r.get("body") or ""),
            }
        )
    return out


# 后端名称按优先级排列；执行时经 globals() 动态解析（便于测试替身按名替换）
_PROVIDER_NAMES: tuple[str, ...] = ("tavily", "bing", "ddgs")


def web_search_impl(query: str, top_k: int = 5) -> dict[str, Any]:
    """tools.web_search 的实现。永不抛异常，失败返回结构化错误。"""
    query = (query or "").strip()
    if not query:
        return {"ok": False, "error": "搜索关键词为空"}
    top_k = max(1, min(int(top_k or 5), 8))

    errors: list[str] = []
    for name in _PROVIDER_NAMES:
        fn = globals()[f"_search_{name}"]
        try:
            results = fn(query, top_k)
        except Exception as e:  # noqa: BLE001
            logger.warning("web_search[%s] 失败: %s", name, e)
            errors.append(f"{name}: {type(e).__name__}: {e}")
            continue
        if not results and name == "tavily":
            errors.append("tavily: 未配置 api_key")
            continue
        return {
            "ok": True,
            "provider": name,
            "query": query,
            # 聚合层统一裁剪：不管后端实现如何，进窗口的结果一定有界
            "results": [
                {
                    "title": _clip(r.get("title", ""), 100),
                    "url": (r.get("url") or "").strip(),
                    "snippet": _clip(r.get("snippet", "")),
                }
                for r in results
            ],
        }

    return {
        "ok": False,
        "error": "所有搜索后端都不可用（" + "；".join(errors) + "）",
        "hint": "本地知识足以回答常见课程问题，请直接用已有知识回应并说明确定度",
    }
