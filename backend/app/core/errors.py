"""错误码与业务异常。

- `ErrorCode`：统一错误码（对应 `docs/02-API接口文档.md` §1.3）。
- `BizError`：业务异常。控制器只需 `raise BizError(code, msg, data)`，
  全局异常处理器会把它转成统一响应体，控制器永不手拼响应。
"""
from __future__ import annotations

from enum import IntEnum
from typing import Any


class ErrorCode(IntEnum):
    OK = 0
    BAD_REQUEST = 400
    UNAUTHORIZED = 401
    FORBIDDEN = 403
    NOT_FOUND = 404
    CONFLICT = 409
    VALIDATION = 422
    RATE_LIMIT = 429
    INTERNAL = 500
    # 业务码
    PIPELINE_BIZ = 1001        # 学情/管线业务错误（如证据不足）
    COGNITIVE_ENGINE = 1002    # 认知引擎不可用
    RAG_ERROR = 2001           # RAG 检索失败（未索引/无命中）
    APPROVAL_STATE = 2002      # HITL 审批状态冲突


class BizError(Exception):
    """业务异常：携带 code/message/data，由全局处理器统一转响应体。"""

    def __init__(self, code: ErrorCode | int, message: str, data: Any = None):
        self.code = int(code)
        self.message = message
        self.data = data
        super().__init__(message)
