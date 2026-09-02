"""回归测试：流式链路 ContextVar 在工具执行前必须重新绑定。

背景（2026-09-01 排查）：
- 截图现象：`memory_search` / `scaffold_state` 在 /chat/stream 路径下
  持续拿到 `{"ok": false, "error": "当前上下文没有用户身份，无法查记忆"}`；
- 同步 /chat 路径正常，因为 endpoint 顶端的 `current_user_id.set(user.id)`
  不会被任何 `next()` 跨段动作打断；
- 流式 /chat/stream 经 Starlette `iterate_in_threadpool` 跨 `next()` 时，
  `anyio.to_thread.run_sync()` 每次 `copy_context()`，原本在 step 顶部
  设的 ContextVar 在 tool 真正执行时已丢失。

修复：`AgentOrchestrator._execute_tools` 在每个工具执行前调用
`_bind_user_context()` 重 set。同步链路也走同一段路径，无副作用。

本测试用 Pydantic + 简单 stub 模拟「stream 跨段、ContextVar 已重置」的状态，
只验证「_execute_tools 会触发重 set」。
"""
from __future__ import annotations

from app.agent import orchestrator as orch_mod
from app.agent.orchestrator import AgentOrchestrator
from app.mcp import current_user_id


def test_execute_tools_rebinds_context_before_each_tool_call():
    """_execute_tools 进入 for 循环前 current_user_id 丢了也无所谓 —— 内部每次都会重 bind。"""
    uid = "test-uid-regression"
    # 模拟「跨段后 ContextVar 没值」的状态
    saved = current_user_id.set(None)
    try:
        # 重建一个 orchestrator；它的 _user_id 还在
        orch = AgentOrchestrator(
            dynamic_context="",
            allowed_tools=None,
            user_id=uid,
        )
        # 直接复用 _execute_tools 的实际实现：先用 spy 替换 execute_tool，记录每次调用前的 uid。
        seen: list[str | None] = []

        def spy_execute_tool(name, args):
            seen.append(current_user_id.get())
            return {"ok": True, "tool_name": name}

        # 临时 monkey-patch execute_tool 为受控 spy
        orig = orch_mod.execute_tool
        orch_mod.execute_tool = spy_execute_tool
        try:
            # 真实跑一次 _execute_tools；用空 tool_calls 不会真出 bug
            orch._execute_tools(
                messages=[],
                assistant_msg={"role": "assistant", "content": "", "tool_calls": []},
                tool_calls=[
                    {"function": {"name": "memory_search", "arguments": '{"query":"x"}'}, "id": "1"},
                    {"function": {"name": "scaffold_state", "arguments": '{"topic":"x"}'}, "id": "2"},
                ],
                result=orch_mod.AgentResult(),
            )
        finally:
            orch_mod.execute_tool = orig

        # 每次 execute_tool 调用前都应该已经把 uid 拿回来了
        assert seen == [uid, uid], f"execute_tool 看到的 ContextVar 不一致: {seen}"
    finally:
        current_user_id.reset(saved)


def test_bind_user_context_no_op_when_user_id_unset():
    """orchestrator.user_id 没传 → _bind_user_context 是 no-op（仍 OK，sync/stream 都别抛）。"""
    saved = current_user_id.set("context-leaked")
    try:
        orch = AgentOrchestrator(dynamic_context="", allowed_tools=None, user_id=None)
        orch._bind_user_context()
        # 没主动清，仍是原来 set 的；保证不会自动覆盖成 None（防止误清端点 set 的值）
        assert current_user_id.get() == "context-leaked"
    finally:
        current_user_id.reset(saved)
