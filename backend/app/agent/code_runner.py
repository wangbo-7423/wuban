"""code_runner：在受控环境里执行学生提交的代码（工科微项目线核心工具）。

## 它解决什么

「工科微项目线」要求学生把代码 / 仿真贴出来，AI 跑一遍再给**审阅式反馈**。
本模块就是那个「跑一遍」的执行器：把代码写到临时文件，在受控后端里跑，
超时就杀掉，再把 stdout/stderr/退出码/图表回传。

## 执行后端（`CODE_RUNNER_BACKEND`，docs/14）

| 后端 | 隔离级别 | 适用 | 硬化手段 |
| --- | --- | --- | --- |
| `process`（默认） | 子进程级 | dev / 受信环境 | 三禁 prologue + `PYTHONSAFEPATH` + 硬超时 + 输出截断 |
| `docker` | 容器级 | 公网多租户 | `--network none --memory --pids-limit --cpus --read-only --cap-drop ALL` + 非 root + tmpfs |

两个后端**返回契约完全一致**（`{ok, lang, stdout, stderr, returncode,
timed_out, figures, truncated}`），切换只动配置，不改 tool 层一行代码。
docker 后端跑不起（无 daemon / 镜像缺失）时返回结构化错误 + hint，
绝不抛异常打断聊天主链路。

## 安全模型（务必读懂边界）

`process` 后端是**演示级沙箱**，防「粗心 / 学生跑自己代码」，不防「蓄意逃逸」：
1. **禁网络**：`socket.socket` 构造即报错 + 拦截 `socket` 导入；
2. **禁危险入口**：`subprocess / ctypes / shutil / signal / multiprocessing / pickle`
   等模块导入即报错，`os.system / popen / exec* / spawn*` 全部废掉；
3. **子进程隔离** + **硬超时**（超时 terminate）+ **输出截断** + **代码长度上限**；
4. **已知边界（对评委要诚实）**：文件读写未拦（学生代码可写后端进程有权限写的
   任何路径）、无 CPU/内存硬配额——这两条只有容器级后端能补。

`docker` 后端补齐上述边界：文件系统只读（产物目录挂卷例外）、网络物理断开、
内存/进程数/CPU 硬配额、非 root 运行、capability 全部丢弃。
选择依据与量化评测见 `docs/14-code_runner沙箱设计.md`，实测探针脚本
`app/agent/scripts/sandbox_eval.py`。
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

MAX_CODE_LEN = 4000          # 单段代码字符上限
MAX_OUT_CHARS = 4000         # stdout / stderr 各自截断上限
SUPPORTED_LANGS = ("python",)
DOCKER_KILL_GRACE_SEC = 5.0  # docker 客户端超时 = 代码超时 + 该宽限（容器侧杀掉余量）


# ── 沙箱前置：禁用网络与危险入口（拼在学生代码前面）──────────────
# 关键设计（2026-09-02 量化评测发现，docs/14 §4）：拦截必须**按调用方作用域**——
# 全局拦会误伤合法库（matplotlib 内部 import shutil、sympy 探测 gmpy 要 import
# ctypes）；只拦 __main__（学生代码）的直接导入，库内部导入放行。
_SANDBOX_PROLOGUE = textwrap.dedent(
    """
import builtins as _blt
# 拦截危险模块导入（仅学生主代码直接导入；库内部导入放行，防误伤）
_BLOCK_IMPORT = {"subprocess", "ctypes", "shutil", "signal", "multiprocessing",
                 "pty", "resource", "socket", "pickle", "marshal"}
_real_import = _blt.__import__
def _guard_import(name, *a, **k):
    if name.split(".")[0] in _BLOCK_IMPORT:
        # 调用方判定：import 语句（IMPORT_NAME）总会带 globals；显式
        # __import__('x') 可以不带 —— **globals 未知时一律拦截**（缺省拒绝），
        # 只放行「能证明自己不是学生主代码」的库内部导入。
        # （2026-09-02 评测发现：显式调用不带 globals 时旧逻辑会放行 → 逃逸）
        _g = a[0] if len(a) >= 1 else k.get("globals")
        _caller = (_g or {}).get("__name__", "")
        if _g is None or _caller == "__main__":
            raise ImportError(f"沙箱禁止导入模块：{name}")
    return _real_import(name, *a, **k)
_blt.__import__ = _guard_import

# importlib.import_module / _gcd_import 是 __import__ 的旁路，同样按作用域拦
import importlib as _ilb
_real_import_module = _ilb.import_module
def _guard_import_module(name, *a, **k):
    if name.split(".")[0] in _BLOCK_IMPORT:
        import sys as _sys
        if _sys._getframe(1).f_globals.get("__name__") == "__main__":
            raise ImportError(f"沙箱禁止导入模块：{name}")
    return _real_import_module(name, *a, **k)
_ilb.import_module = _guard_import_module

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
# 惰性设计（2026-09-02 量化评测发现，docs/14 §4）：学生没 import pyplot 就完全
# 跳过——实测 postlude 无条件 import matplotlib 给每次执行平添 ~480ms。
_SANDBOX_POSTLUDE = textwrap.dedent(
    """
import json as _json, os as _os, sys as _sys
_fig_dir = _os.environ.get("CODE_RUNNER_FIGDIR", "")
try:
    if "matplotlib.pyplot" in _sys.modules:
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


def _prepare(code: str, lang: str, timeout: float | None) -> tuple[Path, Path, float]:
    """校验入参并落盘脚本，返回 (脚本路径, 产物目录, 实际超时)。抛 ValueError 带结构化文案。"""
    code = (code or "").strip()
    if not code:
        raise ValueError("代码不能为空")
    if len(code) > MAX_CODE_LEN:
        raise ValueError(f"代码过长（{len(code)} > {MAX_CODE_LEN} 字符），请拆分后再提交")
    if lang not in SUPPORTED_LANGS:
        raise ValueError(f"不支持的语言: {lang}（当前仅支持 {SUPPORTED_LANGS}）")

    run_uuid = uuid.uuid4().hex
    fig_dir = Path(settings.upload_dir) / "code-run" / run_uuid
    fig_dir.mkdir(parents=True, exist_ok=True)

    full = _SANDBOX_PROLOGUE + "\n" + code + "\n" + _SANDBOX_POSTLUDE
    tmp_py = fig_dir / "_run.py"
    tmp_py.write_text(full, encoding="utf-8")
    return tmp_py, fig_dir, (timeout or settings.code_runner_timeout)


def _base_env() -> dict[str, str]:
    env = dict(os.environ)
    env["MPLBACKEND"] = "Agg"
    env["PYTHONSAFEPATH"] = "1"          # 阻断通过 cwd 注入恶意 sitecustomize
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _err(payload: dict[str, Any]) -> dict[str, Any]:
    """补齐返回契约里所有键，调用方永远拿到同形状 dict。"""
    out = {
        "ok": False, "lang": "python", "stdout": "", "stderr": "",
        "returncode": None, "timed_out": False, "figures": [], "truncated": False,
    }
    out.update(payload)
    return out


def run_code(code: str, lang: str = "python", timeout: float | None = None) -> dict[str, Any]:
    """在受控环境里执行代码，返回结构化结果（绝不抛异常）。

    Args:
        code: 学生提交的源代码（限长 `MAX_CODE_LEN`）。
        lang: 语言，目前仅支持 `"python"`。
        timeout: 超时秒数（覆盖 config 默认值）。

    Returns:
        `{ok, lang, stdout, stderr, returncode, timed_out, figures, truncated}`；
        失败：`{ok: False, error[, hint]}`。
    """
    try:
        tmp_py, fig_dir, eff_timeout = _prepare(code, (lang or "python").strip().lower(), timeout)
    except ValueError as e:
        return _err({"error": str(e)})

    backend = settings.code_runner_backend
    try:
        if backend == "docker":
            result = _run_in_docker(tmp_py, fig_dir, eff_timeout)
        else:
            result = _run_in_process(tmp_py, fig_dir, eff_timeout)
    except Exception as e:  # noqa: BLE001
        logger.warning("code_runner(%s) 执行异常: %s", backend, e)
        result = _err({"error": f"{type(e).__name__}: {e}"})

    result.setdefault("figures", [])
    result["figures"] = result.get("figures") or _collect_figures(fig_dir)
    # CODE_RUNNER_KEEP_TEMP=1 时保留 _run.py（排障用：看拼装后的完整沙箱脚本）
    if os.environ.get("CODE_RUNNER_KEEP_TEMP") != "1":
        try:
            tmp_py.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
    return result


# ── 后端一：子进程级（dev / 受信环境默认）────────────────────────


def _run_in_process(tmp_py: Path, fig_dir: Path, timeout: float) -> dict[str, Any]:
    env = _base_env()
    env["CODE_RUNNER_FIGDIR"] = str(fig_dir)

    try:
        proc = subprocess.run(
            [sys.executable, str(tmp_py)],
            capture_output=True, text=True, timeout=timeout,
            env=env, cwd=str(fig_dir),
        )
    except subprocess.TimeoutExpired:
        return _err({
            "timed_out": True,
            "error": f"代码执行超时（>{timeout}s），可能存在死循环或太慢，请检查后重试",
        })

    out, trunc = _truncate(proc.stdout or "", MAX_OUT_CHARS)
    err2, _ = _truncate(proc.stderr or "", MAX_OUT_CHARS)
    return {
        "ok": proc.returncode == 0,
        "lang": "python",
        "stdout": out,
        "stderr": err2,
        "returncode": proc.returncode,
        "timed_out": False,
        "figures": _collect_figures(fig_dir),
        "truncated": trunc or len(proc.stderr or "") > MAX_OUT_CHARS,
    }


# ── 后端二：容器级（公网多租户）──────────────────────────────────


def _run_in_docker(tmp_py: Path, fig_dir: Path, timeout: float) -> dict[str, Any]:
    container = f"cbrun_{uuid.uuid4().hex[:12]}"
    cmd = [
        "docker", "run", "--rm", "--name", container,
        "--network", "none",                       # 物理断网（process 后端拦不住的旁路全部失效）
        "--memory", "256m", "--pids-limit", "64", "--cpus", "0.5",   # 硬配额
        "--read-only",                             # 根文件系统只读 → 越界文件写失效
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=32m",
        "--user", "65534:65534",                   # nobody：非 root
        "-e", "HOME=/tmp", "-e", "MPLCONFIGDIR=/tmp/mpl",
        "-e", "CODE_RUNNER_FIGDIR=/work",
        "-v", f"{fig_dir}:/work",                  # 唯一可写：产物目录挂卷
        "-w", "/work",
        settings.code_runner_docker_image,
        "python", "/work/_run.py",
    ]
    env = _base_env()
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout + DOCKER_KILL_GRACE_SEC, env=env,
        )
    except subprocess.TimeoutExpired:
        # docker CLI 被杀不会连带容器，必须显式 rm -f（否则死循环容器会一直吃配额）
        subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=10)
        return _err({
            "timed_out": True,
            "error": f"代码执行超时（>{timeout}s），可能存在死循环或太慢，请检查后重试",
        })
    except FileNotFoundError:
        return _err({
            "error": "docker 后端不可用：宿主机没有 docker CLI",
            "hint": "将 CODE_RUNNER_BACKEND 改回 process，或在部署机安装 docker",
        })

    if proc.returncode not in (0, 1) and "docker" in (proc.stderr or "")[:200].lower() \
            and "Unable to find image" in (proc.stderr or ""):
        return _err({
            "error": f"docker 镜像不存在: {settings.code_runner_docker_image}",
            "hint": "先 docker pull 该镜像，或自建含 matplotlib 的镜像（见 docs/14）",
        })

    out, trunc = _truncate(proc.stdout or "", MAX_OUT_CHARS)
    err2, _ = _truncate(proc.stderr or "", MAX_OUT_CHARS)
    return {
        "ok": proc.returncode == 0,
        "lang": "python",
        "stdout": out,
        "stderr": err2,
        "returncode": proc.returncode,
        "timed_out": False,
        "figures": _collect_figures(fig_dir),
        "truncated": trunc or len(proc.stderr or "") > MAX_OUT_CHARS,
    }


def _collect_figures(fig_dir: Path) -> list[dict[str, str]]:
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
            url = f"/api/uploads-image/code-run/{fig_dir.name}/{name}"
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
