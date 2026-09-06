"""交互可视化卡构建器（docs/16）：模板填参 → 自包含 HTML → `interactive` 卡 payload。

P0 只做「模板填参」层（docs/16 §2.2 分层的第一层）；`viz-kit` 内联库与 LLM 现写
（make_visual 长尾）是 P1 范围。

安全模型（docs/16 §4）：沙箱 iframe 渲染由前端负责；这里守两道闸——
体积上限（64KB）+ 零外部请求（模板源码不含 http(s) 引用，守门测试锁死）。

参数注入约定：模板内写 `<script>window.__VIZ_PARAMS__ = __VIZ_PARAMS__;</script>`，
build() 把该 token 替换为参数 JSON——模板代码只从 window.__VIZ_PARAMS__ 读初始值。
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

VIZ_DIR = Path(__file__).parent / "templates"

MAX_HTML_BYTES = 64 * 1024  # docs/16 §4：单卡 HTML 体积上限

# 模板注册表：新增模板 = 加一个 html 文件 + 这里一行 + test_viz.py 守门自动覆盖
# kind: static = 参数只是初始值（数值/字符串）；expr = 数据驱动家族——
#       params 里带数学表达式，后端 sympy 编译成 JS 函数注入（docs/16 v2）
_TEMPLATES: dict[str, dict[str, Any]] = {
    "harmonic": {
        "kind": "static",
        "file": "harmonic.html",
        "title": "傅里叶谐波实验台",
        "defaults": {"default_n": 5},
        "desc": "拖动谐波数 N，看方波如何被正弦波逐层逼近（吉布斯现象）",
    },
    "pid_tuner": {
        "kind": "static",
        "file": "pid_tuner.html",
        "title": "PID 调参实验台",
        "defaults": {"kp": 2.0, "ki": 0.5, "kd": 0.0},
        "desc": "拖动 Kp / Ki / Kd，实时看阶跃响应曲线的超调与稳定过程",
    },
    "curve_lab": {
        "kind": "expr",
        "file": "curve_lab.html",
        "title": "曲线实验台",
        "defaults": {
            "expr": "A*exp(-0.35*x)*sin(w*x)",
            "sliders": [
                {"key": "A", "min": 0, "max": 3, "step": 0.05, "default": 2},
                {"key": "w", "min": 1, "max": 10, "step": 0.1, "default": 5},
            ],
            "x_range": [0, 12],
        },
        "desc": "曲线实验台（通用）：给出 y=f(x) 表达式与滑块，拖动看曲线变化"
                "——阻尼振动/拍频/增长衰减/导数几何等参数化曲线类概念",
    },
}

# 禁网红线：模板 HTML 里不允许出现任何外部引用（docs/16 §4）
_FORBIDDEN_MARKS = ("http://", "https://")

# params 值只收标量，防止把奇怪对象注入模板 JSON
_MAX_STR = 200


class VizError(ValueError):
    """模板/参数不合法（调用方转成结构化错误返回 GLM）。"""


def template_names() -> list[str]:
    return sorted(_TEMPLATES)


def template_brief() -> str:
    """给 GLM 的一句话模板清单（进工具描述/错误 hint）。"""
    return "；".join(f"{k}（{v['desc']}）" for k, v in sorted(_TEMPLATES.items()))


def _clean_params(params: dict[str, Any] | None) -> dict[str, Any]:
    if params is None:
        return {}
    if not isinstance(params, dict):
        raise VizError("params 必须是对象")
    out: dict[str, Any] = {}
    for k, v in list(params.items())[:12]:
        key = str(k).strip()[:40]
        if not key:
            continue
        if isinstance(v, bool) or isinstance(v, (int, float)):
            out[key] = v
        elif isinstance(v, str):
            out[key] = v.strip()[:_MAX_STR]
        else:
            raise VizError(f"参数 {key} 只支持数字/字符串")
    return out


def _guard(html: str) -> str:
    if len(html.encode("utf-8")) > MAX_HTML_BYTES:
        raise VizError(f"HTML 超过 {MAX_HTML_BYTES // 1024}KB 上限")
    low = html.lower()
    for mark in _FORBIDDEN_MARKS:
        if mark in low:
            raise VizError(f"模板含外部引用 {mark}（禁网红线）")
    return html


def build(
    template: str,
    *,
    params: dict[str, Any] | None = None,
    title: str | None = None,
    preview_text: str | None = None,
    probe_question: str | None = None,
    reveal_hint: str | None = None,
) -> dict[str, Any]:
    """模板填参生成 `interactive` 卡 payload（html / preview_text / probe / meta）。

    抛 VizError = 模板名不存在或参数不合法；调用方（make_visual 工具）转结构化错误。
    """
    spec = _TEMPLATES.get(str(template or "").strip())
    if spec is None:
        raise VizError(f"未知模板 {template!r}，可用：{template_names()}")

    if spec["kind"] == "expr":
        return _build_expr(spec, params if isinstance(params, dict) else None,
                           title=title, preview_text=preview_text,
                           probe_question=probe_question, reveal_hint=reveal_hint)

    merged = {**spec["defaults"], **_clean_params(params)}
    try:
        raw = (VIZ_DIR / spec["file"]).read_text(encoding="utf-8")
    except OSError as e:  # 模板缺失是配置错误，宁可炸也别静默
        raise VizError(f"模板文件缺失: {spec['file']}") from e
    # 精确替换赋值语句（str.replace 会连 window.__VIZ_PARAMS__ 读取处的同名
    # 子串一起换掉，两个 script 块全变语法错误——浏览器实测踩过）
    stmt = "window.__VIZ_PARAMS__ = __VIZ_PARAMS__;"
    if stmt not in raw:
        raise VizError(f"模板缺少参数注入语句: {spec['file']}")
    html = _guard(raw.replace(
        stmt,
        f"window.__VIZ_PARAMS__ = {json.dumps(merged, ensure_ascii=False)};",
    ))

    probe = (
        {"question": str(probe_question).strip()[:300], "reveal_hint": _hint(reveal_hint)}
        if probe_question and str(probe_question).strip()
        else None
    )
    return {
        "html": html,
        "preview_text": (str(preview_text).strip()[:200] if preview_text and str(preview_text).strip() else spec["desc"]),
        "probe": probe,
        "meta": {
            "source": "template",
            "template": template,
            "params": merged,
            "title": (str(title).strip()[:60] if title and str(title).strip() else spec["title"]),
        },
    }


def _clean_label(s: Any, limit: int) -> str | None:
    s = str(s or "").strip()
    return s[:limit] or None


def _hint(s: str | None) -> str | None:
    if not s or not str(s).strip():
        return None
    # 前端渲染会加「提示：」前缀，模型自己写的要去掉，避免「提示：提示：」
    return str(s).strip().removeprefix("提示：").strip()[:200] or None


def _build_expr(
    spec: dict[str, Any],
    params: dict[str, Any] | None,
    *,
    title: str | None,
    preview_text: str | None,
    probe_question: str | None,
    reveal_hint: str | None,
) -> dict[str, Any]:
    """数据驱动家族（curve_lab 等）：LLM 只给「数学表达式 + 滑块定义」，不写 JS。

    后端 sympy 白名单编译表达式 → jscode 注入模板（inline function，运行时无
    eval）；JS 侧只负责按滑块值采样画线。
    """
    from app.viz.expr_eval import ExprError, compile_expr

    params = params or {}
    defaults = spec["defaults"]
    expr = str(params.get("expr") or defaults["expr"])
    x_range = params.get("x_range") or defaults["x_range"]
    try:
        xr = (float(x_range[0]), float(x_range[1]))
    except (TypeError, ValueError, IndexError):
        raise VizError("x_range 必须是 [xmin, xmax]")
    if not (math.isfinite(xr[0]) and math.isfinite(xr[1]) and 0 < xr[1] - xr[0] <= 1000):
        raise VizError("x_range 要求 xmin < xmax，区间宽度 ≤ 1000")

    # 滑块定义：合并 LLM 传入与模板默认，逐项校验
    raw_sliders = params.get("sliders") if isinstance(params.get("sliders"), list) else defaults["sliders"]
    sliders: list[dict[str, Any]] = []
    keys: list[str] = []
    for s in raw_sliders[:5]:
        if not isinstance(s, dict):
            raise VizError("sliders 每项必须是对象 {key,min,max,step,default}")
        key = str(s.get("key") or "").strip()
        try:
            mn, mx = float(s.get("min")), float(s.get("max"))
            st = float(s.get("step") or (mx - mn) / 100)
            df = float(s.get("default", (mn + mx) / 2))
        except (TypeError, ValueError):
            raise VizError(f"滑块 {key!r} 的 min/max/step/default 必须是数字")
        if not key or mn >= mx or st <= 0:
            raise VizError(f"滑块 {key!r} 定义不合法（要求 min<max、step>0）")
        sliders.append({
            "key": key,
            "label": _clean_label(s.get("label"), 20) or key,
            "min": mn, "max": mx, "step": st,
            "default": min(mx, max(mn, df)),
        })
        keys.append(key)

    try:
        js_fn = compile_expr(expr, param_keys=keys, x_range=xr)
    except ExprError as e:
        raise VizError(f"表达式不合法：{e}") from e

    merged: dict[str, Any] = {
        "expr": expr[:200],
        "sliders": sliders,
        "x_range": [xr[0], xr[1]],
    }
    for opt in ("x_label", "y_label"):
        v = _clean_label(params.get(opt), 20)
        if v:
            merged[opt] = v

    try:
        raw = (VIZ_DIR / spec["file"]).read_text(encoding="utf-8")
    except OSError as e:
        raise VizError(f"模板文件缺失: {spec['file']}") from e
    stmt_params = "window.__VIZ_PARAMS__ = __VIZ_PARAMS__;"
    stmt_fn = "window.__VIZ_EVAL_FN__ = __VIZ_EVAL_FN__;"
    if stmt_params not in raw or stmt_fn not in raw:
        raise VizError(f"模板缺少注入语句: {spec['file']}")
    html = _guard(raw
                  .replace(stmt_params, f"window.__VIZ_PARAMS__ = {json.dumps(merged, ensure_ascii=False)};")
                  .replace(stmt_fn, f"window.__VIZ_EVAL_FN__ = {js_fn};"))

    probe = (
        {"question": str(probe_question).strip()[:300], "reveal_hint": _hint(reveal_hint)}
        if probe_question and str(probe_question).strip()
        else None
    )
    return {
        "html": html,
        "preview_text": (str(preview_text).strip()[:200] if preview_text and str(preview_text).strip() else spec["desc"]),
        "probe": probe,
        "meta": {
            "source": "template",
            "template": "curve_lab",
            "params": merged,
            "title": (str(title).strip()[:60] if title and str(title).strip() else spec["title"]),
        },
    }
