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

from .client import FollowingEngine, GuiEngine
from .commands.registry import REGISTRY, CommandError

INSTRUCTIONS = """Lumina is a Premiere-style video editor. Every edit is an undoable command.
Times accept ticks (int) or strings: "12.5s", "45f", "00:01:02:15". Omitted sequence_id = active sequence.
Typical flow: open_project/new_project -> media_import -> sequence_create_from_media -> edits -> get_timeline_state -> save_project.
GUI-first: call get_editor_status to inspect without launching. Before project work call ensure_editor(project_path).
It starts the GUI if needed, activates an already open target without reloading unsaved edits, or opens the target alongside other projects.
All editing uses the GUI engine; a missing GUI never silently creates a headless project. Verify the active project before edits.
Wrap multi-step edits in begin_transaction/commit_transaction so they undo as one step (rollback_transaction on failure).
Use get_frame_image to look at the result, and export_media to render a verified file.
Shorts, few calls: shorts_plan (draft from the captions) -> source_plan (one call, `look` per shot) -> shorts_build
(shots + end + effects + sounds + check, one undo) -> fix what its check lists -> get_frame_strip once -> save.
Sources arriving later: shorts_build without shots (base setup, plan stored as S-markers; shorts_mark_shots to add
looks) -> per handed-over file: intake -> shorts_gaps(paths) -> clip_place_shots(place) -> shorts_check."""

_OBJ = {"type": "object", "properties": {}, "additionalProperties": False}

EXTRA_TOOLS: dict[str, tuple[str, str, dict, Any]] = {
    'source_review_choices': ('source_review.choices', 'Read IP-catalog work and character enums; query searches works, work scopes characters. Also returns source type and shot enums.',
        {'type':'object','properties':{'query':{'type':'string'},'work':{'type':'string'}},'additionalProperties':False}, None),
    'source_review_save': ('source_review.save', 'Atomically save source edits and synchronize search DB. Revision and DB baseline checked. Boundary moves this cut end and next cut start. Untouched AI facts are not approved. No timeline changes.',
        {'type':'object','properties':{'media_id':{'type':'string'},'revision':{'type':'integer'},'changes':{'type':'array','minItems':1,'maxItems':50,'items':{'type':'object','properties':{'action':{'enum':['update','identity','boundary','split','merge','keyframe','undo']},'cut_id':{'type':'string'},'time':{'type':'integer'},'fields':{'type':'object'}},'required':['action'],'additionalProperties':False}}},'required':['media_id','revision','changes'],'additionalProperties':False}, lambda a: {**a, 'actor':'agent'}),
    'source_review_get': ('source_review.get', 'Read active-project source review draft, detected boundaries, IP, characters, speaker/dialogue and provenance. media_id is the project media ID, not source DB ID.',
        {'type':'object','properties':{'media_id':{'type':'string'}},'required':['media_id'],'additionalProperties':False}, None),
    'source_review_start': ('source_review.start', 'Read existing analysis first. ingest/missing run only absent stages; existing cuts and completed jobs are reused. Individual stages skip completed work unless force=true. Stages: cuts, describe (Claude), tagger or speech (local). Poll source_review_get. Does not publish or edit timeline. Re-running cuts replaces draft; undo restores it.',
        {'type':'object','properties':{'media_id':{'type':'string'},'revision':{'type':'integer'},'stage':{'enum':['ingest','missing','cuts','describe','tagger','speech']},'force':{'type':'boolean'}},'required':['media_id','stage'],'additionalProperties':False}, None),
    'source_review_update': ('source_review.update', 'Edit revision-checked source review draft. Actions: update fields; identity; split/boundary/keyframe at integer source ticks; merge next; review; undo. Characters on screen are NOT necessarily speakers. Review requires examining the actual frames/audio.',
        {'type':'object','properties':{'media_id':{'type':'string'},'revision':{'type':'integer'},'action':{'enum':['update','identity','split','boundary','keyframe','merge','review','undo']},'cut_id':{'type':'string'},'time':{'type':'integer'},'fields':{'type':'object'},'reviewed':{'type':'boolean'}},'required':['media_id','revision','action'],'additionalProperties':False}, lambda a: {**a, 'actor': 'agent'}),
    'source_review_preview': ('source_review.preview', 'Preview exact revision for publishing reviewed source cuts to Lumina source.db. Does not alter timeline.',
        {'type':'object','properties':{'media_id':{'type':'string'},'revision':{'type':'integer'}},'required':['media_id','revision'],'additionalProperties':False}, None),
    'source_review_publish': ('source_review.publish', 'Publish reviewed draft with token from source_review_preview. All cuts including exclusions must be reviewed. Replaces source search evidence; preserves audit history. Does not alter project timeline.',
        {'type':'object','properties':{'media_id':{'type':'string'},'revision':{'type':'integer'},'token':{'type':'string'}},'required':['media_id','revision','token'],'additionalProperties':False}, lambda a: {**a, 'actor': 'agent'}),
    'source_review_import_srt': ('source_review.import_srt', 'Replace draft dialogues from source-time SRT file. Leaves speakers unknown and all cuts unreviewed.',
        {'type':'object','properties':{'media_id':{'type':'string'},'revision':{'type':'integer'},'path':{'type':'string'}},'required':['media_id','revision','path'],'additionalProperties':False}, None),
    'speech_status': ('speech.status', 'Check Lumina internal speech runtime. No Phrase Studio server required.', _OBJ, None),
    'speech_start': ('speech.start', 'Transcribe narration, align script lines or retime existing caption tracks in Lumina. Produces a preview; no timeline mutation until speech_apply. Korean acoustic timing; never estimates from character count.',
                     {'type': 'object', 'properties': {'sequence_id': {'type': 'string'}, 'audio_track_id': {'type': 'string'}, 'mode': {'type': 'string', 'enum': ['transcribe', 'script', 'retime']}, 'caption_track_ids': {'type': 'array', 'items': {'type': 'string'}}, 'shorts': {'type': 'boolean'}}, 'required': ['audio_track_id']}, None),
    'speech_list': ('speech.list', 'List speech jobs for the active project.', _OBJ, None),
    'speech_job': ('speech.job', 'Read speech progress and the full reviewable result; timestamps are sequence ticks.', {'type': 'object', 'properties': {'job_id': {'type': 'string'}}, 'required': ['job_id']}, None),
    'speech_cancel': ('speech.cancel', 'Cancel a speech job or discard its preview without changing the timeline.', {'type': 'object', 'properties': {'job_id': {'type': 'string'}}, 'required': ['job_id']}, None),
    'speech_apply': ('speech.apply', 'Apply a reviewed speech preview as one undoable edit; refuses changed inputs or another project. Caption finishing removes terminal comma/period, starts at frame zero, closes gaps and splits long cues at acoustic clause boundaries. Retiming preserves original tracks/styles; first child keeps the original ID. Inspect quality_warnings.', {'type': 'object', 'properties': {'job_id': {'type': 'string'}, 'transcript_only': {'type': 'boolean'}}, 'required': ['job_id']}, None),
    "get_editor_status": ("editor.status", "Inspect GUI connection and open projects without starting the GUI.", _OBJ, None),
    "ensure_editor": ("editor.ensure", "Connect/start the GUI and prepare the requested project. An already open target is activated without reloading; other projects and unsaved work stay open.",
                      {"type": "object", "properties": {"project_path": {"type": "string"}}}, None),
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
    "shorts_plan": ("shorts.plan",
                    "Draft a Short's edit from its captions in one read-only call, by docs/shorts-editing grammar: shots "
                    "(captions grouped by sentence; cuts on clause ends ≥1 s, list items or `reveal` captions; lead-ins "
                    "ride along; none under 1.1 s), each caption's effect (pop/slam/fade) and sound (W3/W4/W1-6 "
                    "rotation, hits as 'choose', Bells 2, 물방울, 티딩), plus grammar problems. Reads the sequence's "
                    "caption tracks (or an srt file). Review it, then: shots -> source_plan (add look) -> "
                    "clip_place_shots; effects -> shorts_caption_effects; sounds -> shorts_place_sfx.",
                    {"type": "object", "properties": {
                        "sequence_id": {"type": "string"},
                        "srt": {"type": "string", "description": "read captions from this .srt instead of the sequence"},
                        "reveal": {"type": "array", "items": {"type": "integer"},
                                   "description": "caption numbers that must start a new shot (a name reveal, a new subject)"},
                        "auto_reveal": {"type": "boolean", "default": True,
                                        "description": "cut where a caption opens a reveal mid-sentence (바로 …)"},
                        "names": {"type": "array", "items": {"type": "string"},
                                  "description": "names to mark as 'reveal?' where first said mid-sentence; default the "
                                                 "source DB's works and characters"},
                        "min_shot": {"type": "number", "default": 1.1},
                        "format": {"type": "string", "enum": ["text", "json"], "default": "text"}}}, None),
    "shorts_gaps": ("shorts.gaps",
                    "A Short filled as sources arrive: which planned shots (markers S1, S2 ... from shorts_mark_shots or "
                    "shorts_build) are still empty on V1, with their look and narration. Give the new source files (paths, "
                    "already intaken into the source DB) to search only them for the empty shots: returns candidates and a "
                    "ready clip_place_shots list (`place`, each cut once). Read-only.",
                    {"type": "object", "properties": {
                        "sequence_id": {"type": "string"},
                        "paths": {"type": "array", "items": {"type": "string"}, "description": "the source files just handed over"},
                        "source_media_ids": {"type": "array", "items": {"type": "integer"}, "description": "or their DB media ids"},
                        "per_shot": {"type": "integer", "default": 3},
                        "include_partial": {"type": "boolean", "default": False},
                        "use_pictures": {"type": "boolean", "default": True,
                                         "description": "picture search (SigLIP) as well as words"}}}, None),
    "shorts_check": ("shorts.check",
                     "Check a Short's edit as text, without frames: short shots, gaps, cuts off caption starts, speeds, "
                     "neighbour-shot flashes and scene changes inside shots (source DB), dark shots, the ending (video = "
                     "narration + 3-9 f; V5/V6/last caption/V1 on that frame), overlapping or too-wide captions, untagged or "
                     "crowded sounds. Lists only problems, each with its frame.",
                     {"type": "object", "properties": {"sequence_id": {"type": "string"},
                                                       "use_db": {"type": "boolean", "default": True},
                                                       "format": {"type": "string", "enum": ["text", "json"], "default": "text"}}},
                     None),
    "edit_check": ("shorts.check",
                   "Check a long-form (profile longform) or Short edit as text, without frames: the same source reused, "
                   "captions (first at frame 0, gaps, overlaps, trailing , or ., verb/auxiliary split, over 3.5 s / 5 s, two "
                   "sentences in one), cuts that split a phrase, neighbour-shot flashes, scene changes inside shots and dark "
                   "shots (source DB), gaps. Lists only problems, each with its frame.",
                   {"type": "object", "properties": {"sequence_id": {"type": "string"},
                                                     "profile": {"type": "string", "enum": ["longform", "shorts"], "default": "longform"},
                                                     "use_db": {"type": "boolean", "default": True},
                                                     "format": {"type": "string", "enum": ["text", "json"], "default": "text"}}},
                   lambda a: {"profile": "longform", **a}),
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


# Tool profiles (LUMINA_TOOLS): every tool's schema rides on every request in clients that do not defer
# them, and all of them come to ~135k characters. A Short needs a fraction. Entries are profile names,
# tool names or prefixes ending in '*', comma-separated; default "all". Hidden tools still answer if called.
CORE_TOOLS = (
    "get_project", "get_timeline_state", "get_clips", "undo", "redo", "begin_transaction", "commit_transaction",
    "rollback_transaction", "open_project", "save_project", "list_projects", "activate_project", "get_frame_image",
    "get_frame_strip", "export_media", "list_jobs", "convert_time", "media_import", "clip_place", "clip_move",
    "clip_remove", "clip_trim", "clip_set", "clip_set_property", "clip_set_speed", "clip_razor", "caption_add",
    "caption_set", "marker_*", "sequence_duplicate", "sequence_rename", "sequence_set_active", "track_set", "edit_check",
    "source_*", "clip_place_shots", "get_editor_status", "ensure_editor", "inspect_source_frames",
    "inspect_timeline_frames")
TOOL_PROFILES = {
    "core": CORE_TOOLS,
    "shorts": CORE_TOOLS + ("shorts_*", "clip_align_marker", "clip_audio_gain", "clip_set_audio_tag", "audio_auto_match",
                            "graphic_replace_text", "graphic_set_layer", "graphic_create_text", "graphic_add_image",
                            "caption_import_srt", "caption_split_text", "caption_merge_next", "speech_*", "text_*",
                            "media_refresh"),
    "longform": CORE_TOOLS + ("caption_*", "graphic_*", "effect_*", "transition_*", "keyframes_*", "clip_keyframe",
                              "clip_set_keyframes", "clip_set_keyframe_ease", "clip_toggle_animation", "clip_fit_to_frame",
                              "clip_frame_hold", "clip_slip", "clip_slide", "clip_roll", "audio_*", "clip_audio_gain",
                              "clip_set_audio_tag", "analyze_media", "plan_cuts", "plan_scene_cuts", "sequence_*",
                              "track_add", "export_frame", "speech_*", "text_*", "media_refresh"),
}


def selected(names: list[str], spec: str | None) -> set[str]:
    """Tool names a LUMINA_TOOLS spec keeps (all of them for None/'' /'all')."""
    if not spec or spec.strip().lower() == "all":
        return set(names)
    pats: list[str] = []
    for part in (x.strip() for x in spec.split(",")):
        if not part:
            continue
        if part.lower() == "all":
            return set(names)
        pats += list(TOOL_PROFILES.get(part.lower(), (part,)))
    keep = set()
    for pat in pats:
        keep |= {n for n in names if (n.startswith(pat[:-1]) if pat.endswith("*") else n == pat)}
    return keep


def build_tools(profile: str | None = None) -> list[types.Tool]:
    from .frame_review import TOOLS as FRAME_TOOLS
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
    for name, (desc, schema) in FRAME_TOOLS.items():
        tools.append(types.Tool(name=name, description=desc, input_schema=schema))
    keep = selected([t.name for t in tools], profile)
    return [t for t in tools if t.name in keep]


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
    import os

    tools = build_tools(os.environ.get("LUMINA_TOOLS"))
    by_tool = {tool_name(n): n for n in REGISTRY.commands}

    async def list_tools(ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=tools)

    async def call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        args = params.arguments or {}
        try:
            if params.name in ("inspect_source_frames", "inspect_timeline_frames"):
                from .frame_review import inspect
                return await anyio.to_thread.run_sync(lambda: inspect(params.name, args, engine))
            elif params.name in by_tool:
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


def _warm_text_encoder() -> None:
    """Start the SigLIP text encoder in the background, so the first source_plan does not wait ~15 s for it."""
    import threading

    def warm():
        try:
            from .sourcedb import textvec

            textvec.shared().encode(["warm up"])
        except Exception:  # noqa: BLE001 - no GPU env / no DB on this machine: source_plan reports it when called
            pass

    threading.Thread(target=warm, name="text-encoder-warmup", daemon=True).start()


def main() -> None:
    import os

    # Explicit opt-in only, for unattended workflows requested by the user.
    engine = FollowingEngine() if os.environ.get('LUMINA_HEADLESS') == '1' else GuiEngine()
    if os.environ.get("LUMINA_WARM_ENCODER", "1") != "0":
        _warm_text_encoder()

    async def run():
        server = make_server(engine)
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    try:
        anyio.run(run)
    finally:
        engine.close()  # Disconnect MCP; the independently running GUI stays open.
