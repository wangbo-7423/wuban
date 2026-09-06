"""揭示式讲解（表里反差）冒烟：验证场景指南注入 + GLM 实际讲解效果。

用法：cd backend && .venv/Scripts/python.exe -m app.agent.scripts.reveal_sanity
    （需要 GLM_API_KEY；两次真实调用，花费很小）

验证三件事：
1. 概念类问题路由命中后，guide_concept.md（含表里反差指南）确实随装配注入动态上下文；
2. 有「表层无辜、深层离谱」结构的概念（0.1+0.2）：讲解是否用了「表层口吻讲深层」的
   揭示式结构，且不点破幽默；
3. 没有双层结构的概念（进程 vs 线程）：是否克制、不硬造反差（2、3 需人工评估输出文本）。
"""
from __future__ import annotations

import logging
import sys

logging.basicConfig(level=logging.WARNING)

from app.agent import AgentOrchestrator  # noqa: E402
from app.agent.intent import build_scene_context  # noqa: E402

QUESTIONS = [
    "0.1 + 0.2 为什么不等于 0.3？是不是我电脑坏了？",
    "进程和线程有啥区别？",
]


def main() -> int:
    ok = True
    for q in QUESTIONS:
        allowed, scene_text = build_scene_context(q)
        injected = "表里反差" in scene_text
        print(f"\n=== {q}")
        print(f"[路由] tools={allowed} 表里反差指南注入={'是' if injected else '否'}")
        if not injected:
            print("[FAIL] guide_concept.md 未随场景装配注入")
            ok = False
        orch = AgentOrchestrator(dynamic_context=scene_text, allowed_tools=allowed)
        r = orch.run(user_text=q)
        print(f"[讲解] prompt_tokens={r.prompt_tokens}\n{r.text}")
    if ok:
        print(
            "\nSANITY OK —— 请人工评估：第一段应有「表层口吻讲深层」的反差且不点破笑点；"
            "第二段应克制、不硬造幽默"
        )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
