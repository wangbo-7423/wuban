"""健康检查与地基验证端点。

这些端点用于验证「统一响应 + 全局异常 + Pydantic 校验」都生效，
实际业务可按 `docs/02-API接口文档.md` 分域扩展（auth/courses/runs/...）。
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.errors import BizError, ErrorCode
from app.core.response import ok

router = APIRouter()


class DemoIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    age: int = Field(ge=0, le=120)


@router.get("/health")
def health() -> dict:
    """健康检查。"""
    return ok({"status": "up"})


@router.post("/health/demo-ok")
def demo_ok(payload: DemoIn) -> dict:
    """正常业务：返回统一响应体，示范参数校验通过。"""
    return ok({"name": payload.name, "age": payload.age})


@router.get("/health/demo-biz-error")
def demo_biz_error() -> dict:
    """业务异常：应返回 200 + code 409 + message。"""
    raise BizError(ErrorCode.CONFLICT, "课程已存在")


@router.get("/health/demo-500")
def demo_500() -> dict:
    """未捕获异常：兜底处理器应返回 500 统一体。"""
    raise RuntimeError("boom")


@router.get("/health/demo-http-404")
def demo_http_404() -> dict:
    """HTTPException：应返回 404 统一体。"""
    from fastapi import HTTPException

    raise HTTPException(status_code=404, detail="资源不存在")
