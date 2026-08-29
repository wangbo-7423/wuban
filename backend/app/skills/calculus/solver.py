"""微积分 skill：不定/定积分、求导（含高阶）、极限（含单侧极限）。

底层复用 `app/agent/math_tools.py` 共享的安全解析 / 超时 / LaTeX 能力，
本文件只负责「微积分这个场景的多步流程」。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import sympy as sp

from app.agent.math_tools import (
    available,
    latex_of,
    numeric_of,
    run_with_timeout,
    safe_parse,
    to_sym,
)
from app.skills.base import SkillSpec

_MODULE_DIR = Path(__file__).resolve().parent
MAX_EXPR_LEN = 200

_TASKS = ("integrate", "derivative", "limit")

_TASK_DESC = {
    "integrate": "求积分（给 lower/upper 是定积分，否则不定积分）",
    "derivative": "求导（order 指定阶数，默认一阶）",
    "limit": "求极限（lower 为趋向点，写成 0+ / 0- 表示单侧极限）",
}


def _parse_point(value: str | None) -> tuple[Any, str]:
    """解析极限趋向点。

    支持单侧：`0+` → 右极限，`0-` → 左极限；默认双侧（sympy 的 dir='+-'）。
    """
    if value is None:
        return sp.Integer(0), "+-"
    s = str(value).strip()
    direction = "+-"
    if s.endswith("+"):
        direction, s = "+", s[:-1].strip()
    elif s.endswith("-"):
        direction, s = "-", s[:-1].strip()
    return to_sym(s), direction


def _compute(
    task: str,
    expression: str,
    var: str,
    lower: str | None,
    upper: str | None,
    order: int,
) -> dict[str, Any]:
    expr = safe_parse(expression)
    v = sp.Symbol(var)
    steps: list[dict[str, str]] = []

    if task == "integrate":
        if lower is not None and upper is not None:
            a, b = to_sym(lower), to_sym(upper)
            result = sp.integrate(expr, (v, a, b))
            problem = rf"\int_{{{latex_of(a)}}}^{{{latex_of(b)}}} {latex_of(expr)}\, d{latex_of(v)}"
            steps = [
                {"expr": problem, "note": f"对 {var} 在区间 [{a}, {b}] 上求定积分"},
                {"expr": latex_of(result), "note": "代入上下限并化简"},
            ]
        else:
            result = sp.integrate(expr, v)
            problem = rf"\int {latex_of(expr)}\, d{latex_of(v)}"
            steps = [
                {"expr": problem, "note": f"对 {var} 求不定积分"},
                {
                    "expr": latex_of(result) + r" + C",
                    "note": "积分结果（C 为积分常数，千万别漏）",
                },
            ]

    elif task == "derivative":
        n = max(1, int(order or 1))
        result = sp.diff(expr, v, n)
        if n == 1:
            problem = rf"\frac{{d}}{{d {latex_of(v)}}} \left({latex_of(expr)}\right)"
            note = f"对 {var} 求一阶导"
        else:
            problem = (
                rf"\frac{{d^{{{n}}}}}{{d {latex_of(v)}^{{{n}}}}} "
                rf"\left({latex_of(expr)}\right)"
            )
            note = f"对 {var} 求 {n} 阶导"
        steps = [
            {"expr": problem, "note": note},
            {"expr": latex_of(result), "note": "导数结果"},
        ]

    else:  # limit
        point, direction = _parse_point(lower)
        result = sp.limit(expr, v, point, direction)
        arrow = rf"{latex_of(v)} \to {latex_of(point)}"
        if direction == "+":
            arrow += r"^{+}"
        elif direction == "-":
            arrow += r"^{-}"
        problem = rf"\lim_{{{arrow}}} {latex_of(expr)}"
        side = "（单侧极限）" if direction in ("+", "-") else ""
        steps = [
            {"expr": problem, "note": f"求 {var} 趋向 {point} 时的极限{side}"},
            {"expr": latex_of(result), "note": "极限值"},
        ]

    return {
        "ok": True,
        "task": task,
        # ↓ 以下字段对齐 math 卡 payload.math
        "problem": problem,
        "steps": steps,
        "answer": str(result),
        "unit": None,
        "latex": latex_of(result),
        "numeric": numeric_of(result),
    }


def solve(
    task: str,
    expression: str,
    var: str = "x",
    lower: str | None = None,
    upper: str | None = None,
    order: int = 1,
) -> dict[str, Any]:
    """微积分求解入口。

    Args:
        task: `integrate` / `derivative` / `limit`。
        expression: sympy 风格表达式，如 `x**2*sin(x)`。
        var: 主变量，默认 `x`。
        lower: 定积分下限 / 极限趋向点（支持 `0+`、`0-` 单侧）。
        upper: 定积分上限。
        order: 求导阶数，默认 1。

    Returns:
        成功：`{ok, task, problem, steps, answer, unit, latex, numeric}`；
        失败：`{ok: False, error}`。
    """
    if not available():
        return {"ok": False, "error": "sympy 不可用，calculus skill 无法工作"}

    task = (task or "").strip().lower()
    if task not in _TASKS:
        return {
            "ok": False,
            "error": f"不支持的 task: {task}",
            "supported": list(_TASKS),
        }

    expression = (expression or "").strip()
    if not expression:
        return {"ok": False, "error": "expression 不能为空"}
    if len(expression) > MAX_EXPR_LEN:
        return {"ok": False, "error": f"表达式过长（>{MAX_EXPR_LEN} 字符）"}

    try:
        return run_with_timeout(
            lambda: _compute(task, expression, var or "x", lower, upper, order)
        )
    except TimeoutError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "calculus",
            "description": (
                "微积分求解：不定积分 / 定积分、求导（含高阶）、极限（含单侧极限）。"
                "返回 LaTeX 分步推导。表达式用 sympy 风格（幂用 **，根号写 sqrt(x)）。 "
                + "；".join(f"{k}: {v}" for k, v in _TASK_DESC.items())
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "enum": list(_TASKS),
                        "description": "任务类型",
                    },
                    "expression": {
                        "type": "string",
                        "description": "数学表达式，如 'x**2*sin(x)'、'sin(x)/x'",
                    },
                    "var": {"type": "string", "description": "主变量，默认 x"},
                    "lower": {
                        "type": "string",
                        "description": "定积分下限 / 极限趋向点（'0+'、'0-' 表单侧极限）",
                    },
                    "upper": {"type": "string", "description": "定积分上限"},
                    "order": {"type": "integer", "description": "求导阶数，默认 1"},
                },
                "required": ["task", "expression"],
            },
        },
    }


SKILL = SkillSpec(
    name="calculus",
    title="微积分",
    description="微积分求解（积分 / 求导 / 极限）",
    schema=_schema(),
    solve=solve,
    module_dir=_MODULE_DIR,
)
