"""意图识别（detect_intent）——Agent 处理学生消息的**第一层**。

对齐 `docs/单Agent平台-Agent与学生交互设计.md` §5（detect_intent）：
- 把学生消息分类到 8 种 InteractionMode（intake / propose_plan / practice / tutor /
  feedback / warn / self_assess / other）；
- **规则优先**（关键词 + 正则 + 权重打分 → 置信度），确定性、可解释、零成本；
- **预留轻量分类器接口**：采集 ≥300 标注样本后，构造时注入 `classifier`，
  规则与分类器融合（规则弱且 ML 高置信时 ML 接管），不破坏上游路由；
- **人工改判**：任意阶段前端可显式传 `force_intent` 覆盖自动识别。

打分要点（v2 优化）：
- **文本归一化**：全角→半角 + ASCII 小写 + 去空白，提升子串/正则鲁棒性；
- **否定感知**：在「不想/不要/不是/而不是…」窗口内的命中予以抑制，避免否定句误判；
- **证据去重**：同一意图下，被更长命中（结构化正则）完全包含的子串关键词不重复计分，
  避免「关键词 + 其超集正则」对一个表层重复计分；
- **优势式置信度**：置信度由「信号强度」与「领先第二名优势」共同决定，
  单关键词命中不再被误判为强意图，且不受各意图关键词数量多寡的系统性偏置；
- **ML 第二票修正**：仅在规则弱（top_score < ML_OVERRIDE_FLOOR）且 ML 高置信
  （≥ ML_OVERRIDE_PROB）时让 ML 接管，尊重规则主导；同向时给小幅加成。

> 后续接 LLM 分类时，只需替换 `recognize_intent` 内部实现（规则 → LLM 分类），
> 上游路由（student.py chat）不变——这就是「可插拔」。
"""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

# ─────────────────────────────────────────────────────────────
# 1. 意图定义（对齐文档 §4.1 七种交互模式 + other 兜底）
# ─────────────────────────────────────────────────────────────


class Intent(str, Enum):
    """8 种交互模式。warn 以 Agent 主动触发为主，学生侧为弱触发。"""

    INTAKE = "intake"               # 画像采集：自报水平/基础/目标
    PROPOSE_PLAN = "propose_plan"   # 路径规划：要学习计划/路线
    PRACTICE = "practice"           # 练习：做题/出题/刷题
    TUTOR = "tutor"                 # 答疑（P0 最高频）：问概念/为什么/区别
    FEEDBACK = "feedback"           # 反馈解读：测评/成绩/正确率
    WARN = "warn"                   # 学业预警/求助（Agent 主动为主）
    SELF_ASSESS = "self_assess"     # 自评校准：复盘/自我评估
    OTHER = "other"                 # 兜底：寒暄/闲聊/无法归类的请求


# 中文标签（供前端展示、日志调试）
INTENT_LABELS: dict[str, str] = {
    "intake": "画像采集",
    "propose_plan": "路径规划",
    "practice": "练习",
    "tutor": "答疑",
    "feedback": "反馈解读",
    "warn": "学业预警",
    "self_assess": "自评校准",
    "other": "其他",
}

# ─────────────────────────────────────────────────────────────
# 2. 规则引擎（关键词 + 正则 + 权重）
# ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class IntentRule:
    """一条意图规则。

    - `keywords`：子串命中，每条 +1.0 分（乘以 weight）；
    - `patterns`：正则命中，每条 +1.5 分（乘以 weight）——正则视为更强信号；
    - 同一意图多条规则、同一规则多个词命中，得分叠加；被更长命中完全包含的子串
      命中不重复计分（见 `IntentClassifier._score` 的证据去重）。
    """

    intent: Intent
    keywords: tuple[str, ...] = ()
    patterns: tuple[str, ...] = ()
    weight: float = 1.0
    label: str = ""                 # 规则别名（调试用）


# 规则表：按优先级排序（同分取先出现的）。调整关键词/权重即可优化意图边界，
# 这是「规则即配置」。v2 已收紧过松正则、移除与关键词等价的超集正则、
# 修正 intake/self_assess 的「掌握」边界。
INTENT_RULES: tuple[IntentRule, ...] = (
    # ── tutor 答疑：最高频、最该抢到 ──
    IntentRule(
        Intent.TUTOR,
        keywords=("为什么", "什么是", "啥是", "什么叫", "是什么", "区别", "差异",
                  "讲解", "解释", "讲讲", "不懂", "没懂", "不太懂", "不理解",
                  "怎么理解", "原理", "概念", "咋回事", "咋理解", "含义"),
        patterns=(r"(什么是|啥是|什么叫).{1,16}",
                  r"怎么理解.{1,12}",
                  r".{1,10}(区别|差异)是?什么",
                  r".{1,10}是(怎么|如何).{0,6}(回事|实现|工作)"),
        weight=1.0,
    ),
    # ── practice 练习：权重略高，避免「我不会做这道题」被 intake 抢走 ──
    IntentRule(
        Intent.PRACTICE,
        keywords=("练习", "做题", "出题", "题目", "练习题", "练练", "来几道",
                  "几道题", "考考我", "模拟题", "刷题", "练一练", "帮我做"),
        patterns=(r"给我.{0,4}(出|来|做).{0,4}题",   # 收紧：拒绝「给我讲讲这道题」
                  r"出.{0,6}(题|练习)",
                  r"做.{0,2}(道?题|练习)",            # 收紧：拒绝「做错了一道题」
                  r"来.{0,4}道题"),
        weight=1.2,
    ),
    # ── intake 画像采集（去掉 bare「掌握」，避免吞掉自评）──
    IntentRule(
        Intent.INTAKE,
        keywords=("我学过", "我基础", "我的水平", "我水平", "会一点", "零基础",
                  "我擅长", "不擅长", "掌握程度", "没学过", "学过一点",
                  "完全不会", "有些基础"),
        patterns=(r"我.{0,8}(水平|基础|学过|不会|擅长|不擅长)",
                  r"我觉得自己.{0,12}"),
        weight=1.0,
    ),
    # ── propose_plan 路径规划（去掉「学习计划.{0,12}」超集正则）──
    IntentRule(
        Intent.PROPOSE_PLAN,
        keywords=("学习计划", "学习路线", "规划", "怎么学", "从哪里开始", "先学",
                  "安排", "路径", "方案", "进度安排", "计划", "路线"),
        patterns=(r"怎么学", r"先学什么", r"从哪里开始",
                  r"帮我(规划|安排|制定).{0,12}",
                  r"(要|想|给|需要|帮我).{0,6}(学习计划|学习路线|规划|路线|方案|计划)"),
        weight=1.0,
    ),
    # ── feedback 反馈解读（去掉与关键词等价的「测评结果」正则）──
    IntentRule(
        Intent.FEEDBACK,
        keywords=("测评", "成绩", "分数", "正确率", "考得", "考了", "错题",
                  "错了很多", "测评结果", "错了"),
        patterns=(r"考.{0,4}(分|成绩|得.{0,3})", r"对了.{0,4}(道|题)"),
        weight=1.0,
    ),
    # ── self_assess 自评校准（补「我掌握得不错」类表达）──
    IntentRule(
        Intent.SELF_ASSESS,
        keywords=("自我评估", "自评", "复盘", "总结一下", "我掌握了",
                  "我觉得我懂", "我感觉我会"),
        patterns=(r"我(觉得|感觉)我.{0,8}(掌握|会|懂|学会|理解)",
                  r"我(掌握|会|懂|理解)得?(很|比较|不太|不)?(好|错|行|差|糟|烂)"),
        weight=1.0,
    ),
    # ── warn 学业预警（Agent 主动为主，学生侧弱触发）──
    IntentRule(
        Intent.WARN,
        keywords=("预警", "风险", "会不会挂", "要挂了", "跟不上了", "救救我",
                  "来不及", "危险", "学不进去"),
        patterns=(r"(会不会|是不是|要).{0,3}(挂|挂科)", r"跟不上了"),
        weight=1.0,
    ),
)

# ─────────────────────────────────────────────────────────────
# 3. 打分调参（集中可调）
# ─────────────────────────────────────────────────────────────

# 置信度 = 0.50 + W_STR*strength + W_DOM*dominance，封顶 0.98
SCORE_SAT = 2.5            # top_score 达到该值视为「信号充分」(strength=1.0)
MARGIN_SAT = 1.5           # top 与第二名差达到该值视为「明显胜出」(dominance=1.0)
W_STR = 0.30               # 信号充分度权重
W_DOM = 0.18               # 优势度权重
STRONG_THRESHOLD = 0.62    # 强意图的置信度下限（保留供前端/上游使用）
STRONG_SCORE_FLOOR = 1.5   # 强意图：top_score 至少一个结构化正则或两个关键词
STRONG_DOMINANCE_FLOOR = 0.5  # 强意图：且需明显领先第二名
# ML 第二票：仅当规则弱且 ML 高置信时让 ML 接管（尊重规则主导）
ML_OVERRIDE_PROB = 0.85
ML_OVERRIDE_FLOOR = 1.5      # 规则达此分（一个结构化正则/两个关键词）即视为已决断，ML 不接管

# 否定窗口：marker 起始后 NEGATION_WINDOW 个字符内的命中予以抑制
NEGATION_MARKERS: tuple[str, ...] = (
    "不想", "不要", "不用", "并非", "而不是", "不是", "没有",
    "不希望", "不愿", "不打算", "不准备", "谈不上",
)
NEGATION_WINDOW = 8

# 分句边界：否定窗口在此截断，避免越过逗号/句号抑制后一个肯定分句
_CLAUSE_BOUNDARY = frozenset(",.;!?。；！？\n")


# ─────────────────────────────────────────────────────────────
# 4. 结果结构
# ─────────────────────────────────────────────────────────────


@dataclass
class IntentResult:
    intent: Intent                                   # 最终意图（弱信号时降级为 OTHER）
    confidence: float                                # 0.5 ~ 0.98
    is_strong: bool                                  # 是否强意图（见 STRONG_* 下限）
    matched: list[str] = field(default_factory=list)          # 命中的规则/关键词（可解释）
    entities: list[str] = field(default_factory=list)         # 抽取的实体（知识点 id 等）
    candidates: list[tuple[str, float]] = field(default_factory=list)  # 全部意图得分（供人工改判）
    raw: str = ""                                    # 原始文本（调试/审计）
    trace: dict = field(default_factory=dict)        # 可解释留痕：归一化/否定/打分/ML

    def label(self) -> str:
        return INTENT_LABELS.get(self.intent.value, self.intent.value)

    def to_dict(self) -> dict:
        return {
            "intent": self.intent.value,
            "label": self.label(),
            "confidence": round(self.confidence, 2),
            "is_strong": self.is_strong,
            "matched": self.matched,
            "entities": self.entities,
            "candidates": [(k, round(v, 2)) for k, v in self.candidates],
            "trace": self.trace,
        }


# 轻量分类器协议：(text) -> (intent_value, prob)。≥300 标注样本后注入，
# 例如 sklearn 的 TfidfVectorizer + LinearSVC，或 ONNX 模型。
Classifier = Callable[[str], tuple[str, float]]


# ─────────────────────────────────────────────────────────────
# 5. 文本归一化与否定感知
# ─────────────────────────────────────────────────────────────


def _normalize_text(text: str) -> str:
    """全角→半角 + ASCII 小写 + 去空白。

    中文输入里空格多为误触/排版残留，去掉后显著提升子串与正则命中率
    （如「我 不 懂」「什 么 是」「ＰＣＢ」）。
    """
    text = unicodedata.normalize("NFKC", text or "")
    text = text.lower()
    return "".join(text.split())


def _all_occurrences(text: str, sub: str):
    """yield 所有子串起始位置（用于逐位置判定是否被否定窗口覆盖）。"""
    if not sub:
        return
    i = text.find(sub)
    while i != -1:
        yield i
        i = text.find(sub, i + 1)


def _negation_spans(text: str) -> list[tuple[int, int]]:
    """返回否定抑制窗口 [marker_start, end)。

    落在窗口内的关键词/正则命中视为被否定（如「不想练习」「不是要练习」），
    予以抑制；窗口在分句边界（逗号/句号等）处截断，避免越过标点抑制后一个
    肯定分句（如「不想练习，想解释」里的「解释」不应被抑制）。
    """
    spans: list[tuple[int, int]] = []
    for m in NEGATION_MARKERS:
        i = text.find(m)
        while i != -1:
            start = i
            end = i + len(m) + NEGATION_WINDOW
            for j in range(i + len(m), min(end, len(text))):
                if text[j] in _CLAUSE_BOUNDARY:
                    end = j
                    break
            spans.append((start, end))
            i = text.find(m, i + 1)
    return spans


def _in_neg(start: int, spans: list[tuple[int, int]]) -> bool:
    return any(s <= start < e for s, e in spans)


# ─────────────────────────────────────────────────────────────
# 6. 分类器
# ─────────────────────────────────────────────────────────────


class IntentClassifier:
    """规则优先的意图分类器；可选注入轻量 ML 分类器做「第二票」。"""

    def __init__(
        self,
        rules: tuple[IntentRule, ...] = INTENT_RULES,
        classifier: Classifier | None = None,
    ):
        self.rules = rules
        self.classifier = classifier
        self._compiled = [(r, [re.compile(p) for p in r.patterns]) for r in rules]

    # ── 规则打分（归一化 + 否定感知 + 证据去重）──
    def _score(
        self, text: str, neg_spans: list[tuple[int, int]]
    ) -> tuple[dict[Intent, float], list[str]]:
        scores: dict[Intent, float] = defaultdict(float)
        matched: list[str] = []
        for rule, compiled in self._compiled:
            label = rule.label or rule.intent.value
            hits: list[tuple[int, int, float, str, str]] = []  # (start,end,points,src,kind)
            # 关键词命中：命中即计一次（取首个非否定出现），避免同一关键词重复膨胀
            for kw in rule.keywords:
                for start in _all_occurrences(text, kw):
                    if _in_neg(start, neg_spans):
                        continue
                    hits.append((start, start + len(kw), 1.0, kw, "kw"))
                    break
            # 正则命中：命中即计一次（取首个非否定匹配），避免重复膨胀
            for pat in compiled:
                for m in pat.finditer(text):
                    if _in_neg(m.start(), neg_spans):
                        continue
                    hits.append((m.start(), m.end(), 1.5, pat.pattern, "regex"))
                    break
            if not hits:
                continue
            # 证据去重：按命中跨度降序贪心选择，被更长命中完全包含的子串命中不计
            # （避免「学习计划」+ 其超集正则、「为什么」+ `为什么.{0,24}` 重复计分）
            hits.sort(key=lambda h: (h[1] - h[0]), reverse=True)
            covered: list[tuple[int, int]] = []
            sel = 0.0
            for start, end, pts, src, kind in hits:
                if any(cs <= start and end <= ce for cs, ce in covered):
                    continue
                covered.append((start, end))
                sel += pts
                matched.append(f"{label}:{src}" if kind == "kw" else f"{label}:regex")
            if sel > 0:
                scores[rule.intent] += sel * rule.weight
        return scores, matched

    def predict(
        self,
        text: str,
        force_intent: str | None = None,
        entities: list[str] | None = None,
    ) -> IntentResult:
        raw = (text or "").strip()
        norm = _normalize_text(raw)
        # 人工改判优先（前端显式指定，任意阶段可覆盖自动识别）
        if force_intent:
            try:
                intent = Intent(force_intent)
            except ValueError:
                intent = Intent.OTHER
            return IntentResult(
                intent=intent, confidence=1.0, is_strong=True,
                matched=["force_intent"], entities=entities or [],
                candidates=[(intent.value, 1.0)], raw=raw,
                trace={"reason": "force_intent"},
            )

        neg_spans = _negation_spans(norm)
        scores, matched = self._score(norm, neg_spans)
        candidates = sorted(
            ((k.value, v) for k, v in scores.items()),
            key=lambda kv: kv[1], reverse=True,
        )
        if scores:
            top, top_score = max(scores.items(), key=lambda kv: kv[1])
            sorted_scores = sorted(scores.values(), reverse=True)
            runner_up = sorted_scores[1] if len(sorted_scores) > 1 else 0.0
        else:
            # 规则零命中：降级 OTHER，但仍交 ML 第二票尝试接管
            top, top_score = Intent.OTHER, 0.0
            runner_up = 0.0

        # 优势式置信度：信号强度 + 领先第二名的优势
        strength = min(top_score / SCORE_SAT, 1.0)
        dominance = min((top_score - runner_up) / MARGIN_SAT, 1.0)
        confidence = min(0.98, 0.50 + W_STR * strength + W_DOM * dominance)
        # 强意图需同时满足：信号下限 + 优势下限 + 置信度下限
        is_strong = (
            top_score >= STRONG_SCORE_FLOOR
            and dominance >= STRONG_DOMINANCE_FLOOR
            and confidence >= STRONG_THRESHOLD
        )
        trace: dict = {
            "normalized": norm,
            "negation": [{"start": s, "end": e} for s, e in neg_spans],
            "scores": {k.value: round(v, 2) for k, v in scores.items()},
            "strength": round(strength, 2),
            "dominance": round(dominance, 2),
            "runner_up": round(runner_up, 2),
        }

        # ML 第二票：仅在规则弱（top_score < ML_OVERRIDE_FLOOR）且 ML 高置信时接管；
        # 同向时小幅加成。规则零命中也走此路径，让 ML 有机会兜底。
        if self.classifier is not None:
            try:
                ml_intent_val, ml_prob = self.classifier(raw)
                ml_intent = Intent(ml_intent_val)
            except Exception:
                ml_intent, ml_prob = None, 0.0
            if ml_intent is not None:
                ml_agrees = ml_intent == top
                trace["ml"] = {"intent": ml_intent.value, "prob": round(ml_prob, 2),
                               "agrees": ml_agrees, "overrode": False}
                if (not ml_agrees) and ml_prob >= ML_OVERRIDE_PROB \
                        and top_score < ML_OVERRIDE_FLOOR:
                    # 规则弱（< 1.5，约零命中或单个关键词）且 ML 高置信 → ML 接管
                    top = ml_intent
                    confidence = min(0.95, 0.55 + 0.40 * ml_prob)
                    is_strong = ml_prob >= 0.8
                    trace["ml"]["overrode"] = True
                    candidates = [(top.value, round(confidence, 2)), *candidates]
                elif ml_agrees and ml_prob >= 0.7:
                    confidence = min(0.98, confidence + 0.04)

        return IntentResult(
            intent=top,
            confidence=round(confidence, 2),
            is_strong=is_strong,
            matched=matched,
            entities=entities or [],
            candidates=candidates,
            raw=raw,
            trace=trace,
        )


# ─────────────────────────────────────────────────────────────
# 7. 实体抽取（知识底座解耦：也可由调用方用 KG.match_node 提供）
# ─────────────────────────────────────────────────────────────

# 通用课程术语表（与各 KG 关键词对齐的公共子集；完整抽取应由 KG 提供）
_COURSE_TERMS = (
    "进程", "线程", "并发", "同步", "锁", "信号量", "死锁", "内存", "虚拟",
    "分页", "页面", "置换", "文件", "调度", "临界区", "互斥", "PCB", "中断",
    "死循环", "缺页", "页表",
)


def extract_entities(text: str) -> list[str]:
    """从文本抽取已知课程术语（无 KG 场景的轻量抽取）。

    > 完整实体应优先由知识图谱提供（`kg.match_node(text)`），本函数只做通用兜底。
    """
    norm = _normalize_text(text)
    return [t for t in _COURSE_TERMS if t.lower() in norm]


# ─────────────────────────────────────────────────────────────
# 8. 模块级入口（默认单例）
# ─────────────────────────────────────────────────────────────

_default_classifier: IntentClassifier | None = None


def get_classifier() -> IntentClassifier:
    """获取默认分类器（惰性单例；注入 ML 模型时请自行构造 IntentClassifier）。"""
    global _default_classifier
    if _default_classifier is None:
        _default_classifier = IntentClassifier()
    return _default_classifier


def recognize_intent(
    text: str,
    force_intent: str | None = None,
    entities: list[str] | None = None,
) -> IntentResult:
    """识别学生消息意图（Agent 处理的第一层）。

    Args:
        text: 学生消息原文。
        force_intent: 人工改判——前端显式指定意图（'tutor'/'practice'/...），直接覆盖。
        entities: 已抽取的实体（通常由调用方传 `kg.match_node(text)` 结果）；
                  为空时用内置术语表兜底抽取。
    """
    if entities is None:
        entities = extract_entities(text)
    return get_classifier().predict(text, force_intent=force_intent, entities=entities)


__all__ = [
    "Intent",
    "INTENT_LABELS",
    "INTENT_RULES",
    "IntentRule",
    "IntentResult",
    "IntentClassifier",
    "extract_entities",
    "recognize_intent",
    "get_classifier",
    "STRONG_THRESHOLD",
    "STRONG_SCORE_FLOOR",
    "STRONG_DOMINANCE_FLOOR",
    "SCORE_SAT",
    "MARGIN_SAT",
]
