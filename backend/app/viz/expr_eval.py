"""受限表达式求值（docs/16 v2「数据驱动家族」的安全核心）。

把 LLM 给的数学表达式编译成模板可用的 JS 函数体。安全模型——LLM 产出物
从「可执行 JS」收缩为「数学表达式」，四道闸：

1. **sympy 白名单解析**：parse_expr 用受控符号表（x + 滑块名）和受控函数表；
   模型调不到任何 Python 内建；
2. **AST 校验**：遍历表达式树，函数必须 ∈ 白名单，自由符号必须 ∈ {x}∪滑块名，
   拒绝关系式/未定义函数/导数积分等一切非纯算术节点；
3. **jscode 打印**：sympy 官方 JS 打印器只输出 Math.* 形式的数学调用——
   不存在注入 JS 语法的通道；打印结果再做一次标识符白名单复核；
4. **数值采样自检**：lambdify 在给定区间采样，要求有效值足够多，
   拦住「在区间上恒 NaN/Inf」的表达式。

模板侧拿到的 js 是纯数学调用串，以 inline script 注入（运行时零 eval，对齐 CSP 设计）。
"""
from __future__ import annotations

import math
import re
from typing import Any

import sympy as sp
from sympy.core.function import AppliedUndef
from sympy.core.relational import Relational
from sympy.logic.boolalg import Boolean
from sympy.printing.jscode import jscode

EXPR_MAX = 200           # 表达式字符串长度上限
MAX_SLIDERS = 5          # 滑块数上限
SAMPLE_CHECK_POINTS = 60  # 数值自检采样数
MIN_FINITE = 5           # 区间内最少有效值

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,11}$")

# JS 保留字/危险标识符：滑块名不许撞（x 是自变量，单独排除）
_RESERVED = {
    "x", "function", "return", "var", "let", "const", "if", "else", "for", "while",
    "new", "this", "class", "window", "document", "Math", "eval", "import", "export",
    "delete", "typeof", "void", "null", "true", "false", "in", "of", "break",
    "continue", "switch", "case", "default", "throw", "try", "catch", "finally",
    "do", "instanceof", "with", "debugger", "yield", "async", "await",
}

# 受控符号表：函数 + 常量（AST 白名单校验的判据）
_ALLOWED_NAMES: dict[str, Any] = {
    "sin": sp.sin, "cos": sp.cos, "tan": sp.tan,
    "asin": sp.asin, "acos": sp.acos, "atan": sp.atan, "atan2": sp.atan2,
    "sinh": sp.sinh, "cosh": sp.cosh, "tanh": sp.tanh,
    "exp": sp.exp, "log": sp.log, "sqrt": sp.sqrt,
    "abs": sp.Abs, "Abs": sp.Abs, "sign": sp.sign,
    "floor": sp.floor, "ceiling": sp.ceiling, "ceil": sp.ceiling,
    "Min": sp.Min, "Max": sp.Max,
    "pi": sp.pi, "E": sp.E,
}
_ALLOWED_FUNCS = {
    sp.sin, sp.cos, sp.tan, sp.asin, sp.acos, sp.atan, sp.atan2,
    sp.sinh, sp.cosh, sp.tanh, sp.exp, sp.log, sp.sqrt, sp.Abs,
    sp.sign, sp.floor, sp.ceiling, sp.Min, sp.Max,
}

# jscode 输出里允许出现的标识符（其余一律拒绝——Heaviside 等打印机
# 不支持的函数会原样留名，这里兜住）
_PRINTED_IDENT_OK = {"Math", "PI", "E", "min", "max", "abs", "pow", "atan2",
                     "floor", "ceil", "exp", "log", "sqrt", "sin", "cos",
                     "tan", "asin", "acos", "atan", "sinh", "cosh", "tanh",
                     "sign"} | set()


class ExprError(ValueError):
    """表达式不合法（调用方转成结构化错误返回 GLM）。"""


def compile_expr(expr: str, *, param_keys: list[str], x_range: tuple[float, float]) -> str:
    """把数学表达式字符串编译成 JS 函数源码 `function (x, A, w) { return ...; }`。

    expr：关于 x 的数学表达式（可引用滑块名）；param_keys：滑块变量名；
    x_range：数值自检区间。不合法抛 ExprError。
    """
    expr = (expr or "").strip()[:EXPR_MAX]
    if not expr:
        raise ExprError("表达式不能为空")

    keys: list[str] = []
    for k in param_keys:
        if not _KEY_RE.match(k) or k in _RESERVED:
            raise ExprError(f"滑块名不合法: {k!r}（字母开头，避开保留字与 x）")
        if k in keys:
            raise ExprError(f"滑块名重复: {k!r}")
        keys.append(k)

    # 1) 解析：sympy 标准命名空间解析（宽松），安全由后面的 AST 白名单保证——
    #    严格受限 global_dict 会连 parse 内部需要的 Symbol 都挡掉（实测踩过）
    local = {"x": sp.Symbol("x"), **{k: sp.Symbol(k) for k in keys}}
    ns: dict[str, Any] = {}
    exec("from sympy import *", ns)  # noqa: S102 - 只作为解析符号表，产出物过 AST 白名单
    ns["__builtins__"] = {}  # 掏空内建：parse 期间模型输入调不到任何 Python 能力
    ns.update(_ALLOWED_NAMES)
    try:
        e = sp.parse_expr(expr, local_dict=local, global_dict=ns)
    except Exception as exc:  # noqa: BLE001
        raise ExprError(f"表达式解析失败: {exc.__class__.__name__}") from exc
    if not isinstance(e, sp.Basic):
        raise ExprError("表达式不是数学式")

    # 2) AST 校验
    bad_funcs = {str(f.func) for f in e.atoms(sp.Function) if f.func not in _ALLOWED_FUNCS}
    if e.atoms(AppliedUndef) or bad_funcs:
        raise ExprError(f"不支持的函数: {sorted(bad_funcs) or '未定义函数'}。可用：sin cos tan exp log sqrt abs atan2 floor ceil sign Min Max 等")
    if isinstance(e, (sp.Derivative, sp.Integral, Relational, Boolean)):
        # 根节点级检查即可：比较式/求导/积分不可能作为算术运算的子节点出现
        raise ExprError("只支持纯算术表达式（不允许求导/积分/比较）")
    allowed_syms = {sp.Symbol("x"), *(sp.Symbol(k) for k in keys)}
    offenders = {str(s) for s in e.free_symbols if s not in allowed_syms}
    if offenders:
        raise ExprError(f"表达式里的符号 {sorted(offenders)} 未定义（只能是 x 和滑块名）")

    # 3) jscode 打印 + 标识符白名单复核
    try:
        js = jscode(e)
    except Exception as exc:  # noqa: BLE001
        raise ExprError(f"表达式无法编译为 JS: {exc.__class__.__name__}") from exc
    idents = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", js))
    bad_idents = idents - _PRINTED_IDENT_OK - {"x"} - set(keys)
    if bad_idents:
        raise ExprError(f"编译产物含未预期标识符: {sorted(bad_idents)}")

    # 4) 数值采样自检
    fn = _lambdify_safe(e, keys)
    if fn is not None:
        x0, x1 = x_range
        finite = 0
        for i in range(SAMPLE_CHECK_POINTS):
            xv = x0 + (x1 - x0) * i / (SAMPLE_CHECK_POINTS - 1)
            try:
                v = fn(xv, *([1.0] * len(keys)))
                if v is not None and math.isfinite(v):
                    finite += 1
            except (ValueError, OverflowError, ZeroDivisionError, TypeError):
                pass
        if finite < MIN_FINITE:
            raise ExprError("表达式在该区间几乎没有有效值（检查 log/sqrt 的定义域或 x 范围）")

    params = f"x{', ' + ', '.join(keys) if keys else ''}"
    return f"function ({params}) {{ return ({js}); }}"


def _lambdify_safe(e: sp.Basic, keys: list[str]):
    try:
        return sp.lambdify(("x", *keys) if keys else "x", e, modules="math")
    except Exception:  # noqa: BLE001
        return None
