"""知识图谱注册表（可插拔底座）。新增一门课 = 新增一个 Graph 并注册。"""
from __future__ import annotations

from app.core.errors import BizError, ErrorCode
from .autocontrol import AutoControlGraph
from .base import KGNode, KnowledgeGraph
from .os import OSGraph
from .signals import SignalSystemGraph

COURSES: dict[str, type[KnowledgeGraph]] = {
    "os": OSGraph,
    "autocontrol": AutoControlGraph,
    "signals": SignalSystemGraph,
}


def get_kg(course_id: str = "os") -> KnowledgeGraph:
    cls = COURSES.get(course_id)
    if cls is None:
        raise BizError(ErrorCode.NOT_FOUND, f"未找到课程知识图谱: {course_id}")
    return cls()


def list_courses() -> list[dict]:
    return [
        {"id": cid, "name": cls.course_name, "subject": cls.subject}
        for cid, cls in COURSES.items()
    ]


__all__ = ["COURSES", "get_kg", "list_courses", "KGNode", "KnowledgeGraph"]
