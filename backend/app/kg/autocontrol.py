"""《自动控制原理》知识图谱（可迁移性示例）。"""
from __future__ import annotations

from .base import KGNode, KeywordMatchMixin


class AutoControlGraph(KeywordMatchMixin):
    course_id = "autocontrol"
    course_name = "自动控制原理"
    subject = "自动化"

    _keywords = {
        "tf": ("传递函数", "建模"),
        "stability": ("稳定", "极点", "判据"),
        "rootlocus": ("根轨迹",),
        "feedback": ("反馈", "闭环", "开环"),
    }

    def nodes(self) -> dict[str, KGNode]:
        return {
            "feedback": KGNode("feedback", "反馈控制", 2, [], False, "闭环/开环"),
            "tf": KGNode("tf", "传递函数", 3, [], True, "系统建模"),
            "stability": KGNode("stability", "稳定性", 4, ["tf"], True, "极点/稳定性判据"),
            "rootlocus": KGNode("rootlocus", "根轨迹", 4, ["stability"], True, "根轨迹法"),
        }

    def strategy_weights(self) -> dict[str, float]:
        # 抽象/动态系统 → 类比+可视化 高
        return {"类比": 1.0, "可视化": 1.0, "分解": 0.6, "反例": 0.7}
