""""认知诊断"：连接知识库(KG)与对话生成的桥梁。

评估学生对当前知识点的掌握与认知卡点，输出推荐引导策略。对齐 docs/AI工科认知引导框架-定位与架构.md §5。
"""
from __future__ import annotations

from dataclasses import dataclass

from app.kg.base import KGNode

CARD_POINTS = {
    "前置缺失": "你缺了这个知识点的前置基础",
    "负荷过高": "这个点可能一次装太多，需要拆开",
    "概念混淆": "这个概念容易和别的混淆，需要一个反例来界定",
    "已理解": "你理解得不错，我们往深走一步",
}


@dataclass
class Diagnosis:
    node: KGNode
    mastery: float
    card_point: str
    missing: list[str]
    message: str


def diagnose(node: KGNode, mastery: float, mastered_prefix: list[str]) -> Diagnosis:
    """判断卡点。

    - 前置缺失：有前置节点未掌握 → 可视化/分解
    - 掌握度低 + 抽象 → 概念混淆 → 反例/类比
    - 掌握度中 → 负荷过高 → 分解/可视化
    - 掌握度高 → 已理解
    """
    missing = [p for p in node.prerequisites if p not in mastered_prefix]
    if missing:
        card_point = "前置缺失"
    elif mastery < 0.4:
        card_point = "概念混淆" if node.abstract else "负荷过高"
    elif mastery < 0.7:
        card_point = "负荷过高"
    else:
        card_point = "已理解"

    return Diagnosis(
        node=node,
        mastery=mastery,
        card_point=card_point,
        missing=missing,
        message=CARD_POINTS[card_point],
    )
