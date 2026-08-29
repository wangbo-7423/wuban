"""MCP 接入层：官方 server-memory 知识图谱记忆桥。

- `memory_bridge.bridge`：单例，per-user 管理子进程会话（read/search/create/delete）；
- `context.current_user_id`：请求级用户上下文，供 memory_search 工具读取；
- 冒烟测试：`.venv/Scripts/python.exe -m app.mcp.scripts.mcp_memory_sanity`
"""
from app.mcp.context import current_user_id
from app.mcp.memory_bridge import MemoryUnavailable, bridge

__all__ = ["bridge", "MemoryUnavailable", "current_user_id"]
