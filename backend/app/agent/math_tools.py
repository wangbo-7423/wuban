"""符号数学工具 `sympy_calc`：替换原本只能数值求值的 `calculator`。

为什么要换：旧 `calculator` 用的是受限 AST 求值，只能算 `1+2*3` 这类数值式，
高数（积分/极限）和线代（矩阵）全废——而 system prompt 却告诉模型“你能算这些”，
学生一问就露馅。sympy 是成熟符号计算库，且不执行任意 Python 代码。

输出结构刻意**对齐 math 卡 payload**（见 `schemas/card.py` 与 `agent/cards.py`）：
    payload.math = {problem, steps: [{expr, note}], answer, unit}
其中 problem / steps[].expr / answer 都是 **LaTeX 字符串**，前端 KaTeX 直接渲染，
format_cards 可以把工具结果原地变成一张分步推导的 math 卡，零转换成本。

安全策略（三层）：
1. 表达式长度上限，防超长输入拖垮解析；
2. `parse_expr` + 清空 `__builtins__` + 函数名白名单，杜绝任意代码执行；
3. 计算丢进线程池并设超时，超时返回结构化错误，**绝不阻塞主请求线程**。
   注：超时只能放弃等待，无法强杀 sympy 线程（Python 无安全中断机制），
   对 demo 场景足够——主线程不会被拖死。
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any

logger = logging.getLogger(__name__)

MAX_EXPR_LEN = 200          # 表达式字符上限
_TIMEOUT_SEC = 5.0          # 单次计算超时

try:  # sympy 是可选依赖：装不上时工具优雅降级，不让整个应用起不来
    import sympy as sp
    from sympy.parsing.sympy_parser import (
        parse_expr,
        rationalize,
        standard_transformations,
    )

    _SYMPY_OK = True
    _SYMPY_ERR = ""
except Exception as e:  # noqa: BLE001
    sp = None  # type: ignore[assignment]
    _SYMPY_OK = False
    _SYMPY_ERR = f"{type(e).__name__}: {e}"
    logger.warning("sympy 未安装，sympy_calc 将不可用：%s", _SYMPY_ERR)


_OPS = (
    "auto", "simplify", "expand", "factor",
    "integrate", "differentiate", "diff",
    "limit", "solve", "series", "matrix", "numeric",
)

_OP_LABEL = {
    "auto": "化简求值",
    "simplify": "化简结果",
    "expand": "展开结果",
    "factor": "因式分解结果",
    "numeric": "数值结果",
    "matrix": "矩阵运算结果",
}

# 每个 op 给 GLM 的一句话说明（写进 schema，帮模型选对 op）
_OP_DESC = {
    "auto": "化简表达式并求值（默认）",
    "simplify": "化简表达式",
    "expand": "展开多项式",
    "factor": "因式分解",
    "integrate": "积分；给 lower/upper 是定积分，否则不定积分",
    "differentiate": "求导（diff 同义）",
    "diff": "求导",
    "limit": "求极限，lower 为趋向点",
    "solve": "解方程 expression=0，var 指定未知数",
    "series": "泰勒展开，lower 为中心（默认 0），upper 为阶数（默认 6）",
    "matrix": "矩阵运算（Matrix([[...]])），方阵附行列式与秩",
    "numeric": "数值化求值",
}


def available() -> bool:
    """sympy 是否可用（tools.py 据此决定注册 sympy_calc 还是回退旧 calculator）。"""
    return _SYMPY_OK


_ALLOWED: dict[str, Any] | None = None
_TRANSFORMS: tuple[Any, ...] | None = None
_GLOBALS: dict[str, Any] | None = None


def _locals() -> dict[str, Any]:
    """函数白名单（惰性构建，避免 sympy 缺失时在导入期炸掉）。"""
    global _ALLOWED
    if _ALLOWED is None:
        _ALLOWED = {
            # 常量
            "pi": sp.pi, "E": sp.E, "I": sp.I, "oo": sp.oo, "inf": sp.oo,
            # 初等函数
            "sin": sp.sin, "cos": sp.cos, "tan": sp.tan, "cot": sp.cot,
            "asin": sp.asin, "acos": sp.acos, "atan": sp.atan,
            "sinh": sp.sinh, "cosh": sp.cosh, "tanh": sp.tanh,
            "exp": sp.exp, "log": sp.log, "ln": sp.log,
            "sqrt": sp.sqrt, "Abs": sp.Abs, "abs": sp.Abs, "sign": sp.sign,
            # 组合
            "factorial": sp.factorial, "binomial": sp.binomial,
            "Sum": sp.Sum, "Product": sp.Product,
            # 线代
            "Matrix": sp.Matrix, "eye": sp.eye, "zeros": sp.zeros, "ones": sp.ones,
            # 运算
            "diff": sp.diff, "integrate": sp.integrate, "limit": sp.limit,
            "solve": sp.solve, "simplify": sp.simplify, "factor": sp.factor,
            "expand": sp.expand, "series": sp.series, "det": sp.det,
        }
    return _ALLOWED


def _transforms() -> tuple[Any, ...]:
    global _TRANSFORMS
    if _TRANSFORMS is None:
        # rationalize：把 0.5 这类浮点转成有理数 1/2，符号运算结果更干净
        _TRANSFORMS = standard_transformations + (rationalize,)
    return _TRANSFORMS


def _globals() -> dict[str, Any]:
    """sympy 命名空间，但清空 `__builtins__`。

    注意：这里**不能**只给 `{"__builtins__": {}}` —— sympy 解析器内部要用
    `Symbol` / `Integer` / `Rational` 等名字构造对象（auto_symbol、rationalize
    这些 transformation 会生成对它们的调用），一旦清空，解析直接 NameError。
    所以保留 sympy 全部公开名字，只掐掉内建函数（eval/import/open 等）。
    """
    global _GLOBALS
    if _GLOBALS is None:
        _GLOBALS = {
            name: getattr(sp, name)
            for name in dir(sp)
            if not name.startswith("_")
        }
        _GLOBALS["__builtins__"] = {}
    return _GLOBALS


def safe_parse(expr: str, extra_locals: dict[str, Any] | None = None) -> Any:
    """安全解析表达式：sympy 命名空间（掐掉 builtins）+ 函数白名单。

    Args:
        expr: 表达式字符串。
        extra_locals: **该场景额外允许的名字**。各 skill 可按需声明
            （如 ODE 需要 `Derivative` / `Function` / `dsolve`），
            避免为兼容性把危险名字塞进全局白名单。
    """
    local = dict(_locals())
    if extra_locals:
        local.update(extra_locals)
    return parse_expr(
        expr,
        global_dict=dict(_globals()),   # 拷贝一份，避免被 eval 写入污染
        local_dict=local,
        transformations=_transforms(),
        evaluate=True,
    )


# 内部沿用旧名，_compute 等既有代码无需改动
_parse = safe_parse


_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sympy_calc")


def _run_with_timeout(fn: Any) -> Any:
    future = _EXECUTOR.submit(fn)
    try:
        return future.result(timeout=_TIMEOUT_SEC)
    except FutureTimeoutError:
        future.cancel()
        raise TimeoutError(
            f"计算超时（>{_TIMEOUT_SEC}s），表达式过于复杂，请拆分成更小的步骤"
        ) from None


def _to_sym(value: Any) -> Any:
    """把 lower/upper 参数转成 sympy 对象（支持 'oo'、'pi/2'、数字等）。"""
    if isinstance(value, bool):
        return sp.Integer(int(value))
    if isinstance(value, int):
        return sp.Integer(value)
    if isinstance(value, float):
        return sp.nsimplify(value)
    return _parse(str(value).strip())


def _numeric(result: Any) -> float | None:
    """能算出实数就返回 float，否则 None（复数/符号解不给数值）。"""
    try:
        num = sp.N(result)
        if num.is_number and num.is_real:
            return float(num)
    except Exception:  # noqa: BLE001
        pass
    return None


def _dedup_steps(
    before: str, after: str, note_before: str, note_after: str
) -> list[dict[str, str]]:
    """两步推导；若前后 LaTeX 完全相同（说明原式已最简），合并成一步。"""
    if before == after:
        return [{"expr": after, "note": note_after}]
    return [
        {"expr": before, "note": note_before},
        {"expr": after, "note": note_after},
    ]


def _compute(
    expression: str,
    op: str,
    var: str,
    lower: Any,
    upper: Any,
) -> dict[str, Any]:
    """真正的计算逻辑（在线程池里跑）。"""
    expr = _parse(expression)
    v = sp.Symbol(var)
    problem = sp.latex(expr)
    steps: list[dict[str, str]] = []
    answer: str
    latex: str
    extra: dict[str, Any] = {}

    if op == "integrate":
        if lower is not None and upper is not None:
            a, b = _to_sym(lower), _to_sym(upper)
            result = sp.integrate(expr, (v, a, b))
            problem = rf"\int_{{{sp.latex(a)}}}^{{{sp.latex(b)}}} {sp.latex(expr)}\, d{sp.latex(v)}"
            steps = [
                {"expr": problem, "note": f"对 {var} 在区间 [{a}, {b}] 上求定积分"},
                {"expr": sp.latex(result), "note": "代入上下限并化简"},
            ]
        else:
            result = sp.integrate(expr, v)
            problem = rf"\int {sp.latex(expr)}\, d{sp.latex(v)}"
            steps = [
                {"expr": problem, "note": f"对 {var} 求不定积分"},
                {"expr": sp.latex(result) + r" + C", "note": "积分结果（C 为积分常数）"},
            ]
        answer, latex = str(result), sp.latex(result)

    elif op in ("differentiate", "diff"):
        result = sp.diff(expr, v)
        problem = rf"\frac{{d}}{{d {sp.latex(v)}}} \left({sp.latex(expr)}\right)"
        steps = [
            {"expr": problem, "note": f"对 {var} 求导"},
            {"expr": sp.latex(result), "note": "导数结果"},
        ]
        answer, latex = str(result), sp.latex(result)

    elif op == "limit":
        point = _to_sym(lower) if lower is not None else sp.Integer(0)
        result = sp.limit(expr, v, point)
        problem = rf"\lim_{{{sp.latex(v)} \to {sp.latex(point)}}} {sp.latex(expr)}"
        steps = [
            {"expr": problem, "note": f"求 {var} → {point} 时的极限"},
            {"expr": sp.latex(result), "note": "极限值"},
        ]
        answer, latex = str(result), sp.latex(result)

    elif op == "solve":
        solutions = sp.solve(expr, v)
        sol_latex = (
            r",\; ".join(sp.latex(s) for s in solutions)
            if solutions
            else r"\varnothing"
        )
        steps = [
            {"expr": sp.latex(expr) + " = 0", "note": f"解方程（未知数 {var}）"},
            {"expr": sol_latex, "note": "解集" + ("" if solutions else "（无解）")},
        ]
        problem = sp.latex(expr) + " = 0"
        answer, latex = str(solutions), sol_latex
        result = solutions

    elif op == "series":
        point = _to_sym(lower) if lower is not None else sp.Integer(0)
        order = int(upper) if upper is not None else 6
        result = sp.series(expr, v, point, order)
        problem = rf"\text{{在 }} {sp.latex(v)} = {sp.latex(point)} \text{{ 处展开}}"
        steps = [
            {"expr": sp.latex(expr), "note": f"原式（在 {var}={point} 处展开到 {order} 阶）"},
            {"expr": sp.latex(result), "note": "泰勒展开式"},
        ]
        answer, latex = str(result), sp.latex(result)

    elif op == "matrix":
        result = sp.Matrix(expr) if not isinstance(expr, sp.MatrixBase) else expr
        result = sp.simplify(result)
        if result.is_square:
            extra["det"] = str(result.det())
            extra["rank"] = result.rank()
        in_latex = sp.latex(expr) if isinstance(expr, sp.MatrixBase) else problem
        steps = _dedup_steps(in_latex, sp.latex(result), "输入矩阵", "化简结果")
        answer, latex = str(result), sp.latex(result)

    elif op == "numeric":
        result = sp.N(expr)
        steps = [{"expr": problem, "note": "数值化求值"}]
        answer, latex = str(result), sp.latex(result)

    else:  # auto / simplify / expand / factor
        if op == "expand":
            result = sp.expand(expr)
        elif op == "factor":
            result = sp.factor(expr)
        else:  # auto / simplify
            result = sp.simplify(expr)
        steps = _dedup_steps(
            problem, sp.latex(result), "原式", _OP_LABEL.get(op, "化简结果")
        )
        answer, latex = str(result), sp.latex(result)

    return {
        "ok": True,
        "op": op,
        "input": expression,
        # ↓ 以下四个字段直接对应 math 卡 payload.math
        "problem": problem,
        "steps": steps,
        "answer": answer,
        "unit": None,
        # ↓ 附加信息，便于 GLM 组织讲解
        "latex": latex,
        "numeric": _numeric(result),
        **extra,
    }


def sympy_calc(
    expression: str,
    op: str = "auto",
    var: str = "x",
    lower: str | None = None,
    upper: str | None = None,
) -> dict[str, Any]:
    """符号数学计算：积分 / 求导 / 极限 / 解方程 / 泰勒展开 / 矩阵 / 化简。

    Args:
        expression: 数学表达式，sympy 风格，如 `x**2 + 2*x + 1`、
            `sin(x)/x`、`Matrix([[1,2],[3,4]])`。
        op: 运算类型，见 `_OPS`；不确定时用 `auto`（化简求值）。
        var: 主变量（积分/求导/极限/解方程用），默认 `x`。
        lower: 定积分下限 / 极限趋向点 / 泰勒展开中心。
        upper: 定积分上限 / 泰勒展开阶数。

    Returns:
        成功：`{ok, op, problem, steps, answer, unit, latex, numeric, ...}`
        失败：`{ok: False, error}` —— 让 GLM 据此换策略，绝不抛异常。
    """
    if not _SYMPY_OK:
        return {"ok": False, "error": f"sympy 不可用（{_SYMPY_ERR}）"}

    expression = (expression or "").strip()
    if not expression:
        return {"ok": False, "error": "expression 不能为空"}
    if len(expression) > MAX_EXPR_LEN:
        return {
            "ok": False,
            "error": f"表达式过长（{len(expression)} > {MAX_EXPR_LEN} 字符），请拆分或化简后再试",
        }

    op = (op or "auto").strip().lower()
    if op not in _OPS:
        return {
            "ok": False,
            "error": f"不支持的 op: {op}",
            "supported": list(_OPS),
        }

    try:
        return _run_with_timeout(
            lambda: _compute(expression, op, var or "x", lower, upper)
        )
    except TimeoutError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001
        logger.warning("sympy_calc 失败: %s: %s", type(e).__name__, e)
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ── 对外公开的共享能力（供 app/skills/ 各场景复用）──────────────
# skill 层不必重复实现：安全解析 / 超时保护 / LaTeX 输出

run_with_timeout = _run_with_timeout
numeric_of = _numeric
to_sym = _to_sym


def latex_of(obj: Any) -> str:
    """统一的 LaTeX 输出（失败时退回 str，绝不抛异常）。"""
    try:
        return sp.latex(obj)
    except Exception:  # noqa: BLE001
        return str(obj)


def sympy_calc_schema() -> dict[str, Any]:
    """OpenAI function-calling 形态的 JSON Schema。"""
    return {
        "type": "function",
        "function": {
            "name": "sympy_calc",
            "description": (
                "符号数学计算工具：积分（定/不定）、求导、极限、解方程、泰勒展开、"
                "因式分解/展开/化简、矩阵运算、数值求值。"
                "返回 LaTeX 公式和分步推导，可直接用于给学生讲解。"
                "表达式用 sympy 风格：幂用 **（x 的平方写 x**2），根号写 sqrt(x)，"
                "矩阵写 Matrix([[1,2],[3,4]])。算错或超时会返回 ok=false，此时换个思路。 "
                + "；".join(f"{k}: {v}" for k, v in _OP_DESC.items())
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "数学表达式，如 'x**2 + 2*x + 1'、'sin(x)/x'",
                    },
                    "op": {
                        "type": "string",
                        "enum": list(_OPS),
                        "description": "运算类型；拿不准就用 auto",
                    },
                    "var": {
                        "type": "string",
                        "description": "主变量，默认 x",
                    },
                    "lower": {
                        "type": "string",
                        "description": "定积分下限 / 极限趋向点 / 展开中心，如 '0'、'oo'、'pi/2'",
                    },
                    "upper": {
                        "type": "string",
                        "description": "定积分上限 / 展开阶数，如 '1'、'6'",
                    },
                },
                "required": ["expression"],
            },
        },
    }
