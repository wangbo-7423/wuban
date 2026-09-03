"""《操作系统》知识图谱（原型课程）。"""
from __future__ import annotations

from .base import KGNode, KeywordMatchMixin


class OSGraph(KeywordMatchMixin):
    course_id = "os"
    course_name = "操作系统"
    subject = "计算机"

    _keywords = {
        "process": ("进程", "线程"),
        "sync": ("并发", "同步", "锁", "信号量"),
        "deadlock": ("死锁",),
        "mm": ("内存", "虚拟"),
        "paging": ("分页", "页面", "置换"),
        "fs": ("文件", "inode"),
    }

    def nodes(self) -> dict[str, KGNode]:
        return {
            "process": KGNode("process", "进程管理", 2, [], False, "进程/线程/状态机"),
            "thread": KGNode("thread", "线程", 3, ["process"], False, "线程与并发模型"),
            "sync": KGNode("sync", "并发与同步", 4, ["thread"], True, "锁/信号量/死锁"),
            "deadlock": KGNode("deadlock", "死锁", 4, ["sync"], True, "四个必要条件与预防"),
            "mm": KGNode("mm", "内存管理", 3, [], True, "虚拟内存/分页"),
            "paging": KGNode("paging", "分页与页面置换", 4, ["mm"], True, "页表/换入换出"),
            "fs": KGNode("fs", "文件系统", 3, [], False, "inode/目录/文件"),
        }

    def strategy_weights(self) -> dict[str, float]:
        # 抽象概念多 → 类比/可视化 权重最高
        return {"类比": 1.0, "可视化": 1.0, "分解": 0.8, "反例": 0.6}
