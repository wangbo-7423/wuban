"""意图识别自检（无需 pytest，直接 `python -m app.agent._intent_selftest` 运行）。

把「规则即配置」易回退的边界固化成断言：改了 INTENT_RULES / 调参后跑一次即可
快速发现回归。每条用例 = (文本, 期望意图, 期望是否强意图[None=不校验])。
"""
from __future__ import annotations

from app.agent.intent import Intent, recognize_intent

CASES: list[tuple[str, Intent, bool | None]] = [
    # 否定感知
    ("我不想练习，我想让你解释一下死锁", Intent.TUTOR, False),
    ("我不是要练习，是要问个概念", Intent.TUTOR, None),
    # 正则收紧：避免「做错了一道题」被 practice 抢
    ("我做错了一道题", Intent.FEEDBACK, False),
    # 避免重复计分膨胀
    ("为什么为什么为什么", Intent.TUTOR, False),
    ("为什么", Intent.TUTOR, False),
    # 过松正则误判修正
    ("给我讲讲这道题为什么错", Intent.TUTOR, True),
    # intake / self_assess 边界
    ("我掌握得不错", Intent.SELF_ASSESS, True),
    ("我零基础", Intent.INTAKE, True),
    # 文本归一化
    ("我 不 懂", Intent.TUTOR, False),
    ("PCB是什么", Intent.TUTOR, None),
    # 各意图主路径
    ("帮我出一道题", Intent.PRACTICE, True),
    ("我要学习计划", Intent.PROPOSE_PLAN, True),
    ("考了80分", Intent.FEEDBACK, True),
    ("要挂科了吗", Intent.WARN, True),
    ("什么是死锁", Intent.TUTOR, True),
    ("你好呀", Intent.OTHER, False),
    ("", Intent.OTHER, False),
    ("   ", Intent.OTHER, False),
]


def run() -> int:
    fails: list[str] = []
    for text, exp_intent, exp_strong in CASES:
        r = recognize_intent(text)
        strong_ok = exp_strong is None or r.is_strong == exp_strong
        if r.intent != exp_intent or not strong_ok:
            fails.append(
                f"  FAIL: {text!r} -> {r.intent.value}(exp {exp_intent.value}) "
                f"conf={r.confidence} strong={r.is_strong}(exp {exp_strong})"
            )
    if not fails:
        print(f"intent self-check OK ({len(CASES)} cases)")
        return 0
    print("intent self-check FAILURES:")
    for f in fails:
        print(f)
    return 1


if __name__ == "__main__":
    raise SystemExit(run())
