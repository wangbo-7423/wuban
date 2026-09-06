"""记忆服务冒烟：不走 HTTP/DB，直接测 extract_and_write + digest + search。

用法：cd backend && .venv/Scripts/python.exe -m app.mcp.scripts.memory_extract_sanity
覆盖信号：常规抽取（概念/偏好）+ 去重 + 新增的 兴趣实体 / 自发关联关系 /
表里反差探针 observation（抽取是概率性的，MISS 人工判断，不算硬失败）。
"""
from __future__ import annotations

import asyncio
import logging
logging.basicConfig(level=logging.INFO)
import logging
logging.basicConfig(level=logging.INFO)
import os
import shutil
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[3]
os.environ.setdefault("MCP_MEMORY_DIR", str(BACKEND / "data" / "memory" / "_sanity_extract"))

# 配置在 import app 模块前就位（settings 是 lru_cache 单例）
from app.core.config import settings  # noqa: E402
settings.mcp_memory_dir = str(BACKEND / "data" / "memory" / "_sanity_extract")

from app.mcp import bridge  # noqa: E402
from app.services import memory_service  # noqa: E402

USER = "_sanity_extract_user"


async def main() -> int:
    if not bridge.available():
        print("[FAIL] bridge 不可用：", bridge.available.__module__)
        return 1
    bridge.delete_all(USER)
    print("[0] available=True, 记忆文件:", settings.mcp_memory_dir)

    # 1) 单轮抽取写入
    stats = memory_service.extract_and_write(
        USER,
        "signals",
        "我一直搞不懂傅里叶变换到底为什么要分解成正弦波，还有卷积定理是怎么把它和频域乘法联系起来的？我平时喜欢先看图再看重公式。",
        "好问题！我们分两步：先把信号看成不同频率正弦波的叠加（这正是傅里叶变换的直觉）……"
        "你有没有想过为什么时域卷积等价于频域乘积？可以从正弦波的正交性想起。",
    )
    print("[1] extract stats:", stats)
    if not stats:
        print("[FAIL] 抽取没有产出")
        return 1

    # 1.5) 新信号：揭示式讲解后学生接住梗 + 主动类比（兴趣 / 自发关联 / 探针）
    stats_probe = memory_service.extract_and_write(
        USER, "general",
        "哈哈哈所以我电脑从来没错，它只是认真地给我找一个最接近的数？"
        "诶这不就是食堂打饭吗——师傅勺子一抖，给你打一勺『最接近二两』的量，"
        "屏幕上还写着二两。我觉得这个太有意思了",
        "0.1 就是 0.1，小学都这么教的。但机器里没有 0.1 这个数——它只是从二进制里挑了个"
        "离 0.1 最近的邻居凑合给你用，屏幕上那个 0.1 是它替你修饰过的样子。"
        "所以 0.1 + 0.2 = 0.30000000000000004，它算的从来不是你以为的那两个数。",
    )
    print("[1.5] probe extract:", stats_probe)

    # 2) 重复抽取一轮（去重路径）
    stats2 = memory_service.extract_and_write(
        USER, "signals",
        "那 PID 控制器和傅里叶分析有关系吗？",
        "PID 是时域反馈控制方法，傅里叶分析帮你理解系统的频率响应……",
    )
    print("[2] second extract:", stats2)

    # 3) 读回图谱
    g = bridge.read_graph(USER)
    ents = [(e["name"], e["entityType"], len(e.get("observations", []))) for e in g.get("entities", [])]
    print("[3] entities:", ents)
    print("[3] relations:", [(r["from"], r["relationType"], r["to"]) for r in g.get("relations", [])])
    if not ents:
        print("[FAIL] 图谱为空")
        return 1

    # 3.5) 新信号读回校验（概率性，MISS 人工判断）
    all_obs = [o for e in g.get("entities", []) for o in (e.get("observations") or [])]
    types = {e.get("entityType") for e in g.get("entities", [])}
    rel_types = {r.get("relationType") for r in g.get("relations", [])}
    for label, hit in {
        "兴趣实体": "兴趣" in types,
        "自发关联关系": "自发关联" in rel_types,
        "表里反差探针": any("表里反差探针" in o for o in all_obs),
    }.items():
        print(f"[{'PASS' if hit else 'MISS'}] {label}")

    # 4) 摘要注入
    digest = memory_service.get_digest(USER)
    print("[4] digest:\n" + (digest or "(None)"))

    # 5) agent 检索
    hit = memory_service.search_for_agent(USER, "傅里叶")
    print("[5] search 傅里叶 ->", [e["name"] for e in hit["entities"]])

    bridge.delete_all(USER)
    shutil.rmtree(settings.mcp_memory_dir, ignore_errors=True)
    print("SANITY OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
