"""Read-only, numbered frame evidence for MCP. Never classifies editing intent."""
from __future__ import annotations

import base64
import io
import json
import re
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageOps
from mcp import types

from .adb.quiet_process import NO_WINDOW
from .commands.registry import CommandError
from .media.probe import ffmpeg_bin


TOOLS = {
    "inspect_source_frames": (
        "Decode original source frames in presentation order, numbered from zero. Returns actual consecutive images, source PTS and pixel change clues. Use count=24 step=1 around source in/out to find even one unwanted frame; step>1 is sampling, never proof of clean boundaries. No automatic cut verdict or edits. Decodes from file beginning for exact indexing; long files may time out.",
        {"type": "object", "properties": {
            "path": {"type": "string"}, "start_frame": {"type": "integer", "minimum": 0},
            "count": {"type": "integer", "minimum": 1, "maximum": 24, "default": 12},
            "step": {"type": "integer", "minimum": 1, "maximum": 120, "default": 1}},
         "required": ["path", "start_frame"], "additionalProperties": False}),
    "inspect_timeline_frames": (
        "Read rendered sequence frames consecutively around a cut, with numbered image evidence and pixel change clues. start_frame is zero-based SEQUENCE frame index. Always supply sequence_id. Read-only. Check source separately; sequence fps, speed, overlays and transitions affect final output. No rhythm/motion verdict from stills.",
        {"type": "object", "properties": {
            "sequence_id": {"type": "string"}, "start_frame": {"type": "integer", "minimum": 0},
            "count": {"type": "integer", "minimum": 1, "maximum": 24, "default": 12}},
         "required": ["sequence_id", "start_frame"], "additionalProperties": False}),
}


def bounds(args):
    start, count, step = args.get("start_frame"), args.get("count", 12), args.get("step", 1)
    if any(type(v) is not int for v in (start, count, step)) or start < 0 or not 1 <= count <= 24 or not 1 <= step <= 120:
        raise CommandError("INVALID_PARAMS", "start_frame >= 0, count 1..24, step 1..120 must be integers")
    return start, count, step


def evidence(images, frames, meta):
    rows, previous = [], None
    for im, frame in zip(images, frames):
        pixels = np.asarray(im.convert("RGB").resize((96, 54)), dtype=np.float32) / 255
        delta = None if previous is None else float(np.abs(pixels - previous).mean())
        rows.append({**frame, "mean_rgb_change": None if delta is None else round(delta, 6)})
        previous = pixels
    content = []
    # Six readable tiles per sheet rather than a single illegible 24-frame grid.
    for offset in range(0, len(images), 6):
        subset = images[offset:offset + 6]
        sheet = Image.new("RGB", (960, 294 * ((len(subset) + 1) // 2)), "#141414")
        draw = ImageDraw.Draw(sheet)
        for j, im in enumerate(subset):
            x, y = (j % 2) * 480, (j // 2) * 294
            tile = ImageOps.contain(im, (480, 270))
            sheet.paste(tile, (x + (480 - tile.width) // 2, y + (270 - tile.height) // 2))
            row = rows[offset + j]
            draw.text((x + 8, y + 273), f"frame {row['frame']} | pts {row.get('pts_seconds', 'sequence')} | d={row['mean_rgb_change']}", fill="white")
        buf = io.BytesIO()
        sheet.save(buf, "JPEG", quality=90)
        content.append(types.ImageContent(type="image", data=base64.b64encode(buf.getvalue()).decode(), mime_type="image/jpeg"))
    content.append(types.TextContent(type="text", text=json.dumps({**meta, "frames": rows,
        "verdict": "not_assessed", "limitations": "Pixel change is a clue, not scene identity or defect classification. Stills do not verify rhythm, action completion or audio. Source frame indices must not be used as sequence indices. End is exclusive; inspect end-1 and end separately."}, ensure_ascii=False)))
    return types.CallToolResult(content=content)


def inspect(name, args, engine):
    start, count, step = bounds(args)
    images, frames = [], []
    if name == "inspect_timeline_frames":
        if not args.get("sequence_id"):
            raise CommandError("INVALID_PARAMS", "sequence_id required")
        # Pin the project too, so a user switching projects cannot mix evidence.
        before = engine.dispatch("project.list", {})
        timeline = engine.dispatch("timeline.state", {"sequence_id": args["sequence_id"]})
        for n in range(start, start + count):
            info = engine.dispatch("export.frame", {"sequence_id": args["sequence_id"], "time": f"{n}f", "max_size": 960})
            path = Path(info["path"])
            try:
                with Image.open(path) as im:
                    images.append(im.convert("RGB"))
            finally:
                path.unlink(missing_ok=True)
            frames.append({"frame": n, "pts_seconds": info.get("seconds"), "ticks": info.get("time"), "timecode": info.get("timecode")})
        if before != engine.dispatch("project.list", {}) or timeline != engine.dispatch("timeline.state", {"sequence_id": args["sequence_id"]}):
            raise CommandError("STATE_CHANGED", "Project state changed while rendering. Repeat read-only inspection.")
        return evidence(images, frames, {"domain": "sequence", "sequence_id": args["sequence_id"], "complete": True, "step": 1})
    path = Path(args.get("path", ""))
    if not path.is_file():
        raise CommandError("INVALID_PARAMS", "path must be an existing local source file")
    last = start + (count - 1) * step
    with tempfile.TemporaryDirectory(prefix="lumina-frame-review-") as tmp:
        select = f"select=between(n\\,{start}\\,{last})*not(mod(n-{start}\\,{step})),showinfo,scale=960:540:force_original_aspect_ratio=decrease"
        try:
            proc = subprocess.run([ffmpeg_bin(), "-hide_banner", "-nostdin", "-i", str(path.resolve()),
                "-map", "0:v:0", "-vf", select, "-frames:v", str(count), "-fps_mode", "passthrough",
                "-q:v", "2", str(Path(tmp) / "%03d.jpg")], capture_output=True, timeout=120, **NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CommandError("FRAME_REVIEW_FAILED", str(exc)) from exc
        if proc.returncode:
            raise CommandError("FRAME_REVIEW_FAILED", proc.stderr.decode(errors="replace")[-1500:])
        pts = re.findall(r"\bn:\s*\d+.*?\bpts_time:\s*([-+\d.eE]+)", proc.stderr.decode(errors="replace"))
        files = sorted(Path(tmp).glob("*.jpg"))
        if len(pts) < len(files) or not files:
            raise CommandError("FRAME_REVIEW_FAILED", "No frames or missing PTS; requested range may be beyond EOF")
        for i, file in enumerate(files):
            with Image.open(file) as im:
                images.append(im.convert("RGB"))
            frames.append({"frame": start + i * step, "pts_seconds": float(pts[i])})
    return evidence(images, frames, {"domain": "source", "path": str(path.resolve()),
        "step": step, "complete": len(images) == count, "requested_count": count,
        "coverage": "consecutive" if step == 1 else "sampled_not_boundary_proof"})
