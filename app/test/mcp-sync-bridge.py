"""JSON-lines test driver for a persistent, real MCP stdio session."""
import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    server = StdioServerParameters(
        command=os.environ.get("LUMINA_TEST_MCP_EXE") or sys.executable,
        args=["mcp"] if os.environ.get("LUMINA_TEST_MCP_EXE") else
             ["-m", "lumina", "mcp"],
        env=dict(os.environ),
    )
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print(json.dumps({"ready": True}), flush=True)
            while line := await asyncio.to_thread(sys.stdin.readline):
                request = json.loads(line)
                try:
                    result = await session.call_tool(request["tool"], request.get("arguments", {}))
                    if result.is_error:
                        raise RuntimeError(str(result.content))
                    value = json.loads(next(c.text for c in result.content if c.type == "text"))
                    reply = {"id": request["id"], "result": value}
                except Exception as exc:
                    reply = {"id": request["id"], "error": str(exc)}
                print(json.dumps(reply), flush=True)


asyncio.run(main())
