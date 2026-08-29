"""认证 / 用户契约（前后端严格对齐）。

本产品不开教师/邮箱注册：
- 注册只需 `username + nickname + password`；
- role 强制 student，不上送；
- email 字段一律不存在。

字段顺序与命名都是前后端契约的一部分，禁止擅自改动。
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RegisterIn(BaseModel):
    """POST /api/auth/register 入参。"""

    username: str = Field(
        min_length=3,
        max_length=32,
        pattern=r"^[a-zA-Z][a-zA-Z0-9_]*$",
        description="用户名：字母开头，3~32 位字母/数字/下划线",
        examples=["zhangsan"],
    )
    nickname: str = Field(
        min_length=1,
        max_length=32,
        description="昵称，用于前端展示（不等于用户名）",
        examples=["张三"],
    )
    password: str = Field(
        min_length=8,
        max_length=128,
        description="明文密码（传输走 HTTPS，服务端 bcrypt 哈希后丢弃）",
        examples=["S3cret_pwd!"],
    )


class LoginIn(BaseModel):
    """POST /api/auth/login 入参。"""

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class UserOut(BaseModel):
    """当前用户资料（GET /api/auth/me）。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    nickname: str
    avatar_url: str | None = None
    status: str
    created_at: datetime


class TokenOut(BaseModel):
    """POST /api/auth/login & register 出参。"""

    token: str
    expires_in: int  # 秒（前端可基于此设 setTimeout 续签/登出）
    nickname: str
    username: str


class AuthMeOut(BaseModel):
    """GET /api/auth/me 出参：把 token 之外尽量多的用户信息一并给出。

    前端持久化 nickname/avatar_url 即可，无需多次往返。
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    nickname: str
    avatar_url: str | None = None
    status: str
    created_at: datetime
