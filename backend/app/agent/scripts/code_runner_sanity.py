"""code_runner 本地验证（不依赖 pytest，直接 `python 本文件` 跑）。

层级：scripts/ → agent → app → backend  → 所以 backend 目录是 parents[3]。
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BACKEND_DIR))

from app.agent.code_runner import run_code  # noqa: E402


def _check(cond: bool, msg: str) -> None:
    print(("✅ " if cond else "❌ ") + msg)
    if not cond:
        raise SystemExit(f"校验失败: {msg}")


def main() -> None:
    # 1. 正常输出
    r = run_code("print('hello', 1+2)")
    _check(r["ok"] and r["returncode"] == 0, "正常代码 ok=True 且退出码 0")
    _check("hello 3" in r["stdout"], "stdout 捕获到 print 结果")

    # 2. 运行时报错
    r = run_code("raise ValueError('boom')")
    _check(not r["ok"] and r["returncode"] != 0, "异常代码 ok=False 且退出码非 0")
    _check("boom" in r["stderr"], "stderr 捕获到异常信息")

    # 3. 网络拦截（socket 模块被禁止导入；学生代码无法联网）
    r = run_code("import socket\ns = socket.socket()\nprint('should not reach')")
    _check(not r["ok"], "尝试 import socket 被沙箱拦截（ok=False）")
    _check(
        "沙箱禁止导入模块" in r["stderr"] or "ImportError" in r["stderr"],
        "stderr 说明 socket 被禁",
    )

    # 4. 系统命令拦截
    r = run_code("import os\nos.system('echo hacked')")
    _check(not r["ok"], "os.system 被沙箱拦截（ok=False）")

    # 5. 超时（传小超时，避免真等 15s）
    r = run_code("while True:\n    pass", timeout=1)
    _check(r.get("timed_out") is True, "死循环被超时拦截")

    # 6. 长度上限
    r = run_code("x=" + "1" * 5000)
    _check(not r["ok"] and "过长" in r["error"], "超长代码被拒")

    print("\ncode_runner sanity_check 全部通过 ✅")


if __name__ == "__main__":
    main()
