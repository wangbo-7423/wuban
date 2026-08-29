"""MCP 记忆桥冒烟测试：拉起本地 server-memory，跑完 create/read/search/delete 全链路。

用法（backend 目录下）：
    .venv/Scripts/python.exe -m app.mcp.scripts.mcp_memory_sanity
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

BACKEND_DIR = Path(__file__).resolve().parents[3]
SERVER_JS = (
    BACKEND_DIR / "mcp_servers" / "node_modules"
    / "@modelcontextprotocol" / "server-memory" / "dist" / "index.js"
)
MEM_FILE = BACKEND_DIR / "data" / "memory" / "_sanity.jsonl"


async def main() -> int:
    if not SERVER_JS.is_file():
        print(f"[FAIL] 找不到 memory server: {SERVER_JS}")
        return 1

    MEM_FILE.parent.mkdir(parents=True, exist_ok=True)
    if MEM_FILE.exists():
        MEM_FILE.unlink()

    import shutil
    node = shutil.which("node")
    if not node:
        print("[FAIL] 找不到 node 可执行文件")
        return 1

    params = StdioServerParameters(
        command=node,
        args=[str(SERVER_JS)],
        env={**os.environ, "MEMORY_FILE_PATH": str(MEM_FILE)},
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=15)

            tools = await asyncio.wait_for(session.list_tools(), timeout=10)
            print("[1] tools:", [t.name for t in tools.tools])

            r = await asyncio.wait_for(
                session.call_tool(
                    "create_entities",
                    {
                        "entities": [
                            {
                                "name": "傅里叶变换",
                                "entityType": "概念",
                                "observations": ["2026-08-29 学生初次提问"],
                            },
                            {
                                "name": "傅里叶级数",
                                "entityType": "概念",
                                "observations": ["学生已学过"],
                            },
                        ]
                    },
                ),
                timeout=10,
            )
            print("[2] create_entities ok:", not r.is_error)

            r = await asyncio.wait_for(
                session.call_tool(
                    "create_relations",
                    {
                        "relations": [
                            {
                                "from": "傅里叶变换",
                                "to": "傅里叶级数",
                                "relationType": "前置依赖",
                            }
                        ]
                    },
                ),
                timeout=10,
            )
            print("[3] create_relations ok:", not r.is_error)

            r = await asyncio.wait_for(
                session.call_tool("read_graph", {}), timeout=10
            )
            import json
            graph = json.loads(r.content[0].text)
            print("[4] read_graph:", len(graph["entities"]), "entities,",
                  len(graph["relations"]), "relations")

            r = await asyncio.wait_for(
                session.call_tool("search_nodes", {"query": "傅里叶"}), timeout=10
            )
            found = json.loads(r.content[0].text)
            print("[5] search '傅里叶':", [e["name"] for e in found["entities"]])

            r = await asyncio.wait_for(
                session.call_tool("delete_entities", {"names": ["傅里叶变换"]}),
                timeout=10,
            )
            r = await asyncio.wait_for(
                session.call_tool("read_graph", {}), timeout=10
            )
            graph = json.loads(r.content[0].text)
            print("[6] after delete:", [e["name"] for e in graph["entities"]])

    size = MEM_FILE.stat().st_size if MEM_FILE.exists() else 0
    print(f"[7] memory file size: {size} bytes")
    print("SANITY OK" if size > 0 else "SANITY FAIL（文件未落盘）")
    return 0 if size > 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
