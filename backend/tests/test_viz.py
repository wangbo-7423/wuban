"""交互可视化（docs/16）单测：builder 守门 + make_visual 工具 + 切卡确定性注入。

纯逻辑，不碰 GLM（切卡 LLM 用 monkeypatch 打桩）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent import cards as cards_mod
from app.agent.cards import format_cards
from app.agent.orchestrator import AgentResult
from app.viz import builder
from app.viz.builder import VizError


class TestBuilderGuards:
    def test_build_harmonic_injects_params(self):
        payload = builder.build("harmonic", params={"default_n": 9}, probe_question="N 拉满会怎样？")
        # 赋值语句右侧已注入 JSON，且 window 读取处未被误替换（否则整段 JS 语法错误）
        assert "window.__VIZ_PARAMS__ = " in payload["html"]
        assert "__VIZ_PARAMS__ = __VIZ_PARAMS__" not in payload["html"]
        assert '"default_n": 9' in payload["html"]
        assert payload["probe"]["question"] == "N 拉满会怎样？"
        assert payload["meta"]["source"] == "template"
        assert len(payload["html"].encode("utf-8")) <= builder.MAX_HTML_BYTES

    def test_reveal_hint_dedupes_prefix(self):
        payload = builder.build("harmonic", probe_question="猜猜看？", reveal_hint="提示：想想采样定理")
        assert payload["probe"]["reveal_hint"] == "想想采样定理"

    def test_build_pid_defaults_merged(self):
        payload = builder.build("pid_tuner", params={"kp": 8})
        assert '"kp": 8' in payload["html"]
        assert '"ki"' in payload["html"]                        # 未给参数回落默认值

    def test_unknown_template_rejected(self):
        with pytest.raises(VizError):
            builder.build("not_a_template")

    def test_non_scalar_params_rejected(self):
        with pytest.raises(VizError):
            builder.build("harmonic", params={"default_n": {"a": 1}})

    def test_guard_rejects_external_reference(self):
        with pytest.raises(VizError):
            builder._guard('<script src="https://cdn.example.com/x.js"></script>')
        with pytest.raises(VizError):
            builder._guard('<img src="http://evil.example.com/x.png">')

    def test_guard_rejects_oversize(self, monkeypatch):
        monkeypatch.setattr(builder, "MAX_HTML_BYTES", 10)
        with pytest.raises(VizError):
            builder._guard("<p>" + "x" * 100 + "</p>")

    def test_all_registered_templates_pass_guard(self):
        # 注册表里的每个模板都要能干净出片（新模板忘了守门就在这里炸）
        for name in builder.template_names():
            payload = builder.build(name)
            assert payload["meta"]["template"] == name
            assert "http://" not in payload["html"].lower()
            assert "https://" not in payload["html"].lower()


class TestCurveLab:
    """数据驱动家族（curve_lab）：LLM 只给表达式+滑块，不写 JS。"""

    PARAMS = {
        "expr": "A*sin(w*x)",
        "sliders": [
            {"key": "A", "min": 0, "max": 2, "step": 0.1, "default": 1},
            {"key": "w", "min": 1, "max": 10, "step": 0.1, "default": 3},
        ],
        "x_range": [0, 8],
    }

    def test_compile_and_inject(self):
        payload = builder.build("curve_lab", params=dict(self.PARAMS, preview_text="试试"))
        assert "window.__VIZ_EVAL_FN__ = function" in payload["html"]
        assert "Math.sin" in payload["html"]
        assert '"expr": "A*sin(w*x)"' in payload["html"]
        assert payload["meta"]["params"]["sliders"][0]["key"] == "A"

    def test_rejects_unknown_function(self):
        with pytest.raises(VizError, match="不支持的函数"):
            builder.build("curve_lab", params={"expr": "foo(x)*2", "sliders": []})

    def test_rejects_undefined_symbol(self):
        with pytest.raises(VizError, match="未定义"):
            builder.build("curve_lab", params={"expr": "y*sin(x)", "sliders": []})

    def test_rejects_non_math_payload(self):
        with pytest.raises(VizError):
            builder.build("curve_lab", params={"expr": "x; alert(1)", "sliders": []})
        with pytest.raises(VizError):
            builder.build("curve_lab", params={"expr": "window", "sliders": []})
        with pytest.raises(VizError):
            builder.build("curve_lab", params={"expr": "x > 2", "sliders": []})

    def test_rejects_reserved_slider_key(self):
        p = dict(self.PARAMS)
        p["sliders"] = [{"key": "window", "min": 0, "max": 1, "step": 0.1, "default": 0}]
        with pytest.raises(VizError, match="滑块名"):
            builder.build("curve_lab", params=p)

    def test_rejects_all_nan_on_range(self):
        # log(x) 在 [0,8] 上 x=0 处无定义，但区间内有效值足够 → 通过；
        # 而 sqrt(x-100) 在 [0,8] 上恒 NaN → 拒绝
        p = dict(self.PARAMS)
        p["expr"] = "sqrt(x-100)"
        p["sliders"] = []
        with pytest.raises(VizError, match="有效值"):
            builder.build("curve_lab", params=p)

    def test_domain_error_ok_when_enough_finite(self):
        # log(x) 在 [0,8]：x=0 一点 NaN，其余有效 → 应通过（模板对 NaN 断线绘制）
        p = dict(self.PARAMS)
        p["expr"] = "log(x)"
        p["sliders"] = []
        payload = builder.build("curve_lab", params=p)
        assert "Math.log" in payload["html"]

    def test_real_model_args_rebuild(self):
        # 回归：实链路中模型传过的真实参数形状（含 x_label/y_label）必须能重建——
        # 曾因 _hint 定义在 build() 嵌套作用域导致 _build_expr NameError
        payload = builder.build("curve_lab", params={
            "expr": "A*exp(-d*x)*sin(w*x)",
            "sliders": [
                {"key": "A", "min": 0.1, "max": 3, "step": 0.1, "default": 1},
                {"key": "w", "min": 0.5, "max": 10, "step": 0.5, "default": 3},
                {"key": "d", "min": 0, "max": 2, "step": 0.05, "default": 0.3},
            ],
            "x_range": [0, 20],
            "x_label": "t", "y_label": "x(t)",
        }, probe_question="把 d 拖到 0，曲线会变成什么样？", reveal_hint="提示：想想 e^-dt 管什么")
        assert payload["meta"]["template"] == "curve_lab"
        assert payload["probe"]["reveal_hint"] == "想想 e^-dt 管什么"
        assert "Math.exp" in payload["html"] and "Math.sin" in payload["html"]


class TestMakeVisualTool:
    def test_returns_summary_without_html(self):
        from app.agent.tools import execute_tool
        out = execute_tool("make_visual", {
            "template": "pid_tuner",
            "probe_question": "把 Kp 拉满会怎样？",
        })
        assert out["ok"] is True
        assert "html" not in out                     # HTML 不进模型上下文
        assert out["html_bytes"] > 0
        assert out["probe_question"] == "把 Kp 拉满会怎样？"

    def test_bad_template_structured_error(self):
        from app.agent.tools import execute_tool
        out = execute_tool("make_visual", {"template": "nope"})
        assert out["ok"] is False
        assert "可用模板" in out["hint"]


def _result_with_viz(args_list: list[dict]) -> AgentResult:
    return AgentResult(
        text="先解释 PID 三个参数各自的脾气……",
        tool_calls=[
            {"tool_name": "make_visual", "args": a, "ok": True,
             "result": {"ok": True, "template": a.get("template"), "title": "x"}}
            for a in args_list
        ],
    )


class TestVizCardInjection:
    def _format(self, result: AgentResult) -> list:
        return format_cards(result, user_text="q", course_id="general")

    def test_interactive_card_appended(self, monkeypatch):
        monkeypatch.setattr(
            cards_mod.glm_client, "chat_structured",
            lambda *a, **k: SimpleNamespace(
                cards=[{"card_type": "understand", "text": "讲解正文"}]
            ),
        )
        out = self._format(_result_with_viz([
            {"template": "pid_tuner", "probe_question": "Kp 拉满会怎样？"},
        ]))
        assert out[0].card_type == "understand"       # 正文卡在前
        assert out[-1].card_type == "interactive"     # 实验卡附后
        blk = out[-1].payload.interactive
        assert '"kp"' in blk.html
        assert blk.probe.question == "Kp 拉满会怎样？"
        assert out[-1].strategy == ["可视化"]

    def test_injection_survives_formatter_failure(self, monkeypatch):
        # 切卡 LLM 挂了也要有实验卡（确定性注入不依赖切卡模型的自觉）
        def _boom(*a, **k):
            raise RuntimeError("llm down")
        monkeypatch.setattr(cards_mod.glm_client, "chat_structured", _boom)
        out = self._format(_result_with_viz([{"template": "harmonic"}]))
        assert out[0].card_type == "text"             # 兜底单卡
        assert out[-1].card_type == "interactive"

    def test_rate_limit_one_per_turn(self, monkeypatch):
        monkeypatch.setattr(
            cards_mod.glm_client, "chat_structured",
            lambda *a, **k: SimpleNamespace(cards=[{"card_type": "text", "text": "t"}]),
        )
        out = self._format(_result_with_viz([
            {"template": "harmonic"}, {"template": "pid_tuner"},
        ]))
        assert sum(1 for c in out if c.card_type == "interactive") == 1
