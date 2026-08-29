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
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# 摘要字段优先级：不同工具的关键结论字段名不一样，按序取第一个非空的
# （值为 0/False/空的字段自动跳过——returncode=0、timed_out=False 是「没有新闻」，
#   异常值如 returncode=1、timed_out=True 才值得占摘要的篇幅）
_DIGEST_FIELDS = (
    "answer",        # calculus/ode：最终答案
    "final_expr",    # ode：末次整理的表达式
    "value",         # calculator：数值结果
    "matched",       # kg_lookup：命中的 KG 节点
    "stdout",        # code_runner：执行输出
    "entities",      # memory_search：命中的记忆实体
    "options",       # project_guide：项目选项
    "timed_out",     # code_runner：执行超时
    "returncode",    # code_runner：非零退出码
    "error",         # 失败时的原因（最重要，永远保留）
)
_FIELD_CAP = 80  # 摘要里每个字段的字符上限

_DIGEST_MARK = "[已压缩]"  # 让模型知道这是摘要，需要细节可重新调用工具


def _one_line(s: str) -> str:
    """把任意值压成单行（stdout 的换行、多余空白），供摘要拼接。"""
    import re
    return re.sub(r"\s+", " ", s).strip()


def digest_tool_content(content: str) -> str:
    """把序列化的工具结果压成一行结论。解析失败则截断兜底，绝不抛异常。"""
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return f"{_DIGEST_MARK} {content[:100]}"
    if not isinstance(data, dict):
        return f"{_DIGEST_MARK} {str(data)[:100]}"

    parts: list[str] = ["ok" if data.get("ok") else "失败"]
    for field in _DIGEST_FIELDS:
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
    """
    saved = 0
    for msg in messages:
        if msg.get("role") != "tool":
            continue
        content = msg.get("content") or ""
        if content.startswith(_DIGEST_MARK):
            continue  # 已压过（理论上不会发生，防御重复调用）
        digest = digest_tool_content(content)
        if len(digest) < len(content):
            saved += len(content) - len(digest)
            msg["content"] = digest
    if saved:
        logger.info("工具结果已压缩，节省 %s 字符", saved)
    return saved
