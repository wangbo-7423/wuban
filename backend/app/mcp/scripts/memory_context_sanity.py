"""分层记忆注入冒烟：不走 HTTP/DB/LLM，直接测 L0 常驻层 + L1 检索层。

用法：cd backend && .venv/Scripts/python.exe -m app.mcp.scripts.memory_context_sanity

不调 GLM 抽取——用 apply_extraction 直接种图（apply_extraction 本身是纯合并逻辑），
只验证「读路径」的分层裁剪是否符合上下文工程预期：
    L0 只含 偏好/目标/误区，不含概念；
    L1 只回当前消息命中的概念 + 一步邻接关系，无关话题返回 None。
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO)

BACKEND = Path(__file__).resolve().parents[3]
os.environ.setdefault("MCP_MEMORY_DIR", str(BACKEND / "data" / "memory" / "_sanity_context"))

# 配置在 import app 模块前就位（settings 是 lru_cache 单例）
from app.core.config import settings  # noqa: E402
settings.mcp_memory_dir = str(BACKEND / "data" / "memory" / "_sanity_context")

from app.mcp import bridge  # noqa: E402
from app.services import memory_service  # noqa: E402

USER = "_sanity_context_user"

_SEED = {
    "entities": [
        {"name": "傅里叶变换", "entityType": "概念", "aliases": ["FFT", "傅氏变换"],
         "observations": ["学生初次接触该概念", "已能说明物理意义"]},
        {"name": "卷积定理", "entityType": "概念", "observations": ["已能独立说明时频对应"]},
        {"name": "傅里叶级数", "entityType": "概念", "observations": ["学过，限于周期信号"]},
        {"name": "混淆卷积与相关", "entityType": "误区", "observations": ["与互相关混淆过，已纠正"]},
        {"name": "偏好图示讲解", "entityType": "偏好", "observations": ["先看图再看重公式"]},
        {"name": "备考信号系统期末", "entityType": "目标", "observations": []},
    ],
    "relations": [
        {"from": "傅里叶变换", "to": "卷积定理", "relationType": "相关"},
        {"from": "傅里叶级数", "to": "傅里叶变换", "relationType": "前置依赖"},
        {"from": "傅里叶变换", "to": "备考信号系统期末", "relationType": "属于目标"},
    ],
}


async def main() -> int:
    if not bridge.available():
        print("[FAIL] bridge 不可用")
        return 1
    bridge.delete_all(USER)

    stats = memory_service.apply_extraction(USER, _SEED)
    print("[0] seed stats:", stats)
    if stats["entities_created"] != 6 or stats["relations_created"] != 3:
        print("[FAIL] 种图结果不符预期")
        return 1

    # 1) L0 常驻层：偏好/目标/误区，绝不含概念
    l0 = memory_service.get_persistent_digest(USER)
    print("[1] L0:\n" + (l0 or "(None)"))
    if not l0 or "偏好" not in l0 or "误区" not in l0 or "已纠正" not in l0:
        print("[FAIL] L0 缺偏好/目标/误区或未标注已纠正")
        return 1
    if "傅里叶变换" in l0:
        print("[FAIL] L0 混入了概念层内容")
        return 1

    # 2) L1 命中：问傅里叶变换 → 命中它 + 一步邻接（含未命中的 傅里叶级数）
    l1_hit = memory_service.get_relevant_digest(USER, "再讲讲傅里叶变换的物理意义可以吗")
    print("[2] L1(命中):\n" + (l1_hit or "(None)"))
    if not l1_hit or "傅里叶变换" not in l1_hit:
        print("[FAIL] L1 未命中傅里叶变换")
        return 1
    if "卷积定理" not in l1_hit or "傅里叶级数" not in l1_hit:
        print("[FAIL] L1 缺一步邻接关系")
        return 1
    if "卷积定理" in l1_hit.split("### 相邻关系")[0]:
        print("[FAIL] 未命中的概念不应出现在命中列表（只应出现在邻接关系里）")
        return 1

    # 3) L1 不命中：无关话题 → None，本轮不注入概念层
    l1_miss = memory_service.get_relevant_digest(USER, "PID 控制器怎么调参")
    print("[3] L1(无关话题):", l1_miss)
    if l1_miss is not None:
        print("[FAIL] 无关话题不应命中")
        return 1

    # 3.5) L1 别名通道：学生说 FFT（图谱记的是「傅里叶变换」）也应命中
    l1_alias = memory_service.get_relevant_digest(USER, "帮我看看这段 FFT 代码哪里有问题")
    print("[3.5] L1(别名 FFT):", (l1_alias or "(None)")[:200])
    if not l1_alias or "傅里叶变换" not in l1_alias:
        print("[FAIL] 别名通道未命中 FFT → 傅里叶变换")
        return 1
    if "别名" in l1_alias:
        print("[FAIL] 别名条目不应出现在注入文本里")
        return 1

    # 4) 全量视图仍可用（API/调试用）
    full = memory_service.get_digest(USER)
    if not full or "傅里叶变换" not in full:
        print("[FAIL] get_digest 全量视图异常")
        return 1
    print("[4] get_digest 字符数:", len(full), "| L0:", len(l0), "| L1命中:", len(l1_hit))

    bridge.delete_all(USER)
    shutil.rmtree(settings.mcp_memory_dir, ignore_errors=True)
    print("SANITY OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
