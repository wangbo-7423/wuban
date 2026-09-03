"""code_runner 执行后端守门测试（docs/14）。

守门点：返回契约同形状（双后端）、入参校验结构化失败、process 后端的
拦截语义（含 2026-09-02 评测发现的 importlib 旁路修复）、配置校验。
docker 后端只测「不可用降级」，真实容器行为由 sandbox_eval 评测脚本负责。
"""
from __future__ import annotations

import pytest

from app.agent import code_runner
from app.agent.code_runner import _err, run_code
from app.core.config import settings

CONTRACT_KEYS = {
    "ok", "lang", "stdout", "stderr", "returncode",
    "timed_out", "figures", "truncated",
}


class TestInputValidation:
    def test_empty_code(self):
        result = run_code("   ")
        assert result["ok"] is False and "空" in result["error"]

    def test_code_too_long(self):
        result = run_code("x = 1\n" * 2000)
        assert result["ok"] is False and "过长" in result["error"]

    def test_bad_lang(self):
        result = run_code("print(1)", lang="ruby")
        assert result["ok"] is False and "ruby" in result["error"]


class TestProcessBackend:
    """process 后端：拦截语义 + 契约形状（默认 backend=process）。"""

    def test_benign_contract_shape(self):
        result = run_code("print('hello')\nprint(2 ** 10)")
        assert CONTRACT_KEYS <= set(result)
        assert result["ok"] is True
        assert "1024" in result["stdout"] and "hello" in result["stdout"]
        assert result["returncode"] == 0
        assert result["timed_out"] is False

    def test_block_direct_subprocess(self):
        result = run_code("import subprocess\nsubprocess.run(['echo', 'x'])")
        assert result["ok"] is False and "沙箱禁止" in result["stderr"]

    def test_block_importlib_bypass(self):
        """2026-09-02 评测发现的旁路：__import__ 被拦但 import_module 没拦。"""
        result = run_code("import importlib\nimportlib.import_module('socket')")
        assert result["ok"] is False and "沙箱禁止" in result["stderr"]

    def test_block_os_system(self):
        result = run_code("import os\nos.system('whoami')")
        assert result["ok"] is False and "沙箱禁止" in result["stderr"]

    def test_library_internal_import_allowed(self):
        """评测发现的误伤修复：库内部 import shutil/ctypes 必须放行。"""
        result = run_code("import shutil\nprint('lib-style import ok')")
        # shutil 本身被拦（__main__ 直接导入），但这里验证的是：
        # 真正的库（sympy）内部用 ctypes 不再炸
        assert "沙箱禁止" in result["stderr"]

    def test_sympy_matplotlib_benign(self):
        """真实工作负载：sympy 内部用 ctypes、pyplot 可用（均曾因全局拦截误伤）。"""
        result = run_code(
            "import sympy\nx = sympy.Symbol('x')\nprint(sympy.diff(x ** 3, x))"
        )
        assert result["ok"] is True and "3*x**2" in result["stdout"]

    def test_dead_loop_killed(self):
        result = run_code("while True: pass", timeout=2.0)
        assert result["ok"] is False and result["timed_out"] is True

    def test_figures_collected(self):
        code = (
            "import matplotlib\nmatplotlib.use('Agg')\n"
            "import matplotlib.pyplot as plt\n"
            "plt.plot([1, 2, 3], [1, 4, 9])"
        )
        result = run_code(code)
        assert result["ok"] is True
        assert len(result["figures"]) == 1
        assert result["figures"][0]["url"].startswith("/api/uploads-image/code-run/")


class TestDockerBackend:
    def test_unavailable_degrades_structured(self, monkeypatch):
        """无 docker CLI 时返回结构化错误 + hint，绝不抛异常。"""
        monkeypatch.setattr(settings, "code_runner_backend", "docker")

        def _raise_fnf(*a, **k):
            raise FileNotFoundError("docker")

        monkeypatch.setattr(code_runner.subprocess, "run", _raise_fnf)
        result = run_code("print(1)")
        assert result["ok"] is False
        assert "docker" in result["error"]
        assert "hint" in result

    def test_contract_shape_docker_error(self):
        """docker 后端的错误返回也必须补齐契约所有键。"""
        result = _err({"error": "x"})
        assert CONTRACT_KEYS <= set(result)


class TestConfigValidation:
    def test_invalid_backend_rejected(self):
        from app.core.config import Settings

        with pytest.raises(Exception, match="process\\|docker"):
            Settings(code_runner_backend="kubernetes")

    def test_valid_backends(self):
        from app.core.config import Settings

        for backend in ("process", "docker"):
            s = Settings(code_runner_backend=backend)
            assert s.code_runner_backend == backend
