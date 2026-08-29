"""code_runner：在受控子进程里执行学生提交的代码（工科微项目线核心工具）。

## 它解决什么

「工科微项目线」要求学生把代码 / 仿真贴出来，AI 跑一遍再给**审阅式反馈**。
本模块就是那个「跑一遍」的执行器：把代码写到临时文件，用独立的 Python 子进程
跑（与后端主进程隔离），超时就杀掉，再把 stdout/stderr/退出码回传。

## 安全模型（务必读懂边界）

这是**演示级沙箱**，不是多租户对抗级隔离。它做的事情：

1. **禁网络**：把 `socket.socket` 改成「构造即报错」，并拦截 `socket` 模块的导入；
2. **禁危险入口**：拦截 `subprocess / ctypes / shutil.rmtree / signal / multiprocessing`
   等模块的导入，并把 `os.system` / `os.popen` / `os.exec*` / `os.spawn*` 改成报错；
3. **子进程隔离**：代码在独立进程跑，崩了不影响后端；
4. **硬超时**：`subprocess.run(timeout=...)`，超时被 terminate（Windows 上能终止）；
5. **输出截断**：stdout/stderr 各限长，防止刷屏把 GLM 上下文撑爆；
6. **代码长度上限**：防超长输入。

**已知边界（对评委要诚实）**：
- 它防的是「粗心 / 学生跑自己代码」，不是「蓄意逃逸」——有经验的攻击者仍可能
  通过 `ctypes` 之外的旁路（如 `__import__` 残留、文件读写）搞事；
- 不限制 CPU / 内存的硬配额（依赖超时兜底）；
- 因此**只应在本地 / 受信环境开启**（默认 `enable_code_runner=True`，但可一键关）。

如果要上生产多租户，应换成容器 / gVisor / WASM 这类真正隔离的方案——这里不实现。
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

MAX_CODE_LEN = 4000          # 单段代码字符上限
MAX_OUT_CHARS = 4000         # stdout / stderr 各自截断上限
SUPPORTED_LANGS = ("python",)


# ── 沙箱前置：禁用网络与危险入口（拼在学生代码前面）──────────────
_SANDBOX_PROLOGUE = textwrap.dedent(
    """
import builtins as _blt
# 拦截危险模块导入
_BLOCK_IMPORT = {"subprocess", "ctypes", "shutil", "signal", "multiprocessing",
                 "pty", "resource", "socket", "pickle", "marshal"}
_real_import = _blt.__import__
def _guard_import(name, *a, **k):
    if name.split(".")[0] in _BLOCK_IMPORT:
        raise ImportError(f"沙箱禁止导入模块：{name}")
    return _real_import(name, *a, **k)
_blt.__import__ = _guard_import

# 即便绕过 import 拦截，也直接废掉网络与系统命令
import os as _os
_os.system = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("沙箱禁止系统命令(os.system)"))
_os.popen = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("沙箱禁止系统命令(os.popen)"))
for _n in ("execl", "execle", "execlp", "execlpe", "execv", "execve",
           "execvp", "execvpe", "spawnl", "spawnle", "spawnlp", "spawnlpe",
           "spawnv", "spawnve", "spawnvp", "spawnvpe"):
    if hasattr(_os, _n):
        setattr(_os, _n, lambda *a, **k: (_ for _ in ()).throw(RuntimeError("沙箱禁止进程操作")))
try:
    import socket as _sock
    _sock.socket = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("沙箱禁止网络访问"))
except Exception:
    pass
"""
)


# ── 沙箱后置：收集 matplotlib 图（若装了 matplotlib 就导出 PNG）──
_SANDBOX_POSTLUDE = textwrap.dedent(
    """
import json as _json, os as _os
_fig_dir = _os.environ.get("CODE_RUNNER_FIGDIR", "")
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    _saved = []
    for _i, _num in enumerate(plt.get_fignums()):
        _fig = plt.figure(_num)
        _p = _os.path.join(_fig_dir, f"fig_{_i}.png")
        _fig.savefig(_p, dpi=90, bbox_inches="tight")
        _saved.append(_p)
    if _saved:
        with open(_os.path.join(_fig_dir, "_manifest.json"), "w") as _mf:
            _json.dump({"figures": _saved}, _mf)
    plt.close("all")
except Exception:
    pass
"""
)


def _truncate(s: str, limit: int) -> tuple[str, bool]:
    if s is None:
        return "", False
    if len(s) <= limit:
        return s, False
    return s[:limit] + f"\n...（输出过长，已截断至 {limit} 字符）", True


def run_code(code: str, lang: str = "python", timeout: float | None = None) -> dict[str, Any]:
    """在受控子进程里执行代码，返回结构化结果。

    Args:
        code: 学生提交的源代码（限长 `MAX_CODE_LEN`）。
        lang: 语言，目前仅支持 `"python"`。
        timeout: 超时秒数（覆盖 config 默认值）。

    Returns:
        成功：`{ok, lang, stdout, stderr, returncode, timed_out, figures, truncated}`；
        失败：`{ok: False, error}` —— 绝不抛异常。
    """
    code = (code or "").strip()
    if not code:
        return {"ok": False, "error": "代码不能为空"}
    if len(code) > MAX_CODE_LEN:
        return {
            "ok": False,
            "error": f"代码过长（{len(code)} > {MAX_CODE_LEN} 字符），请拆分后再提交",
        }
    lang = (lang or "python").strip().lower()
    if lang not in SUPPORTED_LANGS:
        return {
            "ok": False,
            "error": f"不支持的语言: {lang}（当前仅支持 {SUPPORTED_LANGS}）",
        }

    timeout = timeout or settings.code_runner_timeout

    # 运行产物目录（持久化在 upload_dir/code-run/<uuid>/，可经 /api/uploads-image 出图）
    run_uuid = os.path.basename(__import__("uuid").uuid4().hex)
    fig_dir = Path(settings.upload_dir) / "code-run" / run_uuid
    fig_dir.mkdir(parents=True, exist_ok=True)

    full = _SANDBOX_PROLOGUE + "\n" + code + "\n" + _SANDBOX_POSTLUDE
    tmp_py = fig_dir / "_run.py"
    tmp_py.write_text(full, encoding="utf-8")

    env = dict(os.environ)
    env["CODE_RUNNER_FIGDIR"] = str(fig_dir)
    env["MPLBACKEND"] = "Agg"
    env["PYTHONSAFEPATH"] = "1"          # 阻断通过 cwd 注入恶意 sitecustomize
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    try:
        proc = subprocess.run(
            [sys.executable, str(tmp_py)],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            cwd=str(fig_dir),
        )
        out, err = _truncate(proc.stdout or "", MAX_OUT_CHARS)
        err2, _ = _truncate(proc.stderr or "", MAX_OUT_CHARS)
        figures = _collect_figures(fig_dir, run_uuid)

        return {
            "ok": proc.returncode == 0,
            "lang": lang,
            "stdout": out,
            "stderr": err2,
            "returncode": proc.returncode,
            "timed_out": False,
            "figures": figures,
            "truncated": (
                len(proc.stdout or "") > MAX_OUT_CHARS
                or len(proc.stderr or "") > MAX_OUT_CHARS
            ),
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "lang": lang,
            "timed_out": True,
            "error": f"代码执行超时（>{timeout}s），可能存在死循环或太慢，请检查后重试",
            "stdout": "",
            "stderr": "",
            "returncode": None,
            "figures": [],
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("code_runner 执行异常: %s", e)
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        # 删除临时代码文件（图保留以便前端展示）
        try:
            tmp_py.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass


def _collect_figures(fig_dir: Path, run_uuid: str) -> list[dict[str, str]]:
    """读 _manifest.json，把保存的 PNG 整理成可访问的 URL 列表。"""
    manifest = fig_dir / "_manifest.json"
    if not manifest.exists():
        return []
    try:
        import json

        data = json.loads(manifest.read_text(encoding="utf-8"))
        figs: list[dict[str, str]] = []
        for path in data.get("figures", []):
            name = os.path.basename(path)
            url = f"/api/uploads-image/code-run/{run_uuid}/{name}"
            figs.append({"name": name, "url": url})
        return figs
    except Exception:  # noqa: BLE001
        return []


def code_runner_schema() -> dict[str, Any]:
    """OpenAI function-calling 形态的 JSON Schema。"""
    return {
        "type": "function",
        "function": {
            "name": "code_runner",
            "description": (
                "在受控沙箱里执行学生提交的 Python 代码（工科微项目线核心）。"
                "用于：学生贴出仿真 / 脚本后，你跑一遍看输出或报错，再给审阅式反馈；"
                "也用于「预测 → 验证」——学生先猜结果，你用它真的跑出来对比。"
                "沙箱禁网络、禁系统命令、有硬超时；返回 stdout / stderr / 退出码 / 可选图表。"
                "不要让它跑无限循环或训练大模型之类重活。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "要执行的 Python 源码（建议纯 Python，可含 matplotlib 绘图）",
                    },
                    "lang": {
                        "type": "string",
                        "description": "语言，固定 'python'",
                        "default": "python",
                    },
                },
                "required": ["code"],
            },
        },
    }
