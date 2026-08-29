"""AI Agent 模块：把大模型、提示词、工具、编排器聚合在一起。

公开接口：
- `AgentOrchestrator` / `AgentResult`：运行一次对话；
- `tool_schemas()` / `execute_tool()`：直接给上层路由调用；
- `load_system_prompt()` / `load_intent_guide()`：提示词加载（真源 Agent.md / prompts/）。

历史流水线里的 `recognize_intent / diagnose / strategies` 完全被替代 —— 模型自己决定
「先问 / 解释 / 列步骤 / 上网搜」。
"""
from __future__ import annotations

from app.agent.glm_client import (
    GLMUnavailable,
    chat,
    extract_message,
    get_client,
    iter_stream_chunks,
    multimodal_message,
    text_message,
)
from app.agent.orchestrator import AgentOrchestrator, AgentResult, MAX_TOOL_STEPS
from app.agent.system import build_system_prompt, load_intent_guide, load_system_prompt
from app.agent.tools import (
    ToolSpec,
    execute_tool,
    get_tool,
    list_tools,
    tool_schemas,
)

__all__ = [
    # 客户端
    "GLMUnavailable",
    "chat",
    "extract_message",
    "get_client",
    "iter_stream_chunks",
    "multimodal_message",
    "text_message",
    # 提示词（真源在仓库根目录 Agent.md 与 prompts/guide_*.md）
    "load_system_prompt",
    "load_intent_guide",
    "build_system_prompt",
    # 工具
    "ToolSpec",
    "execute_tool",
    "get_tool",
    "list_tools",
    "tool_schemas",
    # 编排
    "AgentOrchestrator",
    "AgentResult",
    "MAX_TOOL_STEPS",
]
