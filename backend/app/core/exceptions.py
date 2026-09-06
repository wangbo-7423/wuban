"""全局异常处理器。

注册四个处理器到 FastAPI app（body 恒为 `{code, message, data}` 统一体）：
1. `BizError`                → 真实 HTTP 状态码（4xx/5xx 业务码即状态码；
                                1xxx/2xxx 纯业务码由 HTTP 500 承载，body.code 保留业务码）
2. `RequestValidationError`  → 422 + 字段级 `data.error`
3. `StarletteHTTPException`  → 对应 HTTP 状态码 + 统一体
4. `Exception`               → 500 兜底（不泄漏内部细节）

历史上业务错误曾统一包成 HTTP 200，导致前端只能靠 body.code 识别失败、
登录态过期变成「僵尸登录态」（http.ts 注释有案底）——现业务码即 HTTP 状态码。

参考：`docs/02-API接口文档.md` §1.3。
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .errors import BizError, ErrorCode

SKIP_LOC = ("body", "query", "path", "header", "cookie")


def _collect_validation_errors(exc: RequestValidationError) -> dict[str, list[str]]:
    """把 Pydantic 校验错误转成 `{字段: [msg,...]}` 的结构。"""
    errors: dict[str, list[str]] = {}
    for e in exc.errors():
        loc = ".".join(str(x) for x in e.get("loc", []) if x not in SKIP_LOC)
        key = loc or "body"
        errors.setdefault(key, [])
        errors[key].append(e["msg"])
    return errors


def _http_status(code: int) -> int:
    """业务码 → HTTP 状态码。

    `ErrorCode` 里 400~599 段直接复用标准 HTTP 语义（BAD_REQUEST=400 等）；
    1xxx/2xxx 是纯业务码，不是合法 HTTP 状态，统一由 500 承载——
    body.code 仍带原业务码，调用方可细分。
    """
    return code if 400 <= code <= 599 else 500


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(BizError)
    async def biz_handler(request: Request, exc: BizError) -> JSONResponse:
        data = exc.data if exc.data is not None else {"error": {}}
        return JSONResponse(
            status_code=_http_status(exc.code),
            content={"code": exc.code, "message": exc.message, "data": data},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "code": int(ErrorCode.VALIDATION),
                "message": "参数校验失败",
                "data": {"error": _collect_validation_errors(exc)},
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.status_code, "message": str(exc.detail), "data": {"error": {}}},
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={
                "code": int(ErrorCode.INTERNAL),
                "message": "服务器内部错误",
                "data": {"error": {}},
            },
        )
