"""《信号与系统》知识图谱（可迁移性示例）。"""
from __future__ import annotations

from .base import KGNode, KeywordMatchMixin


class SignalSystemGraph(KeywordMatchMixin):
    course_id = "signals"
    course_name = "信号与系统"
    subject = "电子信息"

    _keywords = {
        "time_domain": ("时域", "离散", "连续"),
        "freq_domain": ("频域", "傅里叶", "频谱"),
        "convolution": ("卷积",),
        "sampling": ("采样", "奈奎斯特"),
    }

    def nodes(self) -> dict[str, KGNode]:
        return {
            "time_domain": KGNode("time_domain", "时域分析", 2, [], False, "连续/离散信号"),
            "freq_domain": KGNode("freq_domain", "频域分析", 4, ["time_domain"], True, "傅里叶变换"),
            "convolution": KGNode("convolution", "卷积", 4, ["time_domain"], True, "卷积积分/和"),
            "sampling": KGNode("sampling", "采样定理", 4, ["freq_domain"], True, "奈奎斯特"),
        }

    def strategy_weights(self) -> dict[str, float]:
        # 结构复杂/数学性强 → 分解+可视化 高
        return {"分解": 1.0, "可视化": 1.0, "类比": 0.7, "反例": 0.5}
