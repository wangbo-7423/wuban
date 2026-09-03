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

# ── 检索 curation：权威域加权（docs/11 §3.2）────────────────────
# 通用引擎对工科查询会混入资讯/内容农场；这里对结果按域打分重排，
# 让官方文档/教材/论文/问答站排到窗口前排。分数进返回值（authority 字段），
# cards.py 拿它当 evidence.confidence，遥测拿它算「权威域占比」。
# 设计约束：静态表 + 纯函数，不引第三方依赖；未命中域按 0 分保持原序。
_DOMAIN_WEIGHTS: tuple[tuple[str, float], ...] = (
    # 语言/库/标准官方文档（engineering 场景最有用的来源）
    ("docs.python.org", 1.0), ("numpy.org", 1.0),
    ("scipy.org", 1.0), ("matplotlib.org", 1.0), ("pytorch.org", 1.0),
    ("tensorflow.org", 1.0), ("cppreference.com", 1.0), ("rust-lang.org", 1.0),
    ("go.dev", 1.0), ("gnu.org", 1.0), ("kernel.org", 1.0), ("man7.org", 1.0),
    ("developer.mozilla.org", 1.0), ("mdn.dev", 1.0), ("w3.org", 1.0),
    ("sqlite.org", 1.0), ("postgresql.org", 1.0), ("mysql.com", 1.0),
    ("docker.com", 1.0), ("git-scm.com", 1.0), ("llvm.org", 1.0),
    ("microsoft.com", 0.9), ("learn.microsoft.com", 1.0),
    # 论文 / 会议 / 学术
    ("arxiv.org", 1.0), ("acm.org", 1.0), ("ieee.org", 0.95),
    ("springer.com", 0.9), ("sciencedirect.com", 0.9), ("doi.org", 0.9),
    # 大学 / 课程（edu 域整体权威）
    (".edu.cn", 0.9), (".edu", 0.9), ("ocw.mit.edu", 1.0),
    # 百科 / 问答（概念场景主力；问答站算法题质量高但别当唯一依据）
    ("wikipedia.org", 0.85), ("zh.wikipedia.org", 0.85),
    ("stackoverflow.com", 0.85), ("stackexchange.com", 0.85),
    # 通用技术社区（混合质量：可参考不优先）
    ("github.com", 0.7), ("github.io", 0.75), ("readthedocs.io", 0.8),
    ("cnblogs.com", 0.5), ("juejin.cn", 0.5), ("segmentfault.com", 0.6),
    ("zhihu.com", 0.4), ("zhuanlan.zhihu.com", 0.4), ("csdn.net", 0.3),
    ("blog.csdn.net", 0.3), ("baijiahao.baidu.com", 0.1),
    ("cloud.tencent.com", 0.5), ("jianshu.com", 0.3),
)


def _domain_weight(url: str) -> float:
    """URL → 权威分。按最长匹配后缀取分（learn.microsoft.com 优先于 microsoft.com）。"""
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
    except ValueError:
        return 0.0
    best = 0.0
    for suffix, w in _DOMAIN_WEIGHTS:
        if host == suffix or host.endswith("." + suffix) or host.endswith(suffix):
            best = max(best, w)
    return best


def _apply_domain_policy(results: list[dict[str, str]]) -> list[dict[str, Any]]:
    """按权威分稳定重排（同分保持引擎原序），并给每条标注 authority 档位。

    稳定排序很重要：引擎自己的相关度在同分域之间仍然有效，这里只做
    「把官方来源捞到前排」的保守干预，不做激进过滤（宁可有噪声，不空窗）。
    """
    scored = [(_domain_weight(r.get("url") or ""), i, r) for i, r in enumerate(results)]
    scored.sort(key=lambda t: (-t[0], t[1]))
    out: list[dict[str, Any]] = []
    for w, _i, r in scored:
        item = dict(r)
        item["authority"] = round(w, 2)
        out.append(item)
    return out


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
            # 聚合层统一裁剪：不管后端实现如何，进窗口的结果一定有界。
            # 裁剪后走域策略重排：官方来源捞前排 + authority 档位随行
            # （cards.py 转成 evidence.confidence，遥测算权威域占比）。
            "results": _apply_domain_policy(
                [
                    {
                        "title": _clip(r.get("title", ""), 100),
                        "url": (r.get("url") or "").strip(),
                        "snippet": _clip(r.get("snippet", "")),
                    }
                    for r in results
                ]
            ),
        }

    return {
        "ok": False,
        "error": "所有搜索后端都不可用（" + "；".join(errors) + "）",
        "hint": "本地知识足以回答常见课程问题，请直接用已有知识回应并说明确定度",
    }
