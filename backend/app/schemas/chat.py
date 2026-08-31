"""对话 / Agent 调用契约。

字段顺序与命名都是前后端契约的一部分，禁止擅自改动。
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


# ────────────────────────────────────────────────────────────
# 1. 多模态输入部分（与 GLM 5.3 Flash API 直接对齐）
# ────────────────────────────────────────────────────────────


class ImagePart(BaseModel):
    """图片输入：支持三类 url。

    - 公网/对象存储 URL（http(s)://...）
    - base64 data URI（data:image/...;base64,...，前端旧直传兼容）
    - 本地落盘路径（/api/uploads-image/<uuid>.<ext>，由 upload-image 端点返回）
    """

    url: str = Field(description="公网 URL / data:image base64 / /api/uploads-image/ 本地路径")
    detail: Literal["auto", "low", "high"] = "auto"


class TextPart(BaseModel):
    text: str


InputPart = ImagePart | TextPart  # 多模态消息体：文本 + 图片


class ChatIn(BaseModel):
    """POST /api/student/chat 入参。

    `conversation_id` 为空时自动新建。
    `intent` / `course_id` 仅作为软提示，允许模型偏离。
    """

    message: str = Field(min_length=1, max_length=4096, description="用户主文本")
    images: list[ImagePart] = Field(default_factory=list, max_length=4, description="图片（≤4 张）")
    course_id: str = Field(default="general", max_length=64, description="学科/课程标识")
    conversation_id: str | None = Field(default=None, description="已存在会话 ID；为空则新建")


class ChatOut(BaseModel):
    """POST /api/student/chat 出参。"""

    message: dict        # 主卡片（CardMessage.model_dump()）
    extras: list[dict] = Field(default_factory=list)  # 后续卡片（多卡输出）
    thinking: str | None = None
    tool_calls: list[dict] = Field(default_factory=list)
    conversation_id: str
    title: str | None = None
    # 学习上下文回推（GET /student/context 同形状）：course/path/mastery/review_due/cognitive。
    # mastery 语义是「过程性探索深度估计值」，不是考试分数（docs/00 §6 红线）。
    # 聊天结束、卡片落库后重算 cognitive_state 折算生成；重算失败时为 null。
    updated_context: dict | None = None


# ────────────────────────────────────────────────────────────
# 2. 会话
# ────────────────────────────────────────────────────────────


class ConversationOut(BaseModel):
    id: str
    course_id: str | None = None
    title: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class NewConvIn(BaseModel):
    course_id: str = "general"
    title: str | None = None


class ConversationDetail(ConversationOut):
    """GET /api/student/conversations/{id} 出参（含消息列表）。"""

    messages: list[dict] = Field(default_factory=list)


# ────────────────────────────────────────────────────────────
# 3. 练习/选择卡作答闭环（作答留痕 → 审阅反馈 → 画像回推）
# ────────────────────────────────────────────────────────────


class PracticeSubmitIn(BaseModel):
    """POST /api/student/practice/submit 入参。

    `answer` 对 practice 卡是自由作答文本；对 choice 卡是所选项的 value
    （后端会用卡上的 options 把 value 翻译成 label 再给反馈）。
    """

    conversation_id: str = Field(description="所属会话")
    card_id: str = Field(description="练习/选择卡的消息 ID")
    answer: str = Field(min_length=1, max_length=2000, description="学生作答")


class PracticeSubmitOut(BaseModel):
    """POST /api/student/practice/submit 出参。"""

    conversation_id: str
    card: dict = Field(description="更新后的练习/选择卡（payload.meta.attempts 已留痕）")
    feedback: dict = Field(description="assistant 审阅反馈卡（feedback 类型）")
