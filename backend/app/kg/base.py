"""知识的可插拔底座：知识图谱接口（对齐 `docs/01-系统架构与Agent设计.md` §6）。

每个工科课程一张知识图谱（节点/前置/难度/策略权重）。换课程 = 换 KG，Agent 框架不变。
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


class KnowledgeGraph(Protocol):
    course_id: str
    course_name: str
    subject: str

    def nodes(self) -> dict[str, KGNode]:
        ...

    def strategy_weights(self) -> dict[str, float]:
        """各引导策略在本学科的权重（类比/分解/反例/可视化）。"""
        ...
