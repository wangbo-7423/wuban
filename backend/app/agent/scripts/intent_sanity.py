"""意图路由（Select 策略·Skills 路由）冒烟：真实调 GLM 分类 + 工具装配检查。

用法：cd backend && .venv/Scripts/python.exe -m app.agent.scripts.intent_sanity
    （需要 GLM_API_KEY；分类失败路径用空消息覆盖，不依赖网络）

验证四件事：
1. 典型消息被分到正确意图；
2. 意图 → 工具白名单映射 + 过滤后 schema 名单真实存在于注册表；
3. 纯图片/空消息 → "all" 全量（不冒险路由）；
4. 过滤后的 tool_schemas 数量 < 全量（装配确实收窄了候选面）。
"""
from __future__ import annotations

import logging
import sys

logging.basicConfig(level=logging.INFO)

from app.agent.intent import (  # noqa: E402
    allowed_tool_names,
    build_scene_context,
    build_tool_context,
    classify_intent,
)
from app.agent.system import load_scene_guide, load_system_prompt  # noqa: E402
from app.agent.tools import tool_schemas  # noqa: E402
from app.skills.registry import get_skill  # noqa: E402

_CASES = [
    ("帮我算一下 x^2*sin(x) 从 0 到 pi 的定积分，要过程", "math"),
    ("我这段 PID 仿真代码报 KeyError 了，帮我跑一下看看哪里错了", "engineering"),
    ("拉普拉斯变换和傅里叶变换到底有什么区别？为什么要引入收敛域", "concept"),
    ("最近怎么复习比较好啊，期末有点慌", "chat"),
]


def _schema_names(schemas: list[dict]) -> list[str]:
    return [str((s.get("function") or {}).get("name")) for s in schemas]


def main() -> int:
    fails = 0
    for text, expected in _CASES:
        got = classify_intent(text)
        ok = got == expected
        print(f"[1] {expected:<11} <- {text[:24]}…  => {got} {'OK' if ok else 'FAIL'}")
        if not ok:
            fails += 1

    # 2) math 意图：白名单过滤后 schema 与名单一致且真实可执行
    allowed = allowed_tool_names("math")
    schemas = tool_schemas(set(allowed or []))
    names = _schema_names(schemas)
    print("[2] math 装配:", names)
    for n in allowed or []:
        if n == "calculator":
            continue  # sympy 可用时未注册，自然缺席
        if n not in names:
            print(f"[FAIL] {n} 在白名单但未装配")
            fails += 1

    all_names = _schema_names(tool_schemas())
    print(f"[3] 全量 {len(all_names)} 个: {all_names}")
    if len(schemas) >= len(all_names):
        print("[FAIL] 过滤后候选面没有收窄")
        fails += 1

    # 3) 空消息 → 全量（不路由）
    if classify_intent("") != "all" or classify_intent("   ") != "all":
        print("[FAIL] 空消息应降级为 all")
        fails += 1

    # 4) build_scene_context 端到端：装配说明 + 场景指南（渐进式披露）
    allowed2, note = build_scene_context("帮我算 ∫x·e^x dx")
    print("[4] 端到端 allowed:", allowed2, "| 注入文本头:", note[:60], "…")
    if not note or not allowed2:
        print("[FAIL] 端到端装配异常")
        fails += 1
    if "calculus" not in note or "务必调用" not in note:
        print("[FAIL] math 场景指南未随动态上下文注入（渐进式披露失效）")
        fails += 1

    # 5) chat/all 不应带指南；提示词真源可加载
    _, chat_note = build_scene_context("在吗")
    if "场景指南" in chat_note and "数学" in chat_note:
        print("[FAIL] chat 场景不应注入数学指南")
        fails += 1
    sys_prompt = load_system_prompt()
    if len(sys_prompt) < 500 or "AI 理工科伴学" not in sys_prompt:
        print("[FAIL] Agent.md 主提示词加载异常")
        fails += 1
    if load_scene_guide("") != "":
        print("[FAIL] 空 guide 声明应返回空串")
        fails += 1
    print("[5] 提示词真源加载 OK：Agent.md", len(sys_prompt), "字符")

    if fails:
        print(f"SANITY FAIL（{fails} 处）")
        return 1
    print("SANITY OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
