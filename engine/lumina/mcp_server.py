"""MCP server: every registered command is a tool, plus read/undo/transaction tools.

Tools are generated from the command registry, so anything the GUI can do is
callable by an agent with the same validation and the same undo history.
"""

from __future__ import annotations

import json
from typing import Any

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from .client import connect
from .commands.registry import REGISTRY, CommandError

INSTRUCTIONS = """Lumina is a Premiere-style video editor. Every edit is an undoable command.
Times accept ticks (int) or strings: "12.5s", "45f", "00:01:02:15". Omitted sequence_id = active sequence.
Typical flow: open_project/new_project -> media_import -> sequence_create_from_media -> edits -> get_timeline_state -> save_project.
Wrap multi-step edits in begin_transaction/commit_transaction so they undo as one step (rollback_transaction on failure)."""

_OBJ = {"type": "object", "properties": {}, "additionalProperties": False}

EXTRA_TOOLS: dict[str, tuple[str, str, dict, Any]] = {
    # tool name: (rpc method, description, schema, param mapper)
    "get_project": ("project.summary", "Project overview: bins, media (ids, durations, streams) and sequences.", _OBJ, None),
    "get_timeline_state": ("timeline.state", "Tracks, clips (ticks/seconds/timecode), markers and playhead of a sequence.",
                           {"type": "object", "properties": {"sequence_id": {"type": "string"}}}, None),
    "undo": ("undo", "Undo the last edit (Ctrl+Z).", _OBJ, None),
    "redo": ("redo", "Redo (Ctrl+Shift+Z).", _OBJ, None),
    "begin_transaction": ("transaction.begin", "Group following edits into one undo step.",
                          {"type": "object", "properties": {"label": {"type": "string"}}}, None),
    "commit_transaction": ("transaction.commit", "Close the open transaction.", _OBJ, None),
    "rollback_transaction": ("transaction.rollback", "Revert every edit made since begin_transaction.", _OBJ, None),
    "new_project": ("project.new", "Start a new empty project.", {"type": "object", "properties": {"name": {"type": "string"}}}, None),
    "open_project": ("project.open", "Open a .lumina project.",
                     {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}, None),
    "save_project": ("project.save", "Save the project (path required the first time).",
                     {"type": "object", "properties": {"path": {"type": "string"}}}, None),
    "list_jobs": ("jobs.list", "Background proxy/thumbnail/waveform jobs and their status.", _OBJ, None),
    "convert_time": ("time.convert", "Convert a time value to ticks/seconds/frame/timecode in a sequence's frame rate.",
                     {"type": "object", "properties": {"time": {"type": ["string", "integer"]}, "sequence_id": {"type": "string"}},
                      "required": ["time"]}, None),
}


def tool_name(command: str) -> str:
    return command.replace(".", "_")


def build_tools() -> list[types.Tool]:
    tools = []
    for spec in REGISTRY.commands.values():
        if spec.name == "media.set_derived":
            continue  # internal
        desc = spec.description + ("" if spec.undoable else " (not undoable)")
        tools.append(types.Tool(name=tool_name(spec.name), description=desc, input_schema=spec.json_schema()))
    for name, (_, desc, schema, _) in EXTRA_TOOLS.items():
        tools.append(types.Tool(name=name, description=desc, input_schema=schema))
    return tools


def make_server(engine) -> Server:
    tools = build_tools()
    by_tool = {tool_name(n): n for n in REGISTRY.commands}

    async def list_tools(ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=tools)

    async def call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        args = params.arguments or {}
        try:
            if params.name in by_tool:
                result = await anyio.to_thread.run_sync(
                    lambda: engine.dispatch("execute", {"name": by_tool[params.name], "params": args, "label": f"Agent: {params.name}"})
                )
            elif params.name in EXTRA_TOOLS:
                method = EXTRA_TOOLS[params.name][0]
                result = await anyio.to_thread.run_sync(lambda: engine.dispatch(method, args))
            else:
                raise CommandError("UNKNOWN_TOOL", f"unknown tool {params.name}")
        except CommandError as exc:
            return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps({"error": exc.to_dict()}, ensure_ascii=False))], is_error=True)
        if params.name in ("open_project", "new_project") and isinstance(result, dict):
            result = {k: v for k, v in result.items() if k != "document"}
        return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, default=str))])

    return Server("lumina", version="0.1.0", instructions=INSTRUCTIONS, on_list_tools=list_tools, on_call_tool=call_tool)


def main() -> None:
    engine = connect()

    async def run():
        server = make_server(engine)
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    anyio.run(run)
