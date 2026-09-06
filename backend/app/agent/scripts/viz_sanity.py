"""交互可视化冒烟：走真实 orchestrator 验证 make_visual 调用与 interactive 卡注入。

用法：cd backend && .venv/Scripts/python.exe -m app.agent.scripts.viz_sanity
    （需要 GLM_API_KEY；两次真实调用，花费很小）

验证三件事：
1. 概念/工程场景里模型会主动调 make_visual（工具描述与触发策略是否对齐）；
2. format_cards 后 interactive 卡确定性附上（HTML 不进模型上下文，只有小结）；
3. payload 里 probe / meta.source=template 就位。
"""
from __future__ import annotations

import logging
import sys

logging.basicConfig(level=logging.WARNING)

from app.agent import AgentOrchestrator  # noqa: E402
from app.agent.cards import format_cards  # noqa: E402

QUESTION = "能不能让我直观感受一下 PID 三个参数分别有什么影响？最好能自己动手试。"


def main() -> int:
    orch = AgentOrchestrator(
        dynamic_context=(
            "当前课程：autocontrol\n本轮场景：工程实操。"
            "已按场景装配工具：kg_lookup、memory_search、scaffold_state、mastery_evidence、make_visual。"
        ),
        allowed_tools=["kg_lookup", "memory_search", "scaffold_state", "mastery_evidence", "make_visual"],
        temperature=0.5,
    )
    r = orch.run(user_text=QUESTION)
    print(f"[1] 工具调用: {[tc.get('tool_name') for tc in r.tool_calls or []]}")

    cards = format_cards(r, user_text=QUESTION, course_id="autocontrol")
    print(f"[2] 卡片类型: {[c.card_type for c in cards]}")
    viz = [c for c in cards if c.card_type == "interactive"]
    if not viz:
        print("SANITY INCOMPLETE：模型未调 make_visual 或注入失败（人工判断）")
        return 0
    blk = viz[0].payload.interactive
    print(
        f"[3] 实验卡: template={blk.meta.get('template')} "
        f"bytes={len(blk.html.encode('utf-8'))} "
        f"probe={blk.probe.question if blk.probe else None}"
    )
    ok = blk.meta.get("source") == "template" and blk.probe is not None
    print("SANITY OK" if ok else "SANITY INCOMPLETE（payload 结构不符预期）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
