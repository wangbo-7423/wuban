#!/usr/bin/env python3
"""ode skill 本地验证脚本。

用法（在 backend/ 目录下）：
    python app/skills/ode/scripts/sanity_check.py

重点验证：类型判别是否落在「教学优先级」上（如 y' + 2y = e^x 应判为
一阶线性而非 sympy 默认的恰当方程），以及初值条件是否生效。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 路径层级：sanity_check.py → scripts → ode → skills → app → backend
BACKEND_DIR = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(BACKEND_DIR))

from app.skills.ode.solver import solve  # noqa: E402

CASES: list[tuple[dict, str, str]] = [
    # (参数, 期望类型, 期望答案)
    (
        {"equation": "Eq(Derivative(y(x), x), y(x))"},
        "separable",
        "Eq(y(x), C1*exp(x))",
    ),
    (
        {"equation": "Derivative(y(x), x) + 2*y(x) - exp(x)"},
        "1st_linear",
        "Eq(y(x), C1*exp(-2*x) + exp(x)/3)",
    ),
    (
        {"equation": "Eq(Derivative(y(x), x), y(x))", "ics": "y(0)=3"},
        "separable",
        "Eq(y(x), 3*exp(x))",
    ),
]


def main() -> int:
    failed = 0
    for args, expected_type, expected_answer in CASES:
        result = solve(**args)
        if not result.get("ok"):
            print(f"[FAIL] {args['equation']}\n       error={result.get('error')}")
            failed += 1
            continue

        got_type = result.get("ode_type", "")
        got_ans = str(result.get("answer", ""))
        type_ok = got_type == expected_type
        ans_ok = got_ans == expected_answer

        if type_ok and ans_ok:
            print(f"[OK  ] {args['equation']}")
            print(f"       type={got_type} | {result.get('method')}")
            print(f"       {got_ans}")
        else:
            failed += 1
            print(f"[DIFF] {args['equation']}")
            if not type_ok:
                print(f"       type got={got_type} exp={expected_type}")
            if not ans_ok:
                print(f"       ans  got={got_ans}\n            exp={expected_answer}")

    print()
    print("全部通过" if failed == 0 else f"{failed} 个用例未通过")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
