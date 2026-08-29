"""Skill 层协议：把「某个学科的解题流程 + 教学提示」封装成一个可调用单元。

## 与 ToolSpec 的区别（这是本层存在的原因）

| | ToolSpec（能力） | SkillSpec（场景专家） |
|---|---|---|
| 回答的问题 | 「怎么算这个表达式」 | 「怎么教这个知识点」 |
| 结构 | 一个函数算一个结果 | 多步流程 + 该场景的教学策略 |
| 例 | `sympy_calc(op="integrate")` | 级数：判类型 → 选判别法 → 分步算 → 附常见误区 |

像「微分方程要先判类型再选解法」「级数要先判别再选判别法」这类**多步推理**，
塞进一个通用工具就会退化成一坨 if-else；拆成 skill 后每个场景各自清晰。

## 教学提示怎么给模型

每个 skill 目录下的 `SKILL.md` 是该场景的教学策略（讲解顺序、常见误区、
脚手架提示）。`registry.execute_skill()` 在**执行成功后**把它注入返回值的
`teaching_hints` 字段——模型调一次工具就同时拿到「结果」和「怎么教」，
无需额外调用。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class SkillSpec:
    """一个学科场景的定义。"""

    name: str                                   # 工具名（GLM 调用时用）
    title: str                                  # 中文名（日志 / 调试用）
    description: str                            # 给 GLM 的「何时用」说明
    schema: dict[str, Any]                      # OpenAI function-calling schema
    solve: Callable[..., dict[str, Any]]        # 多步求解函数
    prompt_file: str = "SKILL.md"               # 教学提示文件名
    module_dir: Path | None = field(default=None, compare=False)

    @property
    def prompt_path(self) -> Path | None:
        if self.module_dir is None:
            return None
        return self.module_dir / self.prompt_file
