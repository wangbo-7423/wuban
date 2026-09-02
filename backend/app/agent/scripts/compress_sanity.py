"""工具结果压缩（Compress ①②）冒烟：纯逻辑，不调 LLM/DB。

用法：cd backend && .venv/Scripts/python.exe -m app.agent.scripts.compress_sanity

验证五件事：
1. 各类工具结果（skill 推导 / 代码执行 / KG 检索 / 非法 JSON）都能压成一行且保留关键结论；
2. 压缩率显著（对应材料「-81% Token」的量级）；
3. 旧轮压缩、当前轮完整的时序语义（compress_old_tool_results 压全部现存 tool 消息，
   orchestrator 保证它只在「新一轮 tool_calls 已到、新结果未追加」时调用）；
4. 幂等：已压缩的消息不会被二次处理；
5. tool_call_id 等消息结构字段不被破坏。
"""
from __future__ import annotations

import json
import sys

from app.agent.compress import (
    _DIGEST_MARK,
    _digest_fields_for,
    compress_old_tool_results,
    digest_tool_content,
)

_CALC_RESULT = {
    "ok": True,
    "skill": "calculus",
    "expr_latex": r"\int_0^\pi x \sin x\,dx",
    "steps": [{"expr": "x sin x = -x d(cos x)", "note": "分部积分"}] * 6,
    "answer": "π",
    "teaching_hints": "先问学生分部积分的 u/v 怎么选",
}
_RUNNER_RESULT = {
    "ok": True, "stdout": "a=1\nb=2\na b = 2\n", "stderr": "",
    "returncode": 0, "timed_out": False, "figures": [],
}
_KG_RESULT = {
    "ok": True,
    "matched": {"id": "fourier", "name": "傅里叶变换", "difficulty": 4,
                "prerequisites": ["傅里叶级数"], "summary": "把信号分解到频率域"},
    "course": "信号与系统",
}
_BAD_RESULT = "这不是 JSON{{{"

_MESSAGES = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": "算一下"},
    {"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "function": {"name": "calculus", "arguments": "{}"}}]},
    {"role": "tool", "tool_call_id": "c1", "content": json.dumps(_CALC_RESULT, ensure_ascii=False)},
    {"role": "assistant", "content": "答案是 π。接下来我查一下前置。", "tool_calls": [
        {"id": "c2", "function": {"name": "kg_lookup", "arguments": "{}"}}]},
    {"role": "tool", "tool_call_id": "c2", "content": json.dumps(_KG_RESULT, ensure_ascii=False)},
]


def main() -> int:
    # 1) 各类型摘要的关键字段（字段优先级来自各工具 spec 的 digest_fields 声明）
    d1 = digest_tool_content(json.dumps(_CALC_RESULT, ensure_ascii=False), _digest_fields_for("calculus"))
    print("[1] calculus 摘要:", d1)
    if "π" not in d1 or "steps×6" not in d1 or not d1.startswith(_DIGEST_MARK):
        print("[FAIL] calculus 摘要丢了答案或步数")
        return 1

    d2 = digest_tool_content(json.dumps(_RUNNER_RESULT, ensure_ascii=False), _digest_fields_for("code_runner"))
    print("[2] code_runner 摘要:", d2)
    if "a b = 2" not in d2 or "\n" in d2:
        print("[FAIL] code_runner 摘要丢了 stdout 或没压成单行")
        return 1
    d2f = digest_tool_content(json.dumps({"ok": False, "error": "Timeout after 10s"}, ensure_ascii=False))
    print("[2b] 失败结果摘要:", d2f)
    if "失败" not in d2f or "Timeout" not in d2f:
        print("[FAIL] 失败结果必须保留 error")
        return 1

    d3 = digest_tool_content(json.dumps(_KG_RESULT, ensure_ascii=False), _digest_fields_for("kg_lookup"))
    print("[3] kg_lookup 摘要:", d3)
    if "傅里叶变换" not in d3:
        print("[FAIL] kg_lookup 摘要丢了命中节点名")
        return 1

    d4 = digest_tool_content(_BAD_RESULT)
    print("[4] 非 JSON 兜底:", d4)
    if not d4.startswith(_DIGEST_MARK):
        print("[FAIL] 非 JSON 应截断兜底")
        return 1

    # 2) 时序语义：模拟「第二轮 tool_calls 已到、新结果未追加」时压旧轮
    msgs = [dict(m) for m in _MESSAGES]
    before = sum(len(m["content"]) for m in msgs if m["role"] == "tool")
    saved = compress_old_tool_results(msgs)
    after = sum(len(m["content"]) for m in msgs if m["role"] == "tool")
    ratio = 1 - after / before
    print(f"[5] 工具内容 {before} -> {after} 字符，节省 {saved}（{ratio:.0%}）")
    if ratio < 0.5:
        print("[FAIL] 压缩率不足 50%，摘要没起作用")
        return 1

    # 3) 结构字段完好 + 非工具消息未动
    tool_msgs = [m for m in msgs if m["role"] == "tool"]
    if [m["tool_call_id"] for m in tool_msgs] != ["c1", "c2"]:
        print("[FAIL] tool_call_id 被破坏")
        return 1
    if msgs[1]["content"] != "算一下" or msgs[4]["content"] != "答案是 π。接下来我查一下前置。":
        print("[FAIL] 非 tool 消息被误改")
        return 1

    # 4) 幂等
    saved2 = compress_old_tool_results(msgs)
    if saved2 != 0:
        print("[FAIL] 已压缩消息不应再处理，又省了", saved2)
        return 1

    print("SANITY OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
