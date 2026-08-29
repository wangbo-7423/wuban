"""Cache 策略冒烟：走真实 orchestrator 连续两轮，验证隐式前缀缓存命中。

用法：cd backend && .venv/Scripts/python.exe -m app.agent.scripts.cache_sanity
    （需要 GLM_API_KEY；两次真实调用，花费很小）

验证三件事：
1. 生产链路（AgentOrchestrator）连续两轮调用，第二轮 cached_tokens > 0
   ——静态 system 块 + 第二条静态说明的前缀被智谱隐式缓存复用；
2. 动态上下文（课程/记忆/任务状态）变化不影响命中——它在尾部 user 消息里，
   不污染 system 前缀；
3. AgentResult 的 prompt_tokens / cached_tokens 指标可用（命中率可观测）。
"""
from __future__ import annotations

import logging
import sys
import time

logging.basicConfig(level=logging.INFO)

from app.agent import AgentOrchestrator  # noqa: E402


def main() -> int:
    # 第一轮：无历史
    orch1 = AgentOrchestrator(
        dynamic_context="当前课程：signals\n本轮场景：概念/原理理解。已按场景装配工具：kg_lookup、memory_search。",
        temperature=0.5,
    )
    r1 = orch1.run(user_text="用一句话解释什么是卷积")
    print(f"[1] 第一轮 prompt={r1.prompt_tokens} cached={r1.cached_tokens} text={r1.text[:40]!r}")
    if not r1.prompt_tokens:
        print("[FAIL] prompt_tokens 未采集")
        return 1

    time.sleep(2)  # 给隐式缓存写入留点时间

    # 第二轮：动态上下文整段变了（模拟真实场景：换了话题/记忆更新/意图不同）
    orch2 = AgentOrchestrator(
        dynamic_context=(
            "当前课程：os\n本轮场景：概念/原理理解。已按场景装配工具：kg_lookup、memory_search。\n\n"
            "## 学生长期记忆（历史对话沉淀，常驻）\n- 偏好：偏好图示讲解\n- 目标：备考操作系统期末"
        ),
        temperature=0.5,
    )
    r2 = orch2.run(history=[
        {"role": "user", "content": "用一句话解释什么是卷积"},
        {"role": "assistant", "content": r1.text},
    ], user_text="那进程和线程的区别呢？")
    rate = r2.cached_tokens / r2.prompt_tokens * 100 if r2.prompt_tokens else 0
    print(f"[2] 第二轮 prompt={r2.prompt_tokens} cached={r2.cached_tokens}（命中率 {rate:.0f}%）")

    if r2.cached_tokens <= 0:
        print("[FAIL] 第二轮未命中缓存——动态上下文是否混进了 system？")
        return 1
    if rate < 30:
        print(f"[FAIL] 命中率 {rate:.0f}% 过低，静态前缀保护可能失效")
        return 1
    print("SANITY OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
