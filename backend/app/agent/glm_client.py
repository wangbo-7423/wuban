"""GLM 5.3 Flash Agent 客户端：把官方 zai-sdk 包成业务侧易用的对象。

特性：
- 自动从 `settings.glm_*` 读取配置；
- 支持「思维链」(thinking)、多模态（image_url）、工具调用（tools）、流式输出（stream）；
- 统一异常 → BizError，便于上层处理。

参考：
- 智谱官方文档：https://open.bigmodel.cn/cn/api/introduction
- 调用形态与 OpenAI Chat Completions 兼容，便于以后切模型。
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, Iterator

from zai import ZhipuAiClient

from app.core.config import settings
from app.core.errors import BizError, ErrorCode

logger = logging.getLogger(__name__)


class GLMUnavailable(BizError):
    """GLM 不可用 / 调用失败（业务码 1002）"""

    def __init__(self, message: str = "AI 模型暂不可用，请稍后再试"):
        super().__init__(ErrorCode.COGNITIVE_ENGINE, message)


# ────────────────────────────────────────────────────────────
# 1. 客户端单例
# ────────────────────────────────────────────────────────────


def _build_client() -> ZhipuAiClient:
    # max_retries=1：只重试 1 次。长回答单次生成可能接近超时上限，
    # 默认 3 次重试会把最坏等待放大到 4 倍，前端早已放弃。
    return ZhipuAiClient(
        api_key=settings.glm_api_key,
        base_url=settings.glm_base_url,
        timeout=settings.glm_timeout_sec,
        max_retries=1,
    )


_client: ZhipuAiClient | None = None


def get_client() -> ZhipuAiClient:
    global _client
    if _client is None:
        _client = _build_client()
    return _client


# ────────────────────────────────────────────────────────────
# 2. 消息结构辅助
# ────────────────────────────────────────────────────────────


def text_message(role: str, content: str) -> dict[str, Any]:
    """纯文本消息。"""
    return {"role": role, "content": content}


def multimodal_message(role: str, text: str, image_urls: list[str] | None = None) -> dict[str, Any]:
    """多模态消息：文本 + 多张图片。GLM 兼容 OpenAI 的 content-list 形态。

    image_urls：每条要么是 http(s) URL，要么是 data:image/...;base64, 开头。
    """
    parts: list[dict[str, Any]] = []
    for url in image_urls or []:
        parts.append({"type": "image_url", "image_url": {"url": url}})
    parts.append({"type": "text", "text": text})
    return {"role": role, "content": parts}


# ────────────────────────────────────────────────────────────
# 3. 同步对话
# ────────────────────────────────────────────────────────────


def chat(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    stream: bool = False,
    response_format: dict[str, Any] | None = None,
    enable_thinking: bool | None = None,
) -> Any:
    """单次对话调用。

    Args:
        messages: OpenAI 形态消息列表，可包含 system / user / assistant / tool。
        tools: 工具定义（OpenAI function-calling 形态）。
        tool_choice: "auto" / "none" / {"name": "..."}。
        temperature: 覆盖 settings。
        max_tokens: 覆盖 settings。
        stream: 是否流式。
        response_format: 结构化输出，如 {"type": "json_object"}。
        enable_thinking: 覆盖 settings 的 thinking 开关（False 时省略 thinking 参数）。

    Returns:
        当 stream=False：原始 ChatCompletion 对象；
        当 stream=True：Iterator[chunk]，每个 chunk 含 choices[0].delta。
    """
    kwargs: dict[str, Any] = {
        "model": settings.glm_model,
        "messages": messages,
        "stream": stream,
    }
    if enable_thinking is True:
        kwargs["thinking"] = {"type": "enabled"}
    elif enable_thinking is False:
        pass  # 显式关闭：不发送 thinking 参数
    elif settings.glm_enable_thinking:
        kwargs["thinking"] = {"type": "enabled"}
    if response_format is not None:
        kwargs["response_format"] = response_format
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = tool_choice or "auto"
    if temperature is not None:
        kwargs["temperature"] = temperature
    else:
        kwargs["temperature"] = settings.glm_temperature
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    else:
        kwargs["max_tokens"] = settings.glm_max_tokens

    try:
        return get_client().chat.completions.create(**kwargs)
    except Exception as e:  # noqa: BLE001
        logger.exception("GLM 调用失败: %s", e)
        raise GLMUnavailable() from e


# ────────────────────────────────────────────────────────────
# 4. 响应解析
# ────────────────────────────────────────────────────────────


def extract_message(resp: Any) -> dict[str, Any]:
    """从 ChatCompletion 中提取 assistant message（含 thinking / tool_calls 字段）。

    返回 dict，可直接作为下一次 messages 元素的 "assistant" 角色继续对话。
    """
    msg = resp.choices[0].message
    return msg.model_dump() if hasattr(msg, "model_dump") else dict(msg)


def iter_stream_chunks(stream: Iterable[Any]) -> Iterator[tuple[str, str]]:
    """把 stream 切成 (kind, payload)：

    - kind="content"  payload=一段文字（assistant 增量内容）
    - kind="reasoning" payload=思考链增量
    - kind="tool_calls" payload=function-calling 增量（原始 list）
    - kind="done" payload=空

    GLM streaming 把思考放在 `reasoning_content` 字段，OpenAI 风格把工具调用增量放在
    `tool_calls` 列表里。我们这里兼容两种风格。
    """
    for chunk in stream:
        # 流式最后一个 chunk 可能带 usage（token 统计），透传给上层累计
        usage = getattr(chunk, "usage", None)
        if usage is not None:
            yield "usage", usage
        try:
            choice = chunk.choices[0]
            delta = choice.delta
            content_piece = getattr(delta, "content", None) or ""
            reasoning_piece = getattr(delta, "reasoning_content", None) or ""
            tool_calls_piece = getattr(delta, "tool_calls", None)
        except (IndexError, AttributeError):
            continue
        if content_piece:
            yield "content", content_piece
        if reasoning_piece:
            yield "reasoning", reasoning_piece
        if tool_calls_piece:
            yield "tool_calls", tool_calls_piece
    yield "done", ""
