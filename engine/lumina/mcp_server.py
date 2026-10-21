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
Wrap multi-step edits in begin_transaction/commit_transaction so they undo as one step (rollback_transaction on failure).
Use get_frame_image to look at the result, and export_media to render a verified file."""

_OBJ = {"type": "object", "properties": {}, "additionalProperties": False}

EXTRA_TOOLS: dict[str, tuple[str, str, dict, Any]] = {
    # tool name: (rpc method, description, schema, param mapper)
    "get_project": ("project.summary", "Project overview: bins, media (ids, durations, streams) and sequences.", _OBJ, None),
    "get_timeline_state": ("timeline.state", "Tracks, clips (ticks/seconds/timecode), markers and playhead of a sequence.",
                           {"type": "object", "properties": {"sequence_id": {"type": "string"}}}, None),
    "get_clips": ("clip.get", "Every property of clips, as Effect Controls shows them: motion (position / scale / rotation / "
                  "anchor), opacity, volume and gain, speed, keyframes, effects, graphic layers (text, style) and caption. "
                  "clip_ids, or every clip of track_id, or the whole sequence.",
                  {"type": "object", "properties": {"sequence_id": {"type": "string"},
                                                    "clip_ids": {"type": "array", "items": {"type": "string"}},
                                                    "track_id": {"type": "string"}}}, None),
    "undo": ("undo", "Undo the last edit (Ctrl+Z).", _OBJ, None),
    "redo": ("redo", "Redo (Ctrl+Shift+Z).", _OBJ, None),
    "begin_transaction": ("transaction.begin", "Group following edits into one undo step.",
                          {"type": "object", "properties": {"label": {"type": "string"}}}, None),
    "commit_transaction": ("transaction.commit", "Close the open transaction.", _OBJ, None),
    "rollback_transaction": ("transaction.rollback", "Revert every edit made since begin_transaction.", _OBJ, None),
    "list_projects": ("project.list", "Open projects (several can be open; edits go to the active one).", _OBJ, None),
    "activate_project": ("project.activate", "Make an open project the active one (its key from list_projects).",
                         {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}, None),
    "close_project": ("project.close", "Close a project (default: the active one). Refuses with UNSAVED when it has unsaved "
                      "changes unless discard=true - save it first or ask the user.",
                      {"type": "object", "properties": {"key": {"type": "string"}, "discard": {"type": "boolean"}}}, None),
    "media_cache_info": ("cache.info", "Media cache folder, size, and how much belongs to open projects.", _OBJ, None),
    "clean_media_cache": ("cache.clean", "Delete media cache (thumbnails, waveforms, proxies): mode unused | all | older "
                          "(with days). Open projects' files are regenerated.",
                          {"type": "object", "properties": {"mode": {"type": "string", "enum": ["unused", "all", "older"]},
                                                            "days": {"type": "number"}}}, None),
    "new_project": ("project.new", "Start a new project (opened next to the open ones): empty, or a copy of a template .lumina "
                    "(the template is only read; the new project is unsaved until saved under its own path).",
                    {"type": "object", "properties": {"name": {"type": "string"},
                                                      "template": {"type": "string", "description": "a .lumina project to start from"}}}, None),
    "open_project": ("project.open", "Open a .lumina project.",
                     {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}, None),
    "save_project": ("project.save", "Save the project (path required the first time).",
                     {"type": "object", "properties": {"path": {"type": "string"}}}, None),
    "list_jobs": ("jobs.list", "Background jobs (proxy/thumbnail/waveform/export) with status and progress.", _OBJ, None),
    "export_media": ("export.start",
                     "Export a sequence to a file (File > Export > Media). Renders exactly what the Program monitor shows "
                     "(motion, keyframes, opacity, crop, text graphics, audio volume), encodes, then verifies the output "
                     "with ffprobe. wait=true blocks until done and returns the result; otherwise poll list_jobs.",
                     {"type": "object", "properties": {
                         "path": {"type": "string", "description": "output file; extension defaults from the preset"},
                         "sequence_id": {"type": "string"},
                         "preset": {"type": "string", "enum": ["h264", "hevc", "prores", "wav"], "default": "h264"},
                         "range": {"type": "string", "enum": ["entire", "in_out"], "default": "entire"},
                         "start": {"type": ["string", "integer"], "description": "custom range start (overrides range)"},
                         "end": {"type": ["string", "integer"]},
                         "hardware": {"type": "string", "enum": ["auto", "on", "off"], "default": "auto",
                                      "description": "GPU encoder (NVENC/AMF/QSV) when available"},
                         "wait": {"type": "boolean", "default": True}},
                      "required": ["path"]}, lambda a: {"wait": True, **a}),
    "cancel_export": ("export.cancel", "Cancel a queued or running export job.",
                      {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]}, None),
    "list_export_presets": ("export.presets", "Export formats/presets and whether a hardware encoder applies.", _OBJ, None),
    "export_frame": ("export.frame", "Export Frame (Ctrl+Shift+E): render one sequence frame to a PNG/JPEG file.",
                     {"type": "object", "properties": {"time": {"type": ["string", "integer"], "description": "default playhead"},
                                                       "sequence_id": {"type": "string"}, "path": {"type": "string"}},
                      "required": ["path"]}, None),
    "get_frame_image": ("export.frame",
                        "Look at the edit: returns the rendered sequence frame at a time (default playhead) as an image, "
                        "exactly as it will export.",
                        {"type": "object", "properties": {"time": {"type": ["string", "integer"]}, "sequence_id": {"type": "string"},
                                                          "max_size": {"type": "integer", "default": 960}}},
                        lambda a: {"max_size": 960, **a}),
    "convert_time": ("time.convert", "Convert a time value to ticks/seconds/frame/timecode in a sequence's frame rate.",
                     {"type": "object", "properties": {"time": {"type": ["string", "integer"]}, "sequence_id": {"type": "string"}},
                      "required": ["time"]}, None),
    "caption_export_srt": ("caption.export_srt", "Write a caption track's cues to an .srt file (default: the topmost caption track).",
                           {"type": "object", "properties": {"path": {"type": "string"}, "track_id": {"type": "string"},
                                                             "sequence_id": {"type": "string"}}, "required": ["path"]}, None),
    "list_text_styles": ("styles.list", "Text styles in the library (imported from Premiere .prtextstyle files); apply with caption_apply_style / graphic_apply_style.", _OBJ, None),
    "import_text_styles": ("styles.import_premiere", "Import Premiere text styles: a .prtextstyle file, a folder, or a .prproj; default Premiere's Text Styles folder.",
                           {"type": "object", "properties": {"path": {"type": "string"}}}, None),
    "media_scan": ("media.scan", "Offline media (missing file / Made Offline), duplicate imports and unused media.", _OBJ, None),
    "analyze_media": ("analysis.detect",
                      "Analyse a media item, or the source range a clip uses: silence / black / freeze intervals, scene changes, "
                      "loudness (EBU R128) or peak/RMS. With clip_id, results also carry sequence ticks.",
                      {"type": "object", "properties": {
                          "kind": {"type": "string", "enum": ["silence", "black", "freeze", "scene", "loudness", "peak_rms"]},
                          "media_id": {"type": "string"}, "clip_id": {"type": "string"}, "sequence_id": {"type": "string"},
                          "silence_db": {"type": "number", "default": -40}, "min_silence": {"type": "number", "default": 0.5},
                          "scene_threshold": {"type": "number", "default": 0.4}, "black_min": {"type": "number", "default": 0.1}},
                       "required": ["kind"]}, None),
    "plan_cuts": ("analysis.plan_cuts",
                  "Plan removing silence (audio tracks) or black (video tracks): sequence ranges silent/black on every chosen "
                  "track that has a clip there, padded, frame-snapped, clear of markers. apply=true removes them in one undo "
                  "step (ripple=false lifts instead of extracting).",
                  {"type": "object", "properties": {
                      "kind": {"type": "string", "enum": ["silence", "black"]}, "sequence_id": {"type": "string"},
                      "track_ids": {"type": "array", "items": {"type": "string"}}, "clip_ids": {"type": "array", "items": {"type": "string"}},
                      "silence_db": {"type": "number", "default": -40}, "min_duration": {"type": "number", "default": 0.5},
                      "padding_before": {"type": "number", "default": 0.1}, "padding_after": {"type": "number", "default": 0.1},
                      "protect_markers": {"type": "boolean", "default": True}, "apply": {"type": "boolean", "default": False},
                      "ripple": {"type": "boolean", "default": True}},
                   "required": ["kind"]}, None),
    "plan_scene_cuts": ("analysis.plan_scene_cuts",
                        "Scene Edit Detection: razor points where a clip's picture cuts to a new shot. apply=true razors there "
                        "(one undo step).",
                        {"type": "object", "properties": {
                            "sequence_id": {"type": "string"}, "track_ids": {"type": "array", "items": {"type": "string"}},
                            "clip_ids": {"type": "array", "items": {"type": "string"}},
                            "scene_threshold": {"type": "number", "default": 0.4}, "min_shot": {"type": "number", "default": 0.5},
                            "apply": {"type": "boolean", "default": False}}}, None),
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


def _image_result(info: dict) -> types.CallToolResult:
    import base64
    import io
    import os

    from PIL import Image

    with Image.open(info["path"]) as im:
        buf = io.BytesIO()
        im.convert("RGB").save(buf, "JPEG", quality=85)
    try:
        os.unlink(info["path"])
    except OSError:
        pass
    meta = {k: v for k, v in info.items() if k != "path"}
    return types.CallToolResult(content=[
        types.ImageContent(type="image", data=base64.b64encode(buf.getvalue()).decode("ascii"), mime_type="image/jpeg"),
        types.TextContent(type="text", text=json.dumps(meta, ensure_ascii=False)),
    ])


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
                method, _, _, mapper = EXTRA_TOOLS[params.name]
                mapped = mapper(args) if mapper else args
                result = await anyio.to_thread.run_sync(lambda: engine.dispatch(method, mapped))
            else:
                raise CommandError("UNKNOWN_TOOL", f"unknown tool {params.name}")
        except CommandError as exc:
            return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps({"error": exc.to_dict()}, ensure_ascii=False))], is_error=True)
        if params.name == "get_frame_image":
            return _image_result(result)
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
