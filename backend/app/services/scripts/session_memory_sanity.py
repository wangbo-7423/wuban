"""会话级 Write（滚动摘要 + Scratchpad）冒烟：不走 DB，测纯逻辑 + 可选 LLM 压缩。

用法：cd backend && .venv/Scripts/python.exe -m app.services.scripts.session_memory_sanity
    （LLM 压缩部分需要 GLM_API_KEY；没有时跳过，不影响其余断言）

验证三件事：
1. Scratchpad 启发式：question/practice/math 卡能提取出正确的「进行中/待回应」状态；
2. render_session_context：摘要 + 草稿纸 → 注入文本；空输入 → 空串；
3. 滚动压缩（可选，需 GLM）：旧摘要 + 新对话 → 合并后的单份摘要。
"""
from __future__ import annotations

import logging
import sys
from types import SimpleNamespace

logging.basicConfig(level=logging.INFO)

from app.services.session_memory import (  # noqa: E402
    _compress_llm,
    _scratchpad_from_messages,
    render_session_context,
)


def _msg(role: str, text: str = "", card_type: str = "text", payload: dict | None = None):
    return SimpleNamespace(role=role, text=text, card_type=card_type, payload=payload)


def main() -> int:
    # 1) 空会话（只有用户消息，AI 还没开口）→ None
    pad = _scratchpad_from_messages([_msg("user", "傅里叶变换是干嘛的？")])
    if pad is not None:
        print("[FAIL] 开场会话不应产生草稿纸:", pad)
        return 1

    # 2) question 卡 → topic + 进度 + 待回应
    msgs = [
        _msg("user", "我一直搞不懂卷积定理为什么要时域卷积频域相乘"),
        _msg("assistant", "我们先从正交性想起……", card_type="understand"),
        _msg("assistant", "你有没有想过：如果把信号换成非周期的会怎样？", card_type="question",
             payload={"stem": "如果把信号换成非周期的会怎样？"}),
    ]
    pad = _scratchpad_from_messages(msgs)
    print("[1] question 卡草稿纸:", pad)
    if not pad or "卷积定理" not in pad.get("topic", ""):
        print("[FAIL] topic 应取学生最后一条消息")
        return 1
    if "反问" not in pad.get("pending", ""):
        print("[FAIL] question 卡应产生「等学生回应反问」")
        return 1

    # 3) math 卡 → 步数进度；text 卡无问句 → 无 pending
    msgs = [
        _msg("user", "帮我算 ∫x²dx"),
        _msg("assistant", "分步来。", card_type="math", payload={
            "math": {"problem": "∫x²dx", "steps": [
                {"expr": "x^3/3", "note": "幂函数积分公式"},
                {"expr": "+C", "note": "记得常数项"},
            ], "answer": "x^3/3 + C"},
        }),
    ]
    pad = _scratchpad_from_messages(msgs)
    print("[2] math 卡草稿纸:", pad)
    if not pad or "第 2 步" not in pad.get("progress", ""):
        print("[FAIL] math 卡应报「已讲到第 2 步」")
        return 1
    if pad.get("pending"):
        print("[FAIL] math 卡无问句不应有 pending:", pad["pending"])
        return 1

    # 4) practice 卡 → 预测验证进行中
    msgs = [
        _msg("user", "这段仿真代码帮我看看"),
        _msg("assistant", "先猜猜输出", card_type="practice", payload={"stem": "这段代码会输出什么？"}),
    ]
    pad = _scratchpad_from_messages(msgs)
    print("[3] practice 卡草稿纸:", pad)
    if not pad or "预测" not in pad.get("pending", ""):
        print("[FAIL] practice 卡应产生「预测→验证进行中」")
        return 1

    # 5) 渲染：摘要 + 草稿纸
    text = render_session_context(
        "学生在备考信号系统期末，已讲完傅里叶级数到傅里叶变换的过渡，卡点在频域直觉。",
        pad,
    )
    print("[4] 注入文本:\n" + text)
    for mark in ("早期进展摘要", "任务状态", "预测", "备考信号系统期末"):
        if mark not in text:
            print(f"[FAIL] 注入文本缺少「{mark}」")
            return 1

    # 6) 全空 → 空串
    if render_session_context(None, None) != "":
        print("[FAIL] 空输入应渲染为空串")
        return 1

    # 7) 滚动压缩（可选：需要 GLM_API_KEY）
    try:
        merged = _compress_llm(
            old_summary="学生在学傅里叶变换，已讲正弦波分解直觉。",
            transcript=(
                "学生：那卷积定理是怎么把时域卷积变成频域乘法的？\n"
                "AI：从正弦波正交性出发……时域卷积等价频域乘积，你可以想想为什么。"
            ),
        )
        print("[5] LLM 压缩结果:", (merged or "(None)")[:200])
        if not merged:
            print("[FAIL] 压缩返回空")
            return 1
        if "卷积定理" not in merged:
            print("[FAIL] 压缩结果丢失新对话关键信息")
            return 1
    except Exception as e:  # noqa: BLE001
        print("[5][SKIP] LLM 压缩未验证（GLM 不可用）:", e)

    print("SANITY OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
