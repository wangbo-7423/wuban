"""工具结果压缩（Compress 策略 ①②）：丢原始返回，留一行结论。

Agent 每轮工具调用都会往上下文窗口塞回大块 JSON（sympy 分步推导、代码
stdout、图谱节点……）。模型在下一轮把关键信息取走后，这些原始数据就
不再需要了——但它们会一直躺在窗口里跟着后面每一轮调用反复计费、稀释注意力。

对应课程的四种压缩技术里成本最低的两档（零成本 + 配置成本），这里按
LangChain v1 的中间件思想自实现（项目是纯 zai-sdk 自编排，不值得为两个
中间件引入整个框架）：
- **工具结果清除** ≈ LangChain `ClearToolUsesMiddleware`：工具结果被消费后
  原地替换为一行结论摘要；
- **观察遮蔽**：只压旧轮的 tool 消息（Observation），assistant 的
  thinking/tool_calls（Thought + Action）完整保留，当前轮不压。

关键边界：压缩只改 orchestrator 本轮内存里的 messages 列表；
`AgentResult.tool_calls` 的全量留痕（落库、前端 ToolTrace）不受影响——
窗口里瘦身，档案里全量。

摘要字段（哪个字段算「结论」）由各工具 / skill 的 spec 用 digest_fields
就地声明，本模块不做集中维护——见 docs/08-工具层设计.md。
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# 摘要字段优先级不再集中维护：每个工具 / skill 在自己的 spec 上声明
# digest_fields（领域知识就地打包，见 docs/08-工具层设计.md），本模块只负责
# 通用格式化。spec 缺失 / 名字未知的兜底只保留最通用的结论字段。
_UNKNOWN_FIELDS = ("answer", "value", "stdout")
_FIELD_CAP = 80  # 摘要里每个字段的字符上限

_DIGEST_MARK = "[已压缩]"  # 让模型知道这是摘要，需要细节可重新调用工具


def _tool_name_lookup(messages: list[dict[str, Any]]) -> dict[str, str]:
    """从 assistant 消息的 tool_calls 里建 tool_call_id → 工具名 映射。

    兼容 OpenAI dict 形态与 zai-sdk 对象形态；查不到名字的工具消息
    走 _UNKNOWN_FIELDS 兜底，压缩永远不能因为元数据缺失而失败。
    """
    lookup: dict[str, str] = {}
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        for call in msg.get("tool_calls") or []:
            if not isinstance(call, dict):
                call = call.model_dump() if hasattr(call, "model_dump") else dict(call)
            call_id = call.get("id")
            name = (call.get("function") or {}).get("name") or call.get("tool_name")
            if call_id and name:
                lookup[str(call_id)] = str(name)
    return lookup


def _digest_fields_for(name: str) -> tuple[str, ...]:
    """按工具名取它 spec 声明的摘要字段；未知工具走通用兜底。"""
    if name:
        from app.agent.tools import spec_for
        spec = spec_for(name)
        if spec is not None:
            return tuple(getattr(spec, "digest_fields", ()) or ())
    return _UNKNOWN_FIELDS


def _one_line(s: str) -> str:
    """把任意值压成单行（stdout 的换行、多余空白），供摘要拼接。"""
    import re
    return re.sub(r"\s+", " ", s).strip()


def digest_tool_content(content: str, digest_fields: tuple[str, ...] = ()) -> str:
    """把序列化的工具结果压成一行结论。解析失败则截断兜底，绝不抛异常。

    digest_fields：该工具 spec 声明的摘要字段优先级；error 永远保留
    （失败原因最重要），steps/observations 这类列表只给数量。
    """
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return f"{_DIGEST_MARK} {content[:100]}"
    if not isinstance(data, dict):
        return f"{_DIGEST_MARK} {str(data)[:100]}"

    parts: list[str] = ["ok" if data.get("ok") else "失败"]
    for field in (*digest_fields, "error"):
        v = data.get(field)
        if v in (None, "", [], False):
            continue
        if field == "matched" and isinstance(v, dict):
            v = v.get("name") or v
        if field == "entities" and isinstance(v, list):
            v = [e.get("name") if isinstance(e, dict) else e for e in v]
        # 摘要必须是一行：把 stdout 里的换行压平
        parts.append(f"{field}={_one_line(str(v))[:_FIELD_CAP]}")
    # steps/observations 这类列表只给个数量，模型想要细节会重新调
    for k in ("steps", "observations", "relations"):
        if isinstance(data.get(k), list) and data[k]:
            parts.append(f"{k}×{len(data[k])}")
    return f"{_DIGEST_MARK} " + "；".join(parts)


def compress_old_tool_results(messages: list[dict[str, Any]]) -> int:
    """就地压缩 messages 里所有 role="tool" 的消息，返回节省的字符数。

    调用时机由 orchestrator 控制：检测到新一轮 tool_calls、还没有把新结果
    追加进去的时候——此刻列表里的 tool 消息全是旧轮的，模型刚刚消费完
    它们的关键信息；当前轮的完整结果将在追加后原样参加下一次模型调用。

    每条工具消息通过 tool_call_id 反查工具名，按该工具 spec 声明的
    digest_fields 生成摘要——哪个字段是「结论」只有工具自己知道。
    """
    name_of = _tool_name_lookup(messages)
    saved = 0
    for msg in messages:
        if msg.get("role") != "tool":
            continue
        content = msg.get("content") or ""
        if content.startswith(_DIGEST_MARK):
            continue  # 已压过（理论上不会发生，防御重复调用）
        fields = _digest_fields_for(name_of.get(str(msg.get("tool_call_id") or ""), ""))
        digest = digest_tool_content(content, fields)
        if len(digest) < len(content):
            saved += len(content) - len(digest)
            msg["content"] = digest
    if saved:
        logger.info("工具结果已压缩，节省 %s 字符", saved)
    return saved
