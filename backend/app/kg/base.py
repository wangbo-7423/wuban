"""知识的可插拔底座：知识图谱接口（对齐 `docs/01-系统架构与Agent设计.md` §6）。

每个工科课程一张知识图谱（节点/前置/难度/策略权重）。换课程 = 换 KG，Agent 框架不变。

设计要点：
- `KnowledgeGraph` 是 Protocol，实现类用 duck typing；`match_node` 既有协议位置，
  也有现成的 `KeywordMatchMixin` 默认实现（三份手写重复代码已收编于此）；
- `KeywordMatchMixin.match_node` 的匹配顺序：①显式关键词表（`_keywords`）按
  **最长命中关键词优先**（「传递函数」优先于「函数」这类短词误命中）；
  ②兜底扫描节点 name / summary 子串（关键词表漏维护时仍能命中，如 os 查「页表」）；
- `KGNode.refs` 是出处锚点（"CSAPP §9.3" 这类教材章节引用，docs/11 §5）：
  kg_lookup 命中时随节点返回，AI 自然引用教材出处，零版权风险。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class KGNode:
    id: str
    name: str
    difficulty: int = 3                 # 难度层级 1~5
    prerequisites: list[str] = field(default_factory=list)  # 前置节点 id
    abstract: bool = True               # 是否抽象/难直观（影响策略倾向）
    summary: str = ""                   # 概念简介（供对话引用/证据）
    refs: list[str] = field(default_factory=list)  # 出处锚点（教材章节/权威资料）


class KeywordMatchMixin:
    """`match_node` 的默认实现：显式关键词表优先，节点名/摘要兜底。

    子类只需声明 `_keywords: dict[str, tuple[str, ...]]`（node_id → 关键词组）。
    """

    _keywords: dict[str, tuple[str, ...]]

    def match_node(self, text: str) -> str | None:
        text = (text or "").strip()
        if not text:
            return None

        # ① 关键词表：按「命中关键词个数 → 最长命中词」打分取最优。
        #    命中数多 = 更特异（"进程的页面置换" 同时命中 paging 的 页面+置换，
        #    比 process 只命中 进程 更准），不能让 dict 声明顺序决定结果。
        best: tuple[tuple[int, int], str] | None = None  # ((hits, max_len), node_id)
        for node_id, kws in self._keywords.items():
            hits = [kw for kw in kws if kw in text]
            if not hits:
                continue
            score = (len(hits), max(len(kw) for kw in hits))
            if best is None or score > best[0]:
                best = (score, node_id)
        if best:
            return best[1]

        # ② 兜底：节点名 / 摘要分段（"页表/换入换出" → 按段互查）与查询互含。
        #    关键词表漏维护（如 os 查「页表」）时仍能命中。
        for node_id, node in self.nodes().items():
            if node.name in text or text in node.name:
                return node_id
            for seg in (node.summary or "").replace("／", "/").split("/"):
                seg = seg.strip()
                if len(seg) >= 2 and seg in text:
                    return node_id
        return None


class KnowledgeGraph(Protocol):
    course_id: str
    course_name: str
    subject: str

    def nodes(self) -> dict[str, KGNode]:
        ...

    def strategy_weights(self) -> dict[str, float]:
        """各引导策略在本学科的权重（类比/分解/反例/可视化）。"""
        ...

    def match_node(self, text: str) -> str | None:
        """按学生文本匹配节点 id（推荐继承 `KeywordMatchMixin` 获得默认实现）。"""
        ...
