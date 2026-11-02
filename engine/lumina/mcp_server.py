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

from .client import FollowingEngine
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


# Answered in this process from the source DB; no project or engine involved.
_FILTERS = {
    "work": {"type": "string", "description": "work name or alias"},
    "character": {"type": "string"},
    "media_kind": {"type": "string", "enum": ["video", "image"]},
    "source_type": {"type": "string", "description": "trailer, pv, mv, gameplay, key_visual, illustration, fanart, "
                                                     "sns_post, article, store_page, search_result, ..."},
    "variant": {"type": "string", "enum": ["original", "copy", "official", "fan"]},
    "min_seconds": {"type": "number", "description": "usable length after keeping edge_margin clear of each scene edge"},
    "min_luma": {"type": "number", "description": "0..1; 0.15 drops near-black shots"},
    "max_text_overlay": {"type": "number", "description": "0..1; 0.2 drops shots covered in lyric/title typography"},
    "unused_only": {"type": "boolean"}, "used_only": {"type": "boolean"},
}
from .sourcedb.plan import SHOT_SCHEMA as _plan_shot, TOOL_DESCRIPTION as _plan_desc  # noqa: E402
SOURCE_TOOLS: dict[str, tuple[str, dict]] = {
    "source_search": (
        "Find shots for a narration line in the source DB. One line per hit: #cut_id, work, span, source type, shot, "
        "characters (? = unconfirmed), description, [dark/text/x(off-centre)/usedN]. Read lines, not pictures; look with "
        "source_sheet only at the final candidates. Place a hit with its path and span (source_in = start).",
        {"type": "object", "properties": {"text": {"type": "string", "description": "the narration line or keywords"},
                                          **_FILTERS, "limit": {"type": "integer", "default": 8}}}),
    "source_similar": (
        "Shots that look like a given cut (image embedding).",
        {"type": "object", "properties": {"cut_id": {"type": "integer"}, "limit": {"type": "integer", "default": 8}},
         "required": ["cut_id"]}),
    "source_cut": (
        "Everything known about cuts: file path, span in seconds and ticks, every fact with its producer and status, "
        "and where each was used.",
        {"type": "object", "properties": {"cut_ids": {"type": "array", "items": {"type": "integer"}}},
         "required": ["cut_ids"]}),
    "source_plan": (
        _plan_desc,
        {"type": "object", "properties": {
            "shots": {"type": "array", "minItems": 1, "items": _plan_shot},
            **{k: v for k, v in _FILTERS.items() if k not in ("min_seconds", "used_only")},
            "per_shot": {"type": "integer", "default": 5, "maximum": 12},
            "exclude_cuts": {"type": "array", "items": {"type": "integer"}, "description": "cuts already placed"},
            "sheet_top": {"type": "integer", "default": 0, "maximum": 3}},
         "required": ["shots"]}),
    "source_sheet": (
        "Look at candidate cuts: their keyframes on one numbered contact sheet (about 1.5k tokens for 12).",
        {"type": "object", "properties": {"cut_ids": {"type": "array", "items": {"type": "integer"}, "maxItems": 12}},
         "required": ["cut_ids"]}),
}


def _source_call(name: str, args: dict) -> types.CallToolResult:
    import base64
    import tempfile
    from pathlib import Path

    from .sourcedb import db, query
    from .sourcedb.describe import contact_sheet
    from .sourcedb.ingest import thumb_path

    con = db.connect(db.DEFAULT_DB_PATH)
    try:
        if name == "source_search":
            f = query.Filters(**{k: v for k, v in args.items() if k in _FILTERS})
            hits = query.search(con, args.get("text"), f, limit=int(args.get("limit", 8)))
            text = "\n".join(h.line() for h in hits) or "(no hits)"
        elif name == "source_similar":
            hits = query.similar(con, int(args["cut_id"]), "siglip2-so400m-384", limit=int(args.get("limit", 8)))
            text = "\n".join(f"{h.score:.2f} {h.line()}" for h in hits) or "(no embeddings)"
        elif name == "source_cut":
            out = []
            for cid in args["cut_ids"]:
                c = con.execute("SELECT c.*, m.kind, m.sha256 FROM cut c JOIN media m USING (media_id) WHERE cut_id = ?",
                                (cid,)).fetchone()
                if c is None:
                    out.append({"cut_id": cid, "error": "no such cut"})
                    continue
                S = query.S
                facts = con.execute(
                    "SELECT f.kind, COALESCE(e.name, f.value) AS value, f.num, f.producer, f.status, f.t0_ticks, f.t1_ticks "
                    "FROM fact f LEFT JOIN entity e ON e.entity_id = f.ref_id WHERE f.media_id = ? AND f.status != 'rejected' "
                    "AND f.kind NOT IN ('wd_tag', 'rating')", (c["media_id"],)).fetchall()
                used = con.execute("SELECT project, sequence, src_in_ticks, src_out_ticks, caption, scale, pos_x, pos_y "
                                   "FROM usage WHERE media_id = ?", (c["media_id"],)).fetchall()
                out.append({
                    "cut_id": cid, "path": db.media_path(con, c["media_id"]), "kind": c["kind"],
                    "start_s": c["start_ticks"] / S, "end_s": c["end_ticks"] / S,
                    "start_ticks": c["start_ticks"], "end_ticks": c["end_ticks"],
                    "facts": [{k: f[k] for k in ("kind", "value", "num", "producer", "status")} for f in facts
                              if db._overlaps(f["t0_ticks"], f["t1_ticks"], c["start_ticks"], c["end_ticks"])],
                    "used": [{"project": Path(u["project"]).stem, "sequence": u["sequence"], "caption": u["caption"],
                              "src_s": [u["src_in_ticks"] / S, u["src_out_ticks"] / S], "scale": u["scale"],
                              "pos": [u["pos_x"], u["pos_y"]]} for u in used
                             if db._overlaps(u["src_in_ticks"], u["src_out_ticks"], c["start_ticks"], c["end_ticks"])],
                })
            text = json.dumps(out, ensure_ascii=False)
        elif name == "source_plan":
            from .sourcedb import plan
            shots = [plan.Shot(**{k: s[k] for k in ("text", "look", "seconds", "character", "media_kind") if k in s})
                     for s in args["shots"]]
            f = query.Filters(**{k: v for k, v in args.items() if k in _FILTERS})
            notes: list[str] = []
            results = plan.plan(con, shots, f, per_shot=int(args.get("per_shot", 5)),
                                exclude_cuts=tuple(args.get("exclude_cuts") or ()), notes=notes)
            content = [types.TextContent(type="text", text=plan.render(shots, results, tuple(notes)))]
            tiles = plan.sheet_tiles(con, results, int(args.get("sheet_top", 0)))
            for i in range(0, len(tiles), 12):
                part = tiles[i:i + 12]
                with tempfile.TemporaryDirectory() as tmp:
                    sheet = Path(tmp) / "sheet.jpg"
                    contact_sheet([p for _, p in part], sheet)
                    data = base64.b64encode(sheet.read_bytes()).decode("ascii")
                content += [types.ImageContent(type="image", data=data, mime_type="image/jpeg"),
                            types.TextContent(type="text", text=" ".join(f"{j + 1}={lab}" for j, (lab, _) in enumerate(part)))]
            return types.CallToolResult(content=content)
        else:  # source_sheet
            rows = [con.execute("SELECT c.keyframe_ticks, m.sha256 FROM cut c JOIN media m USING (media_id) WHERE cut_id = ?",
                                (cid,)).fetchone() for cid in args["cut_ids"][:12]]
            with tempfile.TemporaryDirectory() as tmp:
                sheet = Path(tmp) / "sheet.jpg"
                contact_sheet([thumb_path(r["sha256"], r["keyframe_ticks"]) for r in rows if r], sheet)
                data = base64.b64encode(sheet.read_bytes()).decode("ascii")
            legend = " ".join(f"{i + 1}=#{cid}" for i, cid in enumerate(args["cut_ids"][:12]))
            return types.CallToolResult(content=[
                types.ImageContent(type="image", data=data, mime_type="image/jpeg"),
                types.TextContent(type="text", text=legend)])
    finally:
        con.close()
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)])


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
    for name, (desc, schema) in SOURCE_TOOLS.items():
        tools.append(types.Tool(name=name, description=desc, input_schema=schema))
    from .frame_strip import TOOL as STRIP
    tools.append(types.Tool(name="get_frame_strip", description=STRIP[0], input_schema=STRIP[1]))
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
            elif params.name == "get_frame_strip":
                from .frame_strip import result as strip_result
                return await anyio.to_thread.run_sync(lambda: strip_result(engine, args))
            elif params.name in SOURCE_TOOLS:
                return await anyio.to_thread.run_sync(lambda: _source_call(params.name, args))
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
    engine = FollowingEngine()

    async def run():
        server = make_server(engine)
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    anyio.run(run)
