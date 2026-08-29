"""Agent Orchestrator：组装 system + 历史 + 工具 + 用户消息，编排 GLM 调用。

使用流程：
1. `orchestrator = AgentOrchestrator()`
2. `result = orchestrator.run(messages=..., image_urls=...)`          # 同步整包
3. `gen = orchestrator.run_stream(...)`                                # 流式（SSE）
   逐个 yield 事件 dict（reasoning/delta/tool），StopIteration.value 为 AgentResult
4. 返回 `AgentResult` 包含最终文本、思考链、工具调用、是否已切到 CardMessage 渲染。

主循环伪代码：
    init_msg = [*静态system块, *history, 动态上下文(user消息), user]
    resp = GLM(init_msg + tools)
    for step in 0..MAX_TOOL_STEPS:
        if not resp.tool_calls: break
        execute each tool_call; append {role:"tool", content:result}
        resp = GLM(updated_msg + tools)
    return final resp.message
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.agent import glm_client
from app.agent.compress import compress_old_tool_results
from app.agent.system import build_system_messages
from app.agent.tools import execute_tool, tool_schemas
from app.core.errors import BizError, ErrorCode
from app.mcp import current_user_id

logger = logging.getLogger(__name__)

MAX_TOOL_STEPS = 4  # 最多 4 轮工具调用，防止死循环


@dataclass
class AgentResult:
    """Agent 一次调用的最终结果。"""

    text: str = ""
    thinking: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    raw_message: dict[str, Any] = field(default_factory=dict)
    # Compress 指标：本轮工具循环里压掉的旧轮工具结果总字符数（答辩数据点）
    compressed_chars: int = 0
    # Cache 指标：本轮全部模型调用的输入/命中缓存 token（智谱隐式前缀缓存）
    prompt_tokens: int = 0
    cached_tokens: int = 0


class AgentOrchestrator:
    """GLM + Tools 编排器（每次调用 new 一个即可，无外部状态）。"""

    def __init__(
        self,
        *,
        dynamic_context: str | None = None,
        temperature: float | None = None,
        allowed_tools: list[str] | None = None,
        user_id: str | None = None,
    ):
        # Cache 策略：system 块两段全部静态（智谱隐式前缀缓存按前缀命中，
        # 任何一条 system 变化都会作废整块）。每轮动态上下文绝不进 system，
        # 由 run() 作为尾部 user 消息注入。
        self._system_messages = build_system_messages()
        self._dynamic = (dynamic_context or "").strip()
        self._temperature = temperature
        # 意图路由（Select 策略）：None=全量装配；列表=只装配名单内的工具/skill
        self._allowed_tools = set(allowed_tools) if allowed_tools is not None else None
        # memory_search 工具经 ContextVar 取当前用户；流式生成器跨线程迭代，
        # 端点线程里 set 的上下文传不过去，所以在每个执行段内显式重设
        self._user_id = user_id

    def _bind_user_context(self) -> None:
        if self._user_id:
            current_user_id.set(self._user_id)

    # ── 消息组装（run / run_stream 共用）──────────────────
    def _build_messages(
        self,
        history: list[dict[str, Any]] | None,
        user_text: str | None,
        image_urls: list[str] | None,
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = [*self._system_messages]
        if history:
            messages.extend(history)

        # 动态上下文（课程/意图/记忆/任务状态）：放消息尾部，保护 system 前缀缓存
        if self._dynamic:
            messages.append({
                "role": "user",
                "content": (
                    f"<本轮动态上下文>\n{self._dynamic}\n</本轮动态上下文>\n"
                    "（以上是系统注入的背景信息，回答学生时自然使用即可，不要复述。）"
                ),
            })

        # 用户消息（文本或多模态）
        user_msg = glm_client.multimodal_message("user", user_text or "", image_urls or [])
        messages.append(user_msg)
        return messages

    # ── 工具执行（run / run_stream 共用）──────────────────
    def _execute_tools(
        self,
        messages: list[dict[str, Any]],
        assistant_msg: dict[str, Any],
        tool_calls: list[Any],
        result: AgentResult,
    ) -> None:
        """执行一轮 tool_calls 并把结果追加回 messages。"""
        # 有 tool_calls：把 assistant 消息 push 回去，执行工具，再让模型继续
        # 注意：assistant 消息里要保留 tool_calls 字段，否则 GLM 会上下文不连续
        messages.append(assistant_msg)
        # 压缩旧轮工具结果（Compress ①②）：此刻列表里的 tool 消息全是上一轮的，
        # 关键信息模型已消费，原始大 JSON 换成一行结论；当前轮结果追加后完整保留。
        # 只改本轮内存 messages；result.tool_calls 的全量留痕不受影响。
        result.compressed_chars += compress_old_tool_results(messages)
        for call in tool_calls:
            name, args, call_id = _normalize_tool_call(call)
            _t0 = time.perf_counter()
            exec_result = execute_tool(name, args)
            result.tool_calls.append(
                {
                    "tool_name": name,
                    "args": args,
                    "result": exec_result,
                    "ok": bool(exec_result.get("ok")),
                    "took_ms": int((time.perf_counter() - _t0) * 1000),
                }
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id or str(uuid.uuid4()),
                    "content": _serialize_tool_result(exec_result),
                }
            )

    # ── 顶层入口 ─────────────────────────────────────────
    def run(
        self,
        history: list[dict[str, Any]] | None = None,
        *,
        user_text: str | None = None,
        image_urls: list[str] | None = None,
    ) -> AgentResult:
        """发起一次 Agent 调用。

        Args:
            history: 历史的 user/assistant messages（不含 system）。
            user_text: 当前轮用户文本（与 image_urls 一并作为最新一条消息）。
            image_urls: 当前轮的多模态图片 URL 列表。
        """
        messages = self._build_messages(history, user_text, image_urls)

        # 工具 schema（意图路由过滤 + 如果一个也没启用就跳过 tool 参数）
        tools = tool_schemas(self._allowed_tools)

        result = AgentResult()
        steps = 0
        while True:
            steps += 1
            if steps > MAX_TOOL_STEPS:
                logger.warning("Agent 工具调用超过 %d 轮，强制收敛", MAX_TOOL_STEPS)
                break

            self._bind_user_context()
            kwargs: dict[str, Any] = {"messages": messages}
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"
            if self._temperature is not None:
                kwargs["temperature"] = self._temperature

            try:
                resp = glm_client.chat(**kwargs)
            except BizError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.exception("Agent 调用模型失败: %s", e)
                raise BizError(ErrorCode.COGNITIVE_ENGINE, "AI 模型调用失败") from e

            msg = resp.choices[0].message
            self._accumulate_usage(result, getattr(resp, "usage", None))
            msg_dict: dict[str, Any] = (
                msg.model_dump() if hasattr(msg, "model_dump") else dict(msg)
            )
            # 累积思考链（如果模型启用 thinking）
            thinking_piece = msg_dict.get("reasoning_content") or msg_dict.get("thinking") or ""
            if isinstance(thinking_piece, str):
                result.thinking += thinking_piece

            tool_calls = msg_dict.get("tool_calls") or []
            if not tool_calls:
                # 终止：把最终 assistant 消息写入
                result.text = msg_dict.get("content") or ""
                result.raw_message = msg_dict
                break

            self._execute_tools(messages, msg_dict, tool_calls, result)

        if not result.text and not result.tool_calls:
            # 全部陷入工具循环，没拿到文本，降级返回错误
            raise BizError(ErrorCode.COGNITIVE_ENGINE, "AI 没有返回可用内容，请稍后重试")

        if result.prompt_tokens:
            logger.info(
                "Agent 调用完成：prompt=%s cached=%s（命中率 %.0f%%）压缩=%s 字符",
                result.prompt_tokens, result.cached_tokens,
                result.cached_tokens / result.prompt_tokens * 100, result.compressed_chars,
            )
        return result

    # ── 流式入口（SSE）────────────────────────────────────
    def run_stream(
        self,
        history: list[dict[str, Any]] | None = None,
        *,
        user_text: str | None = None,
        image_urls: list[str] | None = None,
    ):
        """流式版 run()：生成器逐段产出，StopIteration.value 为 AgentResult。

        yield 的事件 dict：
        - {"type": "reasoning", "text": 增量}   思考链增量（thinking 模式）
        - {"type": "delta", "text": 增量}       最终回答的正文增量
        - {"type": "tool", "tool_name": ..., "args": ...}  开始执行某工具

        工具循环内的中间步骤同样走流式：先边收边吐 delta，一旦该步出现
        tool_calls 就转入工具执行继续循环——中间步骤的少量正文会被最终
        回答覆盖（done 事件后前端用完整卡片替换占位内容）。
        """
        messages = self._build_messages(history, user_text, image_urls)
        tools = tool_schemas(self._allowed_tools)

        result = AgentResult()
        steps = 0
        while True:
            steps += 1
            if steps > MAX_TOOL_STEPS:
                logger.warning("Agent 工具调用超过 %d 轮，强制收敛", MAX_TOOL_STEPS)
                break

            self._bind_user_context()
            kwargs: dict[str, Any] = {"messages": messages, "stream": True}
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"
            if self._temperature is not None:
                kwargs["temperature"] = self._temperature

            try:
                stream = glm_client.chat(**kwargs)
            except BizError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.exception("Agent 调用模型失败: %s", e)
                raise BizError(ErrorCode.COGNITIVE_ENGINE, "AI 模型调用失败") from e

            content_parts: list[str] = []
            reasoning_parts: list[str] = []
            tc_acc: dict[int, dict[str, Any]] = {}
            for kind, piece in glm_client.iter_stream_chunks(stream):
                if kind == "done":
                    break
                if kind == "usage":
                    self._accumulate_usage(result, piece)
                elif kind == "reasoning":
                    reasoning_parts.append(piece)
                    result.thinking += piece
                    yield {"type": "reasoning", "text": piece}
                elif kind == "content":
                    content_parts.append(piece)
                    yield {"type": "delta", "text": piece}
                elif kind == "tool_calls":
                    _accumulate_tool_deltas(tc_acc, piece)

            msg_dict: dict[str, Any] = {
                "role": "assistant",  # 手工组装必须带 role，GLM 校验「角色信息不能为空」
                "content": "".join(content_parts),
                "reasoning_content": "".join(reasoning_parts),
            }
            if tc_acc:
                msg_dict["tool_calls"] = [tc_acc[i] for i in sorted(tc_acc)]

            tool_calls = msg_dict.get("tool_calls") or []
            if not tool_calls:
                result.text = msg_dict["content"]
                result.raw_message = msg_dict
                break

            # 先上报「正在调用」再执行，前端能实时看到工具轨迹
            for call in tool_calls:
                name, args, _ = _normalize_tool_call(call)
                yield {"type": "tool", "tool_name": name, "args": args}
            self._execute_tools(messages, msg_dict, tool_calls, result)

        if not result.text and not result.tool_calls:
            raise BizError(ErrorCode.COGNITIVE_ENGINE, "AI 没有返回可用内容，请稍后重试")

        if result.prompt_tokens:
            logger.info(
                "Agent 流式调用完成：prompt=%s cached=%s 压缩=%s 字符",
                result.prompt_tokens, result.cached_tokens, result.compressed_chars,
            )
        return result

    @staticmethod
    def _accumulate_usage(result: AgentResult, usage: Any) -> None:
        if usage is None:
            return
        result.prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
        det = getattr(usage, "prompt_tokens_details", None)
        result.cached_tokens += getattr(det, "cached_tokens", 0) or 0


# ────────────────────────────────────────────────────────────
# 辅助
# ────────────────────────────────────────────────────────────


def _accumulate_tool_deltas(
    acc: dict[int, dict[str, Any]], pieces: Any
) -> None:
    """把流式 tool_calls 增量（OpenAI 风格分片）累积成完整 tool_call 列表。

    GLM 流式把一个 tool_call 拆成多段（首段带 id/name，后续段只带 arguments
    增量，用 index 标识归属）。acc 按 index 归并，产出 OpenAI 非流式同构的
    {"id", "type", "function": {"name", "arguments"}}，供 _normalize_tool_call 复用。
    """
    for piece in pieces or []:
        if not isinstance(piece, dict):
            # zai-sdk 对象形态
            piece = piece.model_dump() if hasattr(piece, "model_dump") else dict(piece)
        idx = piece.get("index")
        idx = idx if isinstance(idx, int) else len(acc)
        slot = acc.setdefault(
            idx, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
        )
        if piece.get("id"):
            slot["id"] = piece["id"]
        fn = piece.get("function") or {}
        if fn.get("name"):
            slot["function"]["name"] = fn["name"]
        if fn.get("arguments"):
            slot["function"]["arguments"] += fn["arguments"]


def _normalize_tool_call(call: Any) -> tuple[str, dict[str, Any], str | None]:
    """兼容 OpenAI 风格 + zai-sdk 流式形态：从 tool_call 里拆出 (name, args, id)。"""
    if isinstance(call, dict):
        fn = call.get("function") or {}
        name = fn.get("name") or call.get("tool_name") or ""
        raw_args = fn.get("arguments") or call.get("args") or "{}"
        call_id = call.get("id")
    else:
        # zai-sdk 对象形态（ChatCompletionMessageToolCall）
        fn = getattr(call, "function", None)
        name = getattr(fn, "name", "") if fn else getattr(call, "tool_name", "")
        raw_args = getattr(fn, "arguments", "{}") if fn else getattr(call, "args", "{}")
        call_id = getattr(call, "id", None)

    if isinstance(raw_args, str):
        import json
        try:
            args = json.loads(raw_args or "{}")
        except json.JSONDecodeError:
            args = {"_raw": raw_args}
    else:
        args = raw_args if isinstance(raw_args, dict) else {}
    return name, args, call_id


def _serialize_tool_result(result: dict[str, Any]) -> str:
    """把工具结果序列化成一个简洁 string 给 GLM 读（GLM 对 long JSON 不友好）。"""
    import json
    try:
        s = json.dumps(result, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        s = repr(result)
    return s[:4000]  # GLM 工具结果太长会被截断
