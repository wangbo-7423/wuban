""""认知引导"的分层策略库（类比/分解/反例/可视化）。

策略是通用的，但**权重按学科**配置（见各 KG 的 `strategy_weights`）。
对齐 docs/AI工科认知引导框架-定位与架构.md §6。
"""
from __future__ import annotations

STRATEGY_NAMES = ["类比", "分解", "反例", "可视化"]

# 每种策略的说明与能力（供生成/展示用）
STRATEGY_META = {
    "类比": "把陌生概念映射到熟悉的经验/已学概念，建立图式",
    "分解": "把复杂问题拆成简单子问题，降低认知负荷",
    "反例": "用一个错误案例帮你建立概念边界",
    "可视化": "把抽象概念转成图形/流程/映射表",
}


def select_strategy(
    node_abstract: bool,
    card_point: str,
    weights: dict[str, float],
    top_k: int = 2,
) -> list[str]:
    """根据「认知卡点」确定策略候选，再按学科权重排序。

    card_point: '前置缺失' | '概念混淆' | '负荷过高' | '已理解'
    """
    if card_point == "前置缺失":
        pool = ["可视化", "分解"]          # 用可视化/分解建立前置图式
    elif card_point == "负荷过高":
        pool = ["分解", "可视化"]          # 拆解、可视化降低负荷
    elif card_point == "概念混淆":
        pool = ["反例", "类比"]            # 反例定边界、类比正迁移
    else:
        pool = ["类比", "可视化"]          # 已理解：深化理解/迁移

    # 依据学科权重排序，取前 top_k
    scored = sorted(pool, key=lambda s: weights.get(s, 0.5), reverse=True)
    return scored[:top_k]


def strategy_hint(strategy: str) -> str:
    return STRATEGY_META.get(strategy, "")
