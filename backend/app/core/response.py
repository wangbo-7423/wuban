"""统一响应体。

所有接口返回结构统一为 `{ code, message, data }`：
- 成功：code=0, message="ok", data=<业务数据>
- 失败：code=业务/错误码, data 内含 `error`（校验错误为字段级）

参考：`docs/02-API接口文档.md` §1.1。
"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class UnifiedResponse(BaseModel, Generic[T]):
    """Unified response envelope."""
    code: int = 0
    message: str = "ok"
    data: T | None = None


def ok(data: Any = None, message: str = "ok") -> dict[str, Any]:
    """成功响应体。"""
    return {"code": 0, "message": message, "data": data}


def fail(code: int, message: str, error: dict | None = None) -> dict[str, Any]:
    """失败响应体（error 默认空 dict）。"""
    return {"code": code, "message": message, "data": {"error": error or {}}}


class PageData(BaseModel, Generic[T]):
    """分页数据模型。"""
    items: list[T]
    total: int
    page: int
    page_size: int
    has_more: bool = False


def page(items: list, total: int, page: int, page_size: int) -> dict[str, Any]:
    """分页响应体。"""
    return {
        "code": 0,
        "message": "ok",
        "data": {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "has_more": page * page_size < total,
        },
    }
