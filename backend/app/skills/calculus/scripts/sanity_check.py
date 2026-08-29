#!/usr/bin/env python3
"""calculus skill 本地验证脚本。

用法（在 backend/ 目录下）：
    python app/skills/calculus/scripts/sanity_check.py

不依赖 pytest，改完 solver.py 后跑一遍确认没算错。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 允许以脚本方式直接运行：把 backend/ 加进 sys.path
# 路径层级：sanity_check.py → scripts → calculus → skills → app → backend
BACKEND_DIR = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(BACKEND_DIR))

from app.skills.calculus.solver import solve  # noqa: E402

CASES: list[tuple[dict, str]] = [
    ({"task": "integrate", "expression": "x**2"}, "x**3/3"),
    ({"task": "integrate", "expression": "x**2", "lower": "0", "upper": "1"}, "1/3"),
    (
        {"task": "integrate", "expression": "x**2*sin(x)"},
        "-x**2*cos(x) + 2*x*sin(x) + 2*cos(x)",
    ),
    (
        {"task": "derivative", "expression": "sin(x)*exp(x)"},
        "exp(x)*sin(x) + exp(x)*cos(x)",
    ),
    ({"task": "derivative", "expression": "x**4", "order": 2}, "12*x**2"),
    ({"task": "limit", "expression": "sin(x)/x", "lower": "0"}, "1"),
    ({"task": "limit", "expression": "1/x", "lower": "0+"}, "oo"),
]


def main() -> int:
    failed = 0
    for args, expected in CASES:
        result = solve(**args)
        if not result.get("ok"):
            print(f"[FAIL] {args}\n       error={result.get('error')}")
            failed += 1
            continue
        ans = str(result.get("answer", ""))
        if ans == expected:
            print(f"[OK  ] {args}\n       {ans}")
        else:
            print(f"[DIFF] {args}\n       got={ans}\n       exp={expected}")
            failed += 1

    print()
    print("全部通过" if failed == 0 else f"{failed} 个用例未通过")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
