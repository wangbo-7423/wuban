"""search_verify：断言核实流水线 skill（横切任务型，先例 project_guide）。

## 为什么是 skill 而不是工具

单次搜索是单步调用，`web_search` 工具已经覆盖（docs/11 §3）；但「核实一个
具体事实断言」是多步**确定性**流水线：构造查询变体 → 搜索（复用 web_search
的后端链与域策略）→ 抓取 top 权威页原文 → 抽取证据句 → 给出证据支架。一步
模型调用买到整条管线（同 calculus 一步买到 sympy 推导）——MAX_TOOL_STEPS=4
的预算里省下的步数留给教学法。设计记录：docs/11 §6.5。

## 时延预算

两阶段并行：查询搜索（≤2 个查询并发，各 8s 内部超时）→ 权威页抓取
（≤2 页并发，单页 4s 超时）。最坏 ≈12s，与 mcp_memory_call_timeout 同量级。

## 失败语义（结构化降级，与 web_search 同约定）

- 抓页失败 / 抽不出句子 → `ok=true, extracts=[], verdict_hint="snippet_only"`，
  模型退回用搜索摘要判断（SKILL.md 会注入相应的消费纪律）；
- 搜索零命中 → `verdict_hint="no_results"`；
- 搜索后端全挂 → `ok=False` + hint，模型声明确定度后用本地知识回答。
"""
from __future__ import annotations

import html as _html
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.agent.web_search import web_search_impl
from app.skills.base import SkillSpec

_MODULE_DIR = Path(__file__).resolve().parent

# ── 预算常量 ─────────────────────────────────────────────
_FETCH_TIMEOUT = 4.0        # 单页抓取超时（秒）
_FETCH_BYTES_CAP = 400_000  # 单页原始字节上限（防超大页面拖垮解析）
_TEXT_CAP = 20_000          # 抽取用正文字符上限
_SENT_CAP = 6               # 每页最多抽取的证据句数
_PAGES = 2                  # 最多抓取的权威页数
_QUERIES = 2                # 最多构造的查询变体数
_CLAIM_CAP = 300            # 断言长度上限（防把整段文章当断言塞进来）

# ── 查询构造：剥离问句外壳，留核心术语 ────────────────────
_ZH_SHELL = (
    "帮我核实", "帮我查", "请问", "我想核实", "核实一下", "查一下",
    "帮我看看", "是不是真的", "是不是", "有没有", "还有没有", "还会不会",
    "对不对", "现在", "已经", "这个", "那个",
)
_ZH_TAIL = ("吗", "么", "呢")
_LATIN_STOPS = {
    "the", "a", "an", "is", "are", "was", "were", "does", "do", "did",
    "of", "in", "on", "and", "or", "to", "for", "with", "it", "its",
    "still", "removed",  # removed 常见于断言但单独搜噪声大，保留动词原形
}

_TOKEN_RE = re.compile(
    r"[A-Za-z_][A-Za-z0-9_.\-]*|[\u4e00-\u9fff]{2,6}|[0-9]+(?:\.[0-9]+)?"
)


def _clean_claim(claim: str) -> str:
    c = re.sub(r"\s+", " ", str(claim or "")).strip()
    return c[:_CLAIM_CAP]


def _strip_shell(claim: str) -> str:
    """剥掉问句外壳（帮我核实/是不是…吗），留核心陈述。"""
    short = claim
    for w in _ZH_SHELL:
        short = short.replace(w, " ")
    short = re.sub(rf"[{''.join(_ZH_TAIL)}]?[?？!！。.]*\s*$", "", short).strip()
    return re.sub(r"\s+", " ", short)


def _queries(claim: str, lang: str) -> list[str]:
    """断言 → 1~2 个查询变体（确定性规则）。

    - 术语变体：只留拉丁/数字 token（`numpy 2.0 np.float_`）——官方文档以
      英文为主，这个变体召回最好；
    - 清洗变体：剥壳后的完整陈述（保留中文语境）；
    - en 默认术语变体优先，zh 相反。问句外壳永不进查询。
    """
    short = _strip_shell(claim)
    base = short if len(short) >= 4 else claim
    latin = " ".join(
        t for t in _TOKEN_RE.findall(claim) if not _is_cjk(t)
    ).strip()
    pair = [latin, base] if (lang or "en").lower() == "en" else [base, latin]
    out: list[str] = []
    for c in pair:
        c = c.strip()[:160]
        if c and c not in out:
            out.append(c)
    return out or [claim[:160]]


def _is_cjk(token: str) -> bool:
    return all("\u4e00" <= ch <= "\u9fff" for ch in token)


def _keywords(claim: str) -> list[str]:
    """断言 → 关键词表（证据句抽取用）。中英混合，去停用词，保序去重。"""
    out: list[str] = []
    for tok in _TOKEN_RE.findall(claim):
        low = tok.lower()
        if low in _LATIN_STOPS or tok in _ZH_SHELL:
            continue
        if len(tok) >= 2 and tok not in out:
            out.append(tok)
    return out[:8]


# ── 抓取与证据句抽取（纯标准库，同 project_guide 的零依赖取向）──
def _fetch_text(url: str) -> str:
    """抓页面 → 去脚本/样式/标签 → 纯文本。任何失败返回空串（可降级）。"""
    try:
        import urllib.request

        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (compatible; ai-banxue/1.0)"}
        )
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
            raw = resp.read(_FETCH_BYTES_CAP)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", "ignore")
        text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = _html.unescape(text)
        return re.sub(r"\s+", " ", text)[:_TEXT_CAP]
    except Exception:  # noqa: BLE001
        return ""


# 句子切分：中文句终符直接切；英文句点只在「点后跟空白」时切——
# np.float_ / 2.0 这类 token 内部的点后面不是空白，不会被切碎
_SENT_SPLIT = re.compile(r"(?<=[。！？!?；;])\s*|(?<=\.)\s+|\n+")


def _extract_sentences(text: str, keywords: list[str]) -> list[str]:
    """正文 → 含任一关键词的句子（长度过滤 + 保序去重 + 截断）。"""
    if not text or not keywords:
        return []
    kws = [k.lower() for k in keywords]
    out: list[str] = []
    for sent in _SENT_SPLIT.split(text):
        s = sent.strip()
        if not (24 <= len(s) <= 240):
            continue
        low = s.lower()
        if any(k in low for k in kws) and s not in out:
            out.append(s.strip())
            if len(out) >= _SENT_CAP:
                break
    return out


def _domain_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _pick_pages(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """挑抓取目标：优先 authority ≥0.6 的前 _PAGES 条，不足再补普通结果。"""
    strong = [r for r in results if float(r.get("authority") or 0) >= 0.6]
    weak = [r for r in results if r not in strong]
    return (strong + weak)[:_PAGES]


def solve(claim: str, lang: str = "en", fetch_pages: bool = True) -> dict[str, Any]:
    """核实一个事实断言：搜索 → 抓权威页 → 抽证据句 → 证据支架。"""
    c = _clean_claim(claim)
    if not c:
        return {"ok": False, "error": "断言为空"}

    queries = _queries(c, lang)
    with ThreadPoolExecutor(max_workers=_QUERIES) as pool:
        search_outs = list(pool.map(lambda q: web_search_impl(q, 5), queries))

    merged: dict[str, dict[str, Any]] = {}
    search_ok = False
    errors: list[str] = []
    for out in search_outs:
        if out.get("ok"):
            search_ok = True
            for r in out.get("results") or []:
                u = str(r.get("url") or "").strip()
                if u and u not in merged:
                    merged[u] = r
        else:
            errors.append(str(out.get("error") or "search failed"))
    results = list(merged.values())

    if not search_ok:
        return {
            "ok": False,
            "error": "所有搜索后端都不可用（" + "；".join(errors[:3]) + "）",
            "hint": "用本地知识回答并声明确定度低，提示学生自行查证",
        }

    # 结果统一按 authority 稳定降序（多查询合并后恢复全局排序）
    results.sort(key=lambda r: -float(r.get("authority") or 0))

    extracts: list[dict[str, Any]] = []
    if fetch_pages and results:
        keywords = _keywords(c)
        pages = _pick_pages(results)
        with ThreadPoolExecutor(max_workers=_PAGES) as pool:
            texts = list(pool.map(lambda p: _fetch_text(str(p.get("url") or "")), pages))
        for page, text in zip(pages, texts):
            if not text:
                continue
            sents = _extract_sentences(text, keywords)
            if sents:
                extracts.append(
                    {
                        "url": page.get("url"),
                        "title": page.get("title"),
                        "authority": page.get("authority"),
                        "sentences": sents,
                    }
                )

    return {
        "ok": True,
        "claim": c,
        "queries": queries,
        "results": results[:6],
        "extracts": extracts,
        # 证据可用性支架（不是结论！三态结论由模型按 SKILL.md 纪律给出）：
        # extracts_available=有原文证据句 / snippet_only=仅摘要 / no_results=零命中
        "verdict_hint": (
            "extracts_available" if extracts
            else "snippet_only" if results
            else "no_results"
        ),
        "citations": [
            {"title": r.get("title"), "url": r.get("url"), "authority": r.get("authority")}
            for r in results[:3]
        ],
    }


def _schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "search_verify",
            "description": (
                "核实一个具体的事实断言（库/框架版本行为、API 签名、论文著作归属、"
                "发布日期）：自动构造查询 → 搜索权威源 → 抓取原文抽取证据句，"
                "返回证据与结论支架。只是快查一个出处用 web_search 即可；"
                "要对断言下「已核实/部分吻合/无法核实」的结论时用本工具。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "claim": {
                        "type": "string",
                        "description": "要核实的事实断言，一句话，如 'numpy 2.0 移除了 np.float_'",
                    },
                    "lang": {
                        "type": "string",
                        "enum": ["en", "zh"],
                        "description": "查询语言提示；技术断言默认 en（官方文档以英文为主）",
                    },
                    "fetch_pages": {
                        "type": "boolean",
                        "description": "是否抓取权威页原文抽取证据句（默认 true；false 只用搜索摘要，更快）",
                    },
                },
                "required": ["claim"],
            },
        },
    }


SKILL = SkillSpec(
    name="search_verify",
    title="断言核实",
    description="核实一个具体的事实断言（版本/API/出处），返回证据与三态结论支架",
    schema=_schema(),
    solve=solve,
    module_dir=_MODULE_DIR,
    scenes=("concept", "engineering"),
    digest_fields=("verdict_hint", "results"),
    guide="guide_search.md",
)
