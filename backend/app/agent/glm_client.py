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

import json
import logging
import random
import re
import time
from typing import Any, Iterable, Iterator, TypeVar

from pydantic import BaseModel, ValidationError
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
    reasoning_effort: str | None = None,
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
        reasoning_effort: 推理深度 low/high/max（GLM-5.2+ 生效）。glm-5.3 系列强制思考
            关不掉，这个档位是唯一能压缩思考量的开关；不传时平台默认 max（深度推理）。
            传 None 走 settings.glm_reasoning_effort；传 "" 显式不下发（兼容旧模型）。

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
    if reasoning_effort is None:
        reasoning_effort = settings.glm_reasoning_effort
    if reasoning_effort:  # "" 表示显式不下发（模型不支持该参数时用）
        kwargs["reasoning_effort"] = reasoning_effort
    if temperature is not None:
        kwargs["temperature"] = temperature
    else:
        kwargs["temperature"] = settings.glm_temperature
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    else:
        kwargs["max_tokens"] = settings.glm_max_tokens

    # ── 限流/超时重试：指数退避 ──────────────────────────
    # 背景：GLM 429 时请求会在服务端排队 60~90s，SDK 超时后抛 APITimeoutError。
    # 这类错误是暂时性的，值得重试；其他错误（鉴权、参数）重试无意义。
    max_attempts = 1 + max(0, settings.glm_rate_limit_retries)
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return get_client().chat.completions.create(**kwargs)
        except Exception as e:  # noqa: BLE001
            last_exc = e
            retryable = _is_retryable(e)
            if not retryable or attempt >= max_attempts - 1:
                break
            delay = settings.glm_rate_limit_backoff_sec * (2**attempt) + random.uniform(0, 1)
            logger.warning(
                "GLM 调用暂时失败（第 %d/%d 次，%s），%.1fs 后重试: %s",
                attempt + 1,
                max_attempts,
                type(e).__name__,
                delay,
                e,
            )
            time.sleep(delay)
    logger.exception("GLM 调用失败: %s", last_exc)
    raise GLMUnavailable() from last_exc


def _is_retryable(e: Exception) -> bool:
    """判断异常是否值得重试：429 限流 / 超时 / 连接中断。"""
    # SDK 异常可能带 status_code / code 属性
    status = getattr(e, "status_code", None) or getattr(e, "code", None)
    if status in (429, "429"):
        return True
    name = type(e).__name__.lower()
    text = str(e).lower()
    if "timeout" in name or "timed out" in text or "timeout" in text:
        return True
    if "connection" in name or "connection" in text or "eof" in text:
        return True
    if "429" in text or "rate limit" in text or "请求过多" in str(e) or "排队" in str(e):
        return True
    return False


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


# ────────────────────────────────────────────────────────────
# 5. 结构化输出：「提示词|模型|输出解析器」模式的原生实现
#    （LangChain OutputFixingParser 的最小自产集，不引框架）
# ────────────────────────────────────────────────────────────

_TModel = TypeVar("_TModel", bound=BaseModel)


def extract_json_object(text: str) -> Any:
    """从模型输出里抽出 JSON 对象：兼容 ```json 围栏与裸 JSON。解析失败抛 JSONDecodeError。"""
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t).strip()
    return json.loads(t)


def chat_structured(
    messages: list[dict[str, Any]],
    schema: type[_TModel],
    *,
    fix_attempts: int = 1,
    **chat_kwargs: Any,
) -> _TModel:
    """JSON mode 调用 + pydantic 校验，失败带错误反馈重试。

    调用方只写「提示词 + schema」，拿到的是校验过的模型实例——
    解析/校验失败时把失败原因追加为一条 user 消息再试 `fix_attempts`
    轮（模型最擅长修自己格式上的小错：漏字段、多围栏、枚举拼错）。

    约束：
    - messages 的 system 块必须保持静态（智谱前缀缓存按前缀命中），
      重试只追加在尾部，不动 system——与 orchestrator 的缓存策略一致；
    - API 层失败（429/超时/鉴权）不进 fix 循环，chat() 自己的重试与
      GLMUnavailable 异常语义原样向上抛；
    - helper 不替业务决定降级：fix 耗尽后抛最后一个校验异常，由调用方
      兜底（意图分类 → "all"，切卡 → 兜底单卡）。
    """
    msgs = list(messages)
    last_exc: Exception | None = None
    for attempt in range(max(0, fix_attempts) + 1):
        resp = chat(messages=msgs, response_format={"type": "json_object"}, **chat_kwargs)
        content = (getattr(resp.choices[0].message, "content", None) or "")
        try:
            return schema.model_validate(extract_json_object(content))
        except (json.JSONDecodeError, ValidationError) as e:
            last_exc = e
            logger.warning(
                "结构化输出校验失败（第 %d/%d 次，%s）: %s",
                attempt + 1, max(0, fix_attempts) + 1, type(e).__name__, str(e)[:300],
            )
            msgs = msgs + [
                {"role": "assistant", "content": content},
                {
                    "role": "user",
                    "content": (
                        f"上面的输出不符合要求的 JSON 结构（{type(e).__name__}: {str(e)[:400]}）。"
                        "请重新输出：只给一个符合要求的 JSON 对象，不要解释、不要 markdown 围栏。"
                    ),
                },
            ]
    assert last_exc is not None
    raise last_exc
