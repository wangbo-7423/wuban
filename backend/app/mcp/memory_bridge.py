"""MCP 记忆桥：把官方 `@modelcontextprotocol/server-memory` 接入后端。

架构（每个用户一条独立链路）：

    FastAPI 线程池线程（sync 端点）
        └─ MemoryBridge.call(...)  ──run_coroutine_threadsafe──▶  专属后台线程
                                                                    └─ asyncio 事件循环
                                                                        └─ stdio JSON-RPC
                                                                            └─ node dist/index.js
                                                                                └─ data/memory/user_{id}.jsonl

- server 子进程通过 `MEMORY_FILE_PATH` 环境变量拿到各自的数据文件（这一版
  不支持 --memory-path 命令行参数，入参名以实测为准：entityNames / observations 等）；
- 会话懒加载；空闲超过 `mcp_memory_idle_sec` 回收；崩溃后下次调用自动重启；
- **同步降级**：node / server 脚本缺失、spawn 失败、调用超时 → 抛
  `MemoryUnavailable`，上层捕获后跳过记忆功能，绝不影响主对话链路。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import threading
import time
from concurrent.futures import TimeoutError as FutTimeout
from pathlib import Path
from typing import Any

from app.core.config import BACKEND_DIR, settings

logger = logging.getLogger(__name__)

# 本地安装位置（backend/mcp_servers；package.json 锁版本，node_modules 不入库）
_SERVER_JS = (
    BACKEND_DIR / "mcp_servers"
    / "node_modules" / "@modelcontextprotocol" / "server-memory" / "dist" / "index.js"
)

_STARTUP_TIMEOUT = 20.0   # 子进程 initialize 超时
_DISABLE_COOLDOWN = 60.0  # spawn 连续失败后的全局熔断窗口


class MemoryUnavailable(Exception):
    """记忆服务不可用（缺依赖 / 子进程失败 / 超时）。上层应静默降级。"""


def _node_exec() -> str | None:
    """解析 node 可执行文件路径（Windows 上 node.exe 不是 .cmd，可直接 exec）。"""
    return shutil.which("node")


class _Session:
    """一个用户的 memory server 会话：后台线程 + 事件循环 + 常驻子进程。"""

    def __init__(self, user_id: str, mem_file: Path):
        self.user_id = user_id
        self.mem_file = mem_file
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None
        self._session: Any = None          # mcp ClientSession
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._failed = threading.Event()
        self.last_used = time.time()

    # ── 生命周期 ─────────────────────────────────────────
    def start(self) -> None:
        node = _node_exec()
        if node is None:
            raise MemoryUnavailable("未找到 node 可执行文件")
        if not _SERVER_JS.is_file():
            raise MemoryUnavailable(f"memory server 脚本不存在: {_SERVER_JS}")

        self.mem_file.parent.mkdir(parents=True, exist_ok=True)
        self.thread = threading.Thread(
            target=self._run, name=f"mcp-memory-{self.user_id[:8]}", daemon=True
        )
        self.thread.start()
        if not self._ready.wait(_STARTUP_TIMEOUT):
            self.close()
            raise MemoryUnavailable(f"memory server 启动超时（user={self.user_id}）")
        if self._failed.is_set():
            self.close()
            raise MemoryUnavailable(f"memory server 启动失败（user={self.user_id}）")

    def _run(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._worker())
        except Exception as e:  # noqa: BLE001
            logger.warning("memory server 会话异常退出 user=%s: %s", self.user_id, e)
            self._failed.set()
        finally:
            self._ready.set()  # 兜底唤醒等待方
            try:
                self.loop.close()
            except Exception:  # noqa: BLE001
                pass

    async def _worker(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=_node_exec() or "node",
            args=[str(_SERVER_JS)],
            env={**os.environ, "MEMORY_FILE_PATH": str(self.mem_file)},
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), _STARTUP_TIMEOUT)
                self._session = session
                self._ready.set()
                while not self._stop.is_set():
                    await asyncio.sleep(0.25)

    def close(self) -> None:
        self._stop.set()
        if self.thread is not None:
            self.thread.join(timeout=5.0)
            self.thread = None
        self._session = None

    @property
    def alive(self) -> bool:
        return (
            self._session is not None
            and self.thread is not None
            and self.thread.is_alive()
        )

    # ── 调用 ─────────────────────────────────────────────
    def call(self, tool: str, args: dict[str, Any], *, parse_json: bool = False) -> Any:
        if not self._ready.wait(_STARTUP_TIMEOUT) or self._session is None:
            raise MemoryUnavailable(f"会话未就绪 user={self.user_id}")
        self.last_used = time.time()
        fut = asyncio.run_coroutine_threadsafe(self._call(tool, args, parse_json), self.loop)
        try:
            return fut.result(timeout=settings.mcp_memory_call_timeout)
        except FutTimeout as e:
            raise MemoryUnavailable(f"MCP 调用超时: {tool}") from e
        except MemoryUnavailable:
            raise
        except Exception as e:  # noqa: BLE001
            raise MemoryUnavailable(f"MCP 调用失败 {tool}: {e}") from e

    async def _call(self, tool: str, args: dict[str, Any], parse_json: bool = False) -> Any:
        r = await self._session.call_tool(tool, args)
        if r.is_error:
            text = r.content[0].text if r.content else "unknown"
            raise MemoryUnavailable(f"MCP 工具报错 {tool}: {text[:200]}")
        if not r.content:
            return None
        text = r.content[0].text
        if parse_json:
            return json.loads(text)
        return text


class MemoryBridge:
    """单例管理器：per-user 会话缓存 + 空闲回收 + 失败熔断。"""

    def __init__(self) -> None:
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.Lock()
        self._disabled_until = 0.0

    # ── 可用性 ───────────────────────────────────────────
    def available(self) -> bool:
        if not settings.enable_mcp_memory:
            return False
        if time.time() < self._disabled_until:
            return False
        return _node_exec() is not None and _SERVER_JS.is_file()

    def _fuse(self, reason: str) -> None:
        """连续 spawn 失败 → 熔断一段时间，避免每轮聊天都重试。"""
        self._disabled_until = time.time() + _DISABLE_COOLDOWN
        logger.warning("MCP 记忆服务熔断 %ss：%s", _DISABLE_COOLDOWN, reason)

    # ── 会话管理 ─────────────────────────────────────────
    def _get(self, user_id: str) -> _Session:
        with self._lock:
            self._reap_locked()
            s = self._sessions.get(user_id)
            if s is not None and s.alive:
                return s
            if s is not None:
                s.close()
                del self._sessions[user_id]
            # 超过上限：优先回收最闲置的会话
            while len(self._sessions) >= settings.mcp_memory_max_sessions:
                oldest = min(self._sessions.values(), key=lambda x: x.last_used)
                oldest.close()
                del self._sessions[oldest.user_id]

            s = _Session(user_id, self._mem_file(user_id))
            try:
                s.start()
            except MemoryUnavailable as e:
                self._fuse(str(e))
                raise
            self._sessions[user_id] = s
            return s

    def _reap_locked(self) -> None:
        now = time.time()
        stale = [
            uid for uid, s in self._sessions.items()
            if now - s.last_used > settings.mcp_memory_idle_sec
        ]
        for uid in stale:
            self._sessions.pop(uid).close()

    def _mem_file(self, user_id: str) -> Path:
        # user_id 是服务端签发的 UUID，无路径穿越风险；仍取 basename 防御一手
        safe = os.path.basename(user_id)
        return Path(settings.mcp_memory_dir) / f"user_{safe}.jsonl"

    # ── 对外操作（同步；失败抛 MemoryUnavailable）─────────
    def read_graph(self, user_id: str) -> dict[str, Any]:
        return self._call_user(user_id, "read_graph", {})

    def search_nodes(self, user_id: str, query: str) -> dict[str, Any]:
        return self._call_user(user_id, "search_nodes", {"query": query})

    def create_entities(self, user_id: str, entities: list[dict[str, Any]]) -> None:
        if entities:
            self._call_user(user_id, "create_entities", {"entities": entities})

    def add_observations(self, user_id: str, observations: list[dict[str, Any]]) -> None:
        if observations:
            self._call_user(user_id, "add_observations", {"observations": observations})

    def create_relations(self, user_id: str, relations: list[dict[str, Any]]) -> None:
        if relations:
            self._call_user(user_id, "create_relations", {"relations": relations})

    def delete_all(self, user_id: str) -> None:
        """清空某用户记忆：关会话（Windows 下文件被占用删不掉）→ 删文件。"""
        with self._lock:
            s = self._sessions.pop(user_id, None)
            if s is not None:
                s.close()
        f = self._mem_file(user_id)
        f.unlink(missing_ok=True)

    def shutdown_all(self) -> None:
        with self._lock:
            for s in self._sessions.values():
                s.close()
            self._sessions.clear()

    def _call_user(self, user_id: str, tool: str, args: dict[str, Any]) -> Any:
        s = self._get(user_id)
        try:
            return s.call(tool, args, parse_json=True)
        except MemoryUnavailable:
            # 子进程可能中途挂了：回收会话，重启一次再试
            with self._lock:
                dead = self._sessions.pop(user_id, None)
            if dead is not None:
                dead.close()
            s2 = self._get(user_id)
            return s2.call(tool, args, parse_json=True)


# 模块级单例（进程内共享；线程安全由内部 lock 保证）
bridge = MemoryBridge()
