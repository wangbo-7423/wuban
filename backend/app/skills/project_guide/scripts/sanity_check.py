"""project_guide skill 本地验证（不依赖 pytest，直接 `python 本文件` 跑）。

层级：scripts/ → project_guide → skills → app → backend
所以 backend 目录是 parents[4]。
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(BACKEND_DIR))

from app.skills.project_guide.solver import solve  # noqa: E402


def _check(cond: bool, msg: str) -> None:
    print(("✅ " if cond else "❌ ") + msg)
    if not cond:
        raise SystemExit(f"校验失败: {msg}")


def main() -> None:
    # 1. 命中 topic
    r = solve(topic="傅里叶")
    _check(r["ok"] and r["matched"], "topic='傅里叶' 命中")
    _check(len(r["options"]) >= 2, "命中后返回 ≥2 个选项")
    opt = r["options"][0]
    _check(all(k in opt for k in ("title", "minutes", "goal", "scaffolding", "pitfalls", "done_when")),
           "选项字段齐全(scaffolding/pitfalls/done_when)")
    _check(len(r["review_checklist"]) >= 3, "带审阅清单")

    # 2. 别名命中
    _check(solve(topic="PID 控制")[ "matched"], "别名 'PID 控制' 命中")
    _check(solve(topic="操作系统进程")[ "matched"], "别名 '操作系统进程' 命中")
    _check(solve(topic="运放电路")[ "matched"], "别名 '运放电路' 命中")

    # 3. 难度过滤
    easy = solve(topic="傅里叶", level="easy")
    _check(len(easy["options"]) == 1, "level=easy 只返回 1 项")

    # 4. 未命中返回建议
    miss = solve(topic="量子力学")
    _check(miss["ok"] and not miss["matched"], "未命中 ok 且 matched=False")
    _check(len(miss["suggestions"]) == 4, "未命中返回 4 个可提议 topic")

    print("\nproject_guide sanity_check 全部通过 ✅")


if __name__ == "__main__":
    main()
