"""Skill 层：按学科场景封装的「多步求解流程 + 教学提示」单元。

与 `app/agent/tools.py`（能力层）的分工：
- 工具层回答「怎么算这个表达式」；
- skill 层回答「怎么教这个知识点」。

对外只需用 `app.skills.registry` 的四个函数：
`list_skills` / `get_skill` / `execute_skill` / `skill_schemas`。
"""
from app.skills.base import SkillSpec
from app.skills.registry import (
    execute_skill,
    get_skill,
    list_skills,
    skill_schemas,
)

__all__ = [
    "SkillSpec",
    "list_skills",
    "get_skill",
    "execute_skill",
    "skill_schemas",
]
