"""常微分方程 skill：判类型 → 选解法 → 求解 → 代初值。

## 为什么它必须是 skill 而不是一个工具调用

解 ODE 的关键不在「算」，而在「判类型」：可分离、一阶线性、齐次、恰当、
伯努利、欧拉……**类型决定解法**，判错了后面全错。这是个多步推理流程，
塞进 `sympy_calc` 那种单表达式工具里只会变成一坨 if-else。

这里用 sympy 的 `classify_ode` 拿到类型判别结果，再映射成中文方法名，
让模型能告诉学生「为什么用这个方法」——这才是伴学价值。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import sympy as sp

from app.agent.math_tools import (
    available,
    latex_of,
    run_with_timeout,
    safe_parse,
)
from app.skills.base import SkillSpec

_MODULE_DIR = Path(__file__).resolve().parent
MAX_EXPR_LEN = 200

# ODE 场景额外需要的符号（不进全局白名单，避免污染其他 skill）
_ODE_LOCALS: dict[str, Any] = {
    "Derivative": sp.Derivative,
    "Function": sp.Function,
    "Eq": sp.Eq,
    "dsolve": sp.dsolve,
    "classify_ode": sp.classify_ode,
    "diff": sp.diff,
    "exp": sp.exp,
    "sin": sp.sin,
    "cos": sp.cos,
    "log": sp.log,
    "sqrt": sp.sqrt,
}

# sympy classify_ode 的类型标识 → 中文方法名
_METHOD_ZH = {
    "separable": "可分离变量法（两边积分）",
    "1st_exact": "恰当方程（全微分方程）",
    "1st_linear": "一阶线性方程（积分因子法）",
    "Bernoulli": "伯努利方程（换元化为一阶线性）",
    "1st_homogeneous_coeff_best": "齐次方程（令 u = y/x 换元）",
    "1st_homogeneous_coeff_subs_dep_div_indep": "齐次方程（u = y/x 换元）",
    "1st_homogeneous_coeff_subs_indep_div_dep": "齐次方程（u = x/y 换元）",
    "almost_linear": "几乎线性方程",
    "nth_linear_constant_coeff_homogeneous": "高阶常系数线性齐次（特征方程法）",
    "nth_linear_constant_coeff_undetermined_coefficients": "高阶常系数线性（待定系数法）",
    "nth_linear_constant_coeff_variation_of_parameters": "高阶常系数线性（常数变易法）",
    "nth_linear_euler_eq_homogeneous": "欧拉方程（齐次）",
    "nth_linear_euler_eq_nonhomogeneous_undetermined_coefficients": "欧拉方程（非齐次）",
    "nth_order_reducible": "可降阶方程",
    "nth_algebraic": "代数方程（无需求解微分方程）",
    "lie_group": "李群对称法",
    "2nd_power_series_ordinary": "幂级数解法",
}

# classify_ode 返回的是 sympy 内部优先级，未必符合课堂讲授顺序。
# 例：y' + 2y = e^x 会被判成「恰当方程」，但课堂上更该讲「一阶线性·积分因子法」。
# 这里按教学常规重排，让模型讲的是学生最需要掌握的方法。
_TEACHING_PRIORITY = (
    "separable",
    "1st_linear",
    "Bernoulli",
    "1st_homogeneous_coeff_best",
    "1st_homogeneous_coeff_subs_dep_div_indep",
    "1st_homogeneous_coeff_subs_indep_div_dep",
    "almost_linear",
    "nth_linear_constant_coeff_homogeneous",
    "nth_linear_constant_coeff_undetermined_coefficients",
    "nth_linear_constant_coeff_variation_of_parameters",
    "nth_linear_euler_eq_homogeneous",
    "nth_order_reducible",
)


def _pick_primary(types: list[str]) -> str:
    """从候选解法里挑最符合教学顺序的那个。"""
    for prefer in _TEACHING_PRIORITY:
        if prefer in types:
            return prefer
    return types[0] if types else "unknown"


def _parse_ics(ics: str) -> dict[Any, Any]:
    """解析初值条件，支持 `y(0)=1`（多个用逗号分隔）。

    注：导数初值（如 y'(0)=2）请让模型用 sympy 标准写法，
    本函数只保证最常见的函数值初值可用。
    """
    out: dict[Any, Any] = {}
    for part in ics.split(","):
        part = part.strip()
        if "=" not in part:
            continue
        lhs, rhs = part.split("=", 1)
        out[safe_parse(lhs.strip(), _ODE_LOCALS)] = safe_parse(rhs.strip())
    return out


def _compute(
    equation: str,
    func: str,
    var: str,
    ics: str | None,
) -> dict[str, Any]:
    eq = safe_parse(equation, _ODE_LOCALS)
    v = sp.Symbol(var)
    f = sp.Function(func)
    target = f(v)

    # ── 第 1 步：判类型（这一步是 skill 的核心价值）──────────
    try:
        types = list(sp.classify_ode(eq, target))
    except Exception:  # noqa: BLE001
        types = []
    primary = _pick_primary(types)
    method_zh = _METHOD_ZH.get(primary, f"通用解法（{primary}）")

    # ── 第 2 步：按类型求解 ────────────────────────────────
    kwargs: dict[str, Any] = {}
    ics_dict: dict[Any, Any] = {}
    if ics:
        ics_dict = _parse_ics(ics)
        if ics_dict:
            kwargs["ics"] = ics_dict
    sol = sp.dsolve(eq, target, **kwargs)

    # ── 第 3 步：组装分步推导（expr 必须是 LaTeX，中文只放 note）──
    steps: list[dict[str, str]] = [
        {"expr": latex_of(eq), "note": "原方程"},
    ]
    if ics_dict:
        steps.append(
            {"expr": latex_of(sol), "note": f"代入初值条件后的特解（{method_zh}）"}
        )
    else:
        steps.append({"expr": latex_of(sol), "note": f"通解（{method_zh}）"})

    return {
        "ok": True,
        "equation": latex_of(eq),
        "ode_type": primary,                    # sympy 类型标识
        "method": method_zh,                    # 中文方法名（供模型讲解，不进 KaTeX）
        "candidates": types[:5],                # 其他可行解法，供模型择优说明
        "has_ics": bool(ics_dict),
        # ↓ 对齐 math 卡 payload.math
        "problem": latex_of(eq),
        "steps": steps,
        "answer": str(sol),
        "unit": None,
        "latex": latex_of(sol),
    }


def solve(
    equation: str,
    func: str = "y",
    var: str = "x",
    ics: str | None = None,
) -> dict[str, Any]:
    """常微分方程求解入口。

    Args:
        equation: 方程，如 `Eq(Derivative(y(x), x), y(x))`
            或 `Derivative(y(x), x) - y(x)`（等于 0 的形式）。
        func: 未知函数名，默认 `y`。
        var: 自变量，默认 `x`。
        ics: 初值条件（可选），如 `y(0)=1`，多个用逗号分隔。

    Returns:
        成功：`{ok, equation, ode_type, method, candidates, steps, answer, latex, ...}`；
        失败：`{ok: False, error}`。
    """
    if not available():
        return {"ok": False, "error": "sympy 不可用，ode skill 无法工作"}

    equation = (equation or "").strip()
    if not equation:
        return {"ok": False, "error": "equation 不能为空"}
    if len(equation) > MAX_EXPR_LEN:
        return {"ok": False, "error": f"方程过长（>{MAX_EXPR_LEN} 字符）"}

    try:
        return run_with_timeout(
            lambda: _compute(equation, func or "y", var or "x", ics)
        )
    except TimeoutError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "ode",
            "description": (
                "常微分方程求解：自动判别方程类型（可分离 / 一阶线性 / 齐次 / "
                "伯努利 / 恰当 / 高阶常系数线性 / 欧拉方程等），再按类型求解，"
                "返回通解或代入初值后的特解。适用于学生问微分方程怎么解。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "equation": {
                        "type": "string",
                        "description": (
                            "微分方程，sympy 风格。例如 "
                            "'Eq(Derivative(y(x), x), y(x))' 或 "
                            "'Derivative(y(x), x) + 2*y(x) - exp(x)'（等于 0 的形式）"
                        ),
                    },
                    "func": {"type": "string", "description": "未知函数名，默认 y"},
                    "var": {"type": "string", "description": "自变量，默认 x"},
                    "ics": {
                        "type": "string",
                        "description": "初值条件（可选），如 'y(0)=1'；多个用逗号分隔",
                    },
                },
                "required": ["equation"],
            },
        },
    }


SKILL = SkillSpec(
    name="ode",
    title="常微分方程",
    description="常微分方程求解（先判类型再选解法）",
    schema=_schema(),
    solve=solve,
    module_dir=_MODULE_DIR,
    scenes=("math",),
    digest_fields=("answer", "final_expr"),
    guide="guide_math.md",
)
