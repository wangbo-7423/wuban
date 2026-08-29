"""skill 注册表：加载已实现的场景，并在执行成功后注入教学提示。

## 新增一个 skill 只需三步

1. 建目录 `app/skills/<name>/`，内含 `solver.py`（导出 `SKILL`）与 `SKILL.md`；
2. 在 `_SKILL_MODULES` 里加上模块名；
3. 完事。注册、schema 透出、教学提示注入全部自动完成。
"""
from __future__ import annotations

import importlib
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.skills.base import SkillSpec

logger = logging.getLogger(__name__)

# 已实现的场景；后续补齐 series / linear_algebra / probability 时在此追加。
# project_guide 是横切任务型 skill（微项目提议），不绑定某一门学科。
_SKILL_MODULES = ("calculus", "ode", "project_guide")


def _load_all() -> tuple[SkillSpec, ...]:
    specs: list[SkillSpec] = []
    for mod_name in _SKILL_MODULES:
        try:
            mod = importlib.import_module(f"app.skills.{mod_name}")
            specs.append(mod.SKILL)
        except Exception as e:  # noqa: BLE001
            # 单个 skill 加载失败不能拖垮整个应用
            logger.warning("skill [%s] 加载失败，已跳过: %s", mod_name, e)
    return tuple(specs)


@lru_cache(maxsize=1)
def list_skills() -> tuple[SkillSpec, ...]:
    """所有已注册 skill（懒加载 + 缓存）。"""
    return _load_all()


def get_skill(name: str) -> SkillSpec | None:
    for s in list_skills():
        if s.name == name:
            return s
    return None


def _strip_frontmatter(text: str) -> str:
    """剥离 YAML frontmatter。

    frontmatter 里的 name / description / when_to_use 是给人看的元数据，
    注入给模型没有意义（模型已经通过工具 description 知道何时调用），
    所以只把正文部分作为教学提示发出去。
    """
    if not text.startswith("---"):
        return text
    parts = text.split("---", 2)
    if len(parts) >= 3:
        return parts[2].lstrip("\n")
    return text


@lru_cache(maxsize=16)
def _load_prompt(path_str: str) -> str:
    """读取 skill 的教学提示（按路径缓存，避免每次调用都读盘）。"""
    try:
        return _strip_frontmatter(Path(path_str).read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning("读取 skill 提示失败 %s: %s", path_str, e)
        return ""


def execute_skill(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """执行一个 skill。

    与 `tools.execute_tool` 的区别：成功后会把该场景的 `SKILL.md`
    注入到返回值的 `teaching_hints` 字段，让模型一次调用同时拿到
    「计算结果」和「怎么教这个知识点」。

    Returns:
        失败时 `{ok: False, error}`，绝不抛异常。
    """
    skill = get_skill(name)
    if skill is None:
        return {"ok": False, "error": f"未注册的 skill: {name}"}

    try:
        result = skill.solve(**args)
    except TypeError as e:
        # 模型传错参数时给明确提示，便于它下次修正
        return {"ok": False, "error": f"参数错误: {e}"}
    except Exception as e:  # noqa: BLE001
        logger.warning("skill [%s] 执行失败: %s", name, e)
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    if not isinstance(result, dict):
        return {"ok": False, "error": "skill 返回了非结构化结果"}

    if result.get("ok") and skill.prompt_path is not None:
        hints = _load_prompt(str(skill.prompt_path))
        if hints:
            result["teaching_hints"] = hints
    return result


def skill_schemas() -> list[dict[str, Any]]:
    """供 GLM 的 tools 字段使用。"""
    return [s.schema for s in list_skills()]
