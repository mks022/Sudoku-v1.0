"""Optional stdio MCP server exposing NetGuard network tools."""

from __future__ import annotations

import asyncio
import json
import sys

from app.mcp_tools.network import TOOL_SPECS, invoke_tool


async def handle(line: str) -> dict:
    req = json.loads(line)
    method = req.get("method")
    _id = req.get("id")
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": _id, "result": {"tools": TOOL_SPECS}}
    if method == "tools/call":
        params = req.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        result = await invoke_tool(name, **args)
        return {
            "jsonrpc": "2.0",
            "id": _id,
            "result": {
                "content": [{"type": "text", "text": result.summary}],
                "isError": not result.ok,
                "structuredContent": result.data,
            },
        }
    return {"jsonrpc": "2.0", "id": _id, "error": {"code": -32601, "message": f"Unknown method {method}"}}


async def main() -> None:
    loop = asyncio.get_event_loop()
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            break
        resp = await handle(line)
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    asyncio.run(main())
