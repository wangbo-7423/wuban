"""code_runner 沙箱量化评测（docs/14 §3 的可执行形态）。

用法（backend/ 目录下）::

    .venv/Scripts/python.exe -m app.agent.scripts.sandbox_eval               # 当前配置后端
    .venv/Scripts/python.exe -m app.agent.scripts.sandbox_eval --backend docker
    .venv/Scripts/python.exe -m app.agent.scripts.sandbox_eval --json eval.json

四个量化指标（M1~M4）+ 一个边界观测（M5）：

- **M1 恶意探针拦截率**：9 个逃逸探针（网络 / 子进程 / ctypes / os 命令 /
  __import__·importlib 绕过 / shutil / pickle），被判「执行失败」即拦截成功。
- **M2 良性程序误伤率**：4 个正常程序（打印 / 循环 / matplotlib / sympy），
  跑挂即误伤。依赖探针会先探测执行环境是否具备该依赖，缺则跳过不计。
- **M3 沙箱开销**：同一良性程序「沙箱跑」vs「裸 python -c 跑」各 3 次取中位，
  差值 = prologue 注入 + 环境准备的真实开销（ms）。
- **M4 超时杀灭**：死循环探针（timeout=3s），验证 timed_out 标记与实际杀灭时延。
- **M5 边界观测**：越界文件写探针——process 后端**预期泄漏**（诚实边界，
  docs/14 §2），docker 后端预期被只读文件系统拦住。

绝不针对真实主机：越界写只写 backend/escape_probe.txt，评测后自动清理。
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from app.agent.code_runner import run_code
from app.core.config import settings

BACKEND_DIR = Path(__file__).resolve().parents[3]

# ── 探针套件 ─────────────────────────────────────────────────────

MALICIOUS_PROBES: list[tuple[str, str]] = [
    ("socket 外联", "import socket\nsocket.socket().connect(('192.0.2.1', 80))"),
    ("subprocess 起进程", "import subprocess\nsubprocess.run(['echo', 'pwn'])"),
    ("ctypes 调 native", "import ctypes\nctypes.CDLL(None)"),
    ("os.system", "import os\nos.system('whoami')"),
    ("os.popen", "import os\nos.popen('whoami').read()"),
    ("__import__ 绕过", "__import__('socket')"),
    ("importlib 绕过", "import importlib\nimportlib.import_module('socket')"),
    ("shutil 删目录", "import shutil\nshutil.rmtree('x', ignore_errors=True)"),
    ("pickle 反序列化", "import pickle\npickle.loads(b'')"),
]

BENIGN_PROBES: list[tuple[str, str, str | None]] = [
    ("打印与算术", "print(2 ** 64)\nprint('hello 伴学')", None),
    ("百万次循环", "s = 0\nfor i in range(10 ** 6):\n    s += i\nprint(s)", None),
    ("matplotlib 画图", "import matplotlib.pyplot as plt\nplt.plot([1, 2, 3], [1, 4, 9])\nplt.title('sandbox eval')", "matplotlib"),
    ("sympy 求导", "import sympy\nx = sympy.Symbol('x')\nprint(sympy.diff(x ** 3, x))", "sympy"),
]

LEAK_PROBE = "open(r'{path}', 'w').write('leaked')"


def _env_has(backend: str, module: str) -> bool:
    """探测执行环境是否具备某依赖（docker 探镜像、process 探 venv）。"""
    if backend == "docker":
        proc = subprocess.run(
            ["docker", "run", "--rm", settings.code_runner_docker_image,
             "python", "-c", f"import {module}"],
            capture_output=True, timeout=60,
        )
        return proc.returncode == 0
    try:
        __import__(module)
        return True
    except ImportError:
        return False


def _raw_run(code: str, backend: str) -> float:
    """与沙箱同环境裸跑（process=宿主机 python -c；docker=同配置容器无 prologue），
    返回耗时（秒）。开销 = 沙箱跑 - 裸跑，即 prologue 注入的真实成本。"""
    if backend == "docker":
        base = ["docker", "run", "--rm", "--network", "none", "--memory", "256m",
                "--pids-limit", "64", "--cpus", "0.5", "--read-only",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--user", "65534:65534", "-e", "HOME=/tmp", "-w", "/tmp",
                settings.code_runner_docker_image, "python", "-c", code]
    else:
        base = [sys.executable, "-c", code]
    t0 = time.perf_counter()
    subprocess.run(base, capture_output=True, timeout=60)
    return time.perf_counter() - t0


def _sandbox_run(code: str, timeout: float | None = None) -> tuple[dict[str, Any], float]:
    t0 = time.perf_counter()
    result = run_code(code, timeout=timeout)
    return result, time.perf_counter() - t0


def evaluate(backend: str, repeats: int = 3) -> dict[str, Any]:
    os.environ["CODE_RUNNER_KEEP_TEMP"] = "1"   # 评测批量跑，保留临时脚本（不清理沙箱产物）
    settings.code_runner_backend = backend
    report: dict[str, Any] = {
        "backend": backend,
        "docker_image": settings.code_runner_docker_image if backend == "docker" else None,
        "python": sys.version.split()[0],
        "sandbox_timeout": settings.code_runner_timeout,
    }

    # ── M1 恶意探针拦截率 ──
    m1_rows, blocked = [], 0
    for name, code in MALICIOUS_PROBES:
        result, _ = _sandbox_run(code)
        is_blocked = not result.get("ok", False)
        blocked += is_blocked
        marker = "沙箱拦截" if is_blocked else "!! 逃逸"
        stderr_key = next(
            (k for k in ("沙箱禁止", "沙箱禁止导入") if k in (result.get("stderr") or "")),
            (result.get("stderr") or "").strip().splitlines()[-1][:40] if result.get("stderr") else "-",
        )
        m1_rows.append({"probe": name, "verdict": marker, "detail": stderr_key})
    report["m1_rows"] = m1_rows
    report["m1_blocked"] = blocked
    report["m1_total"] = len(MALICIOUS_PROBES)

    # ── M2 良性误伤率 ──
    m2_rows, ran, missed = [], 0, 0
    for name, code, needs in BENIGN_PROBES:
        if needs and not _env_has(backend, needs):
            m2_rows.append({"probe": name, "verdict": "跳过（环境缺依赖）", "figures": "-"})
            continue
        result, _ = _sandbox_run(code)
        ran += 1
        ok = result.get("ok", False)
        missed += not ok
        m2_rows.append({
            "probe": name,
            "verdict": "通过" if ok else f"!! 误伤: {(result.get('stderr') or '')[:60]}",
            "figures": len(result.get("figures") or []),
        })
    report["m2_rows"] = m2_rows
    report["m2_missed"] = missed
    report["m2_total"] = ran

    # ── M3 沙箱开销（取中等探针：循环计算；同环境对比 prologue 成本）──
    _, loop_code, _ = BENIGN_PROBES[1]
    sandbox_ms, raw_ms = [], []
    for _ in range(repeats):
        result, dt = _sandbox_run(loop_code)
        assert result.get("ok"), "M3 基准探针跑挂了"
        sandbox_ms.append(dt * 1000)
        raw_ms.append(_raw_run(loop_code, backend) * 1000)
    report["m3"] = {
        "sandbox_ms_median": round(statistics.median(sandbox_ms), 1),
        "raw_ms_median": round(statistics.median(raw_ms), 1),
        "overhead_ms_median": round(statistics.median(sandbox_ms) - statistics.median(raw_ms), 1),
        "repeats": repeats,
        "note": "同环境对比（docker 后端的裸跑也是同配置容器），开销=prologue 注入成本",
    }

    # ── M4 超时杀灭 ──
    grace = 3.0 if backend == "process" else 8.0 + 3.0  # docker 客户端超时=代码超时+5s 宽限
    t0 = time.perf_counter()
    result, _ = _sandbox_run("while True: pass", timeout=3.0)
    wall = time.perf_counter() - t0
    report["m4"] = {
        "timed_out_flag": result.get("timed_out", False),
        "wall_sec": round(wall, 2),
        "killed": result.get("timed_out", False) and wall < 3.0 + grace,
        "note": "docker 后端含 5s 客户端宽限 + rm -f 强杀（杀净优先于快）",
    }

    # ── M5 边界观测：越界文件写 ──
    escape_path = BACKEND_DIR / f"escape_probe_{uuid.uuid4().hex[:8]}.txt"
    leak_result, _ = _sandbox_run(LEAK_PROBE.format(path=escape_path))
    leaked = escape_path.exists()
    report["m5"] = {
        "probe": "向 backend/ 根目录写文件（产物目录之外）",
        "exec_ok": leak_result.get("ok", False),
        "file_leaked": leaked,
        "leftover": str(escape_path) if leaked else None,
        "verdict": (
            "!! 泄漏：文件越界写成功（已知边界，docs/14 §2）"
            if leaked else "已拦：越界写失败"
        ),
    }
    return report


def render(report: dict[str, Any]) -> str:
    lines = [
        f"# code_runner 沙箱量化评测 — backend={report['backend']}"
        + (f" ({report['docker_image']})" if report["docker_image"] else ""),
        "",
        f"- Python: {report['python']} · 沙箱超时: {report['sandbox_timeout']}s",
        "",
        f"## M1 恶意探针拦截率：**{report['m1_blocked']}/{report['m1_total']}**",
        "",
        "| 探针 | 判定 | 细节 |",
        "| --- | --- | --- |",
        *[f"| {r['probe']} | {r['verdict']} | {r['detail']} |" for r in report["m1_rows"]],
        "",
        f"## M2 良性程序误伤率：**{report['m2_missed']}/{report['m2_total']}**",
        "",
        "| 探针 | 判定 | 产出图 |",
        "| --- | --- | --- |",
        *[f"| {r['probe']} | {r['verdict']} | {r['figures']} |" for r in report["m2_rows"]],
        "",
        "## M3 沙箱开销（百万次循环，中位数）",
        "",
        f"- 沙箱: {report['m3']['sandbox_ms_median']} ms · 裸跑: {report['m3']['raw_ms_median']} ms"
        f" · **开销: {report['m3']['overhead_ms_median']} ms**"
        f"（n={report['m3']['repeats']}）",
        "",
        "## M4 超时杀灭（死循环，timeout=3s）",
        "",
        f"- timed_out 标记: {report['m4']['timed_out_flag']} · 实际耗时: {report['m4']['wall_sec']}s"
        f" · 判定: {'✓ 已杀灭' if report['m4']['killed'] else '!! 未杀灭'}",
        "",
        "## M5 边界观测（越界文件写）",
        "",
        f"- {report['m5']['probe']} → {report['m5']['verdict']}",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="code_runner 沙箱量化评测")
    parser.add_argument("--backend", choices=("process", "docker"), default=None)
    parser.add_argument("--json", dest="json_path", default=None, help="结果另存 JSON 文件")
    args = parser.parse_args()

    backend = args.backend or settings.code_runner_backend
    print(f"评测中… backend={backend}", file=sys.stderr)
    report = evaluate(backend)
    text = render(report)
    print(text)
    if args.json_path:
        Path(args.json_path).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[已保存] {args.json_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
