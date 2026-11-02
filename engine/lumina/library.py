"""Source library: index a folder of footage once, then search it as text.

An agent (or the ``lumina auto`` planner) should not have to watch footage to
find what to use. ``index`` walks a folder and, for every media file, records
what an editor would log by hand:

- shots (scene changes) with a keyframe each, its brightness and sharpness
  (dark or blurry shots are flagged so a planner can skip them),
- how much of each shot has sound above the silence threshold,
- a timestamped transcript (when ``faster-whisper`` is installed),
- descriptions and tags, written later by ``annotate`` (an agent looking at the
  contact sheet, or the planner's vision pass).

Everything lives in ``<folder>/.lumina/index.json`` next to the footage.
Re-indexing only processes files whose size or mtime changed. ``search`` and
``overview`` read that file, so finding sources costs no media decoding and few
tokens.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from .adb.quiet_process import NO_WINDOW
from .commands.registry import CommandError
from .media.probe import IMAGE_EXTS, ProbeError, ffmpeg_bin, probe
from .timebase import TICKS_PER_SECOND as TPS

VERSION = 1
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm", ".mts", ".m2ts", ".mxf", ".wmv", ".flv", ".3gp"}
AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".aif", ".aiff", ".wma"}
STILL_EXTS = IMAGE_EXTS - {".gif", ".psd"}
DARK_LUMA = 40.0  # mean luma (0-255) below which a shot reads as too dark
SOFT_SHARPNESS = 6.0  # mean edge strength below which a shot reads as out of focus

Progress = Callable[[str], None]


def index_dir(root: str | Path) -> Path:
    return Path(root) / ".lumina"


def index_path(root: str | Path) -> Path:
    return index_dir(root) / "index.json"


def load(root: str | Path) -> dict:
    path = index_path(root)
    if not path.exists():
        raise CommandError("NO_INDEX", f"no library index in {root}; run library_index (lumina index {root}) first")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["root"] = str(Path(root).resolve())
    return data


def save(root: str | Path, data: dict) -> None:
    path = index_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def media_kind(path: Path) -> str | None:
    ext = path.suffix.lower()
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in STILL_EXTS:
        return "image"
    return None


def file_id(rel: str) -> str:
    return hashlib.sha1(rel.encode("utf-8")).hexdigest()[:8]


# ---------------------------------------------------------------- per-file analysis

def _ffmpeg(args: list[str], timeout: float = 3600) -> subprocess.CompletedProcess:
    return subprocess.run([ffmpeg_bin(), "-hide_banner", "-nostdin", *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, **NO_WINDOW)


def _shot_bounds(path: str, duration: float, threshold: float, min_shot: float) -> list[tuple[float, float]]:
    from .analysis import detect_path

    cuts = sorted(ev["ptsSeconds"] for ev in detect_path(path, "scene", scene_threshold=threshold).get("events", []))
    bounds, start = [], 0.0
    for t in cuts:
        if t - start >= min_shot and duration - t >= min_shot:
            bounds.append((start, t))
            start = t
    bounds.append((start, duration))
    return bounds


def _keyframe(path: str, at: float, out: Path) -> bool:
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = _ffmpeg(["-y", "-ss", f"{max(0.0, at):.3f}", "-i", path, "-frames:v", "1", "-vf", "scale=320:-2", "-q:v", "4", str(out)], 120)
    return proc.returncode == 0 and out.exists()


def _frame_stats(img_path: Path) -> tuple[float, float]:
    """(mean luma 0-255, mean edge strength) of a keyframe."""
    from PIL import Image, ImageFilter, ImageStat

    with Image.open(img_path) as im:
        gray = im.convert("L")
        luma = ImageStat.Stat(gray).mean[0]
        edges = ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES)).mean[0]
    return round(luma, 1), round(edges, 1)


def _silences(path: str, silence_db: float) -> list[tuple[float, float]]:
    from .analysis import detect_path

    out = detect_path(path, "silence", silence_db=silence_db, min_silence=0.3)
    return [(iv["startSeconds"], iv["endSeconds"]) for iv in out.get("intervals", [])]


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


_WHISPER: dict[str, Any] = {}


def whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


def transcribe(path: str, model: str = "small", language: str | None = None) -> dict:
    """Timestamped transcript with faster-whisper (local; downloads the model on first use)."""
    from faster_whisper import WhisperModel

    if model not in _WHISPER:
        _WHISPER[model] = WhisperModel(model, device="auto", compute_type="auto")
    segments, info = _WHISPER[model].transcribe(path, language=language, vad_filter=True)
    segs = [{"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()} for s in segments if s.text.strip()]
    return {"language": info.language, "segments": segs}


def analyze_file(root: Path, rel: str, *, scene_threshold: float = 0.3, min_shot: float = 1.0, silence_db: float = -40.0,
                 do_transcribe: bool = False, whisper_model: str = "small", language: str | None = None) -> dict:
    path = root / rel
    try:
        info = probe(str(path))
    except ProbeError as exc:
        return {"rel": rel, "error": str(exc)}
    fid = file_id(rel)
    duration = info["duration"] / TPS
    entry: dict[str, Any] = {
        "id": fid, "rel": rel, "kind": info["kind"], "duration": round(duration, 3),
        "size": path.stat().st_size, "mtime": path.stat().st_mtime,
        "video": info["video"], "audio": info["audio"], "shots": [], "transcript": None, "notes": "",
    }
    frames = index_dir(root) / "frames"
    if info["kind"] == "image":
        kf = frames / f"{fid}-0.jpg"
        ok = _keyframe(str(path), 0, kf)
        luma, sharp = _frame_stats(kf) if ok else (None, None)
        entry["shots"] = [{"id": f"{fid}-0", "start": 0.0, "end": 0.0, "keyframe": kf.name if ok else None,
                           "luma": luma, "sharpness": sharp, "sound": 0.0, "desc": "", "tags": []}]
        return entry
    silences = _silences(str(path), silence_db) if info["audio"] else []
    bounds = _shot_bounds(str(path), duration, scene_threshold, min_shot) if info["video"] else [(0.0, duration)]
    for n, (s, e) in enumerate(bounds):
        shot: dict[str, Any] = {"id": f"{fid}-{n}", "start": round(s, 3), "end": round(e, 3), "keyframe": None,
                                "luma": None, "sharpness": None, "desc": "", "tags": []}
        if info["video"]:
            kf = frames / f"{fid}-{n}.jpg"
            if _keyframe(str(path), s + (e - s) / 2, kf):
                shot["keyframe"] = kf.name
                shot["luma"], shot["sharpness"] = _frame_stats(kf)
        if info["audio"] and e > s:
            silent = sum(_overlap(s, e, a, b) for a, b in silences)
            shot["sound"] = round(1 - silent / (e - s), 2)
        else:
            shot["sound"] = 0.0
        entry["shots"].append(shot)
    if do_transcribe and info["audio"]:
        entry["transcript"] = transcribe(str(path), whisper_model, language)
    return entry


# ---------------------------------------------------------------- index / search

def index(root: str | Path, *, force: bool = False, transcribe_mode: str = "auto", whisper_model: str = "small",
          language: str | None = None, scene_threshold: float = 0.3, min_shot: float = 1.0, silence_db: float = -40.0,
          progress: Progress | None = None) -> dict:
    """Index (or refresh) every media file under ``root``. Returns counts and per-file errors."""
    root = Path(root).resolve()
    if not root.is_dir():
        raise CommandError("NOT_A_FOLDER", f"not a folder: {root}")
    if transcribe_mode not in ("auto", "on", "off"):
        raise CommandError("BAD_PARAMS", "transcribe is auto, on or off")
    if transcribe_mode == "on" and not whisper_available():
        raise CommandError("NO_WHISPER", "transcription needs faster-whisper: uv sync --extra transcribe")
    do_tr = transcribe_mode == "on" or (transcribe_mode == "auto" and whisper_available())
    try:
        data = load(root)
    except CommandError:
        data = {"version": VERSION, "files": {}}
    old = data.get("files", {})
    files: dict[str, dict] = {}
    found = sorted(p for p in root.rglob("*") if p.is_file() and ".lumina" not in p.relative_to(root).parts
                   and not any(part.startswith(".") for part in p.relative_to(root).parts) and media_kind(p))
    done = skipped = 0
    errors = []
    for i, p in enumerate(found):
        rel = p.relative_to(root).as_posix()
        st = p.stat()
        prev = old.get(rel)
        fresh = prev and not prev.get("error") and prev.get("size") == st.st_size and abs(prev.get("mtime", 0) - st.st_mtime) < 1e-3
        if fresh and not force and not (do_tr and prev.get("audio") and prev.get("transcript") is None):
            files[rel] = prev
            skipped += 1
            continue
        if progress:
            progress(f"[{i + 1}/{len(found)}] {rel}")
        entry = analyze_file(root, rel, scene_threshold=scene_threshold, min_shot=min_shot, silence_db=silence_db,
                             do_transcribe=do_tr, whisper_model=whisper_model, language=language)
        if prev and not entry.get("error"):  # keep annotations of shots that still exist
            entry["notes"] = prev.get("notes", "")
            prev_shots = {(s["start"], s["end"]): s for s in prev.get("shots", [])}
            for s in entry["shots"]:
                old_s = prev_shots.get((s["start"], s["end"]))
                if old_s:
                    s["desc"], s["tags"] = old_s.get("desc", ""), old_s.get("tags", [])
        if entry.get("error"):
            errors.append({"file": rel, "error": entry["error"]})
        files[rel] = entry
        done += 1
    data = {"version": VERSION, "indexed_at": time.time(), "transcribed": do_tr, "files": files}
    save(root, data)
    shots = sum(len(f.get("shots", [])) for f in files.values())
    return {"root": str(root), "files": len(files), "analyzed": done, "unchanged": skipped, "shots": shots,
            "transcribed": do_tr, "errors": errors, "index": str(index_path(root))}


def iter_shots(data: dict):
    for f in data["files"].values():
        if f.get("error"):
            continue
        for s in f.get("shots", []):
            yield f, s


def find_shot(data: dict, shot_id: str) -> tuple[dict, dict]:
    for f, s in iter_shots(data):
        if s["id"] == shot_id:
            return f, s
    raise CommandError("NO_SHOT", f"shot not found: {shot_id}")


def shot_transcript(f: dict, s: dict) -> str:
    tr = f.get("transcript") or {}
    end = s["end"] if s["end"] > s["start"] else f["duration"]
    return " ".join(seg["text"] for seg in tr.get("segments", []) if _overlap(seg["start"], seg["end"], s["start"], end) > 0)


def shot_flags(s: dict) -> list[str]:
    flags = []
    if s.get("luma") is not None and s["luma"] < DARK_LUMA:
        flags.append("dark")
    if s.get("sharpness") is not None and s["sharpness"] < SOFT_SHARPNESS:
        flags.append("soft")
    if s.get("sound", 0) >= 0.5:
        flags.append("speech/sound")
    return flags


def shot_summary(f: dict, s: dict, with_transcript: bool = True) -> dict:
    out = {"shot": s["id"], "file": f["rel"], "kind": f["kind"], "start": s["start"], "end": s["end"],
           "duration": round((s["end"] - s["start"]) if f["kind"] != "image" else 0, 3),
           "flags": shot_flags(s), "desc": s.get("desc", ""), "tags": s.get("tags", [])}
    if with_transcript and f.get("transcript"):
        out["transcript"] = shot_transcript(f, s)
    return out


def _tokens(text: str) -> list[str]:
    return [t for t in "".join(c.lower() if c.isalnum() else " " for c in text).split() if t]


def search(root: str | Path, query: str, *, kind: str | None = None, min_duration: float = 0.0, limit: int = 20,
           include_flagged: bool = True) -> dict:
    """Shots whose description, tags, transcript, file name or folder match the query words (any language)."""
    data = load(root)
    words = _tokens(query)
    hits = []
    for f, s in iter_shots(data):
        if kind and f["kind"] != kind:
            continue
        if f["kind"] != "image" and s["end"] - s["start"] < min_duration:
            continue
        if not include_flagged and {"dark", "soft"} & set(shot_flags(s)):
            continue
        fields = [(s.get("desc", ""), 3.0), (" ".join(s.get("tags", [])), 3.0), (shot_transcript(f, s), 2.0),
                  (f["rel"], 1.0), (f.get("notes", ""), 1.0)]
        score = 0.0
        for text, weight in fields:
            low = text.lower()
            toks = set(_tokens(text))
            for w in words:
                if w in toks:
                    score += weight
                elif w in low:  # substring: Korean particles, partial words
                    score += weight * 0.6
        if not words:
            score = 1.0
        if score > 0:
            hits.append((score, f, s))
    hits.sort(key=lambda h: (-h[0], h[1]["rel"], h[2]["start"]))
    return {"query": query, "total": len(hits),
            "results": [{**shot_summary(f, s), "score": round(sc, 2)} for sc, f, s in hits[:limit]]}


def overview(root: str | Path, with_transcript: bool = True) -> dict:
    """Every file and shot in compact form (what a planner needs to choose sources)."""
    data = load(root)
    files = []
    for f in data["files"].values():
        if f.get("error"):
            continue
        v = f.get("video") or {}
        files.append({
            "file": f["rel"], "kind": f["kind"], "duration": f["duration"],
            "size": f"{v['width']}x{v['height']}" if v else None,
            "fps": round(v["fps_num"] / v["fps_den"], 3) if v else None, "notes": f.get("notes", ""),
            "shots": [shot_summary(f, s, with_transcript) for s in f.get("shots", [])],
            "transcript": (f.get("transcript") or {}).get("segments") if with_transcript else None,
        })
    return {"root": data["root"], "transcribed": data.get("transcribed", False), "files": files}


def overview_text(root: str | Path) -> str:
    """The overview as plain lines: shot id | time range | flags | description | transcript."""
    ov = overview(root)
    lines = []
    for f in ov["files"]:
        head = f"## {f['file']}  ({f['kind']}, {f['duration']:.1f}s"
        head += f", {f['size']} @ {f['fps']}fps)" if f["size"] else ")"
        lines.append(head)
        if f["notes"]:
            lines.append(f"notes: {f['notes']}")
        for s in f["shots"]:
            rng = "still" if f["kind"] == "image" else f"{s['start']:.2f}-{s['end']:.2f}s ({s['duration']:.1f}s)"
            parts = [s["shot"], rng]
            if s["flags"]:
                parts.append(",".join(s["flags"]))
            if s["desc"]:
                parts.append(s["desc"])
            if s["tags"]:
                parts.append("#" + " #".join(s["tags"]))
            lines.append("- " + " | ".join(parts))
        for seg in f["transcript"] or []:
            lines.append(f"  [{seg['start']:.2f}-{seg['end']:.2f}] {seg['text']}")
        lines.append("")
    return "\n".join(lines)


def annotate(root: str | Path, notes: list[dict]) -> dict:
    """Write shot descriptions/tags: [{shot, desc?, tags?}] or file notes: [{file, notes}]."""
    data = load(root)
    by_id = {s["id"]: s for _f, s in iter_shots(data)}
    updated, missing = 0, []
    for n in notes:
        if n.get("shot"):
            s = by_id.get(n["shot"])
            if s is None:
                missing.append(n["shot"])
                continue
            if n.get("desc") is not None:
                s["desc"] = n["desc"].strip()
            if n.get("tags") is not None:
                s["tags"] = [t.strip().lstrip("#") for t in n["tags"] if t.strip()]
            updated += 1
        elif n.get("file"):
            f = data["files"].get(n["file"])
            if f is None:
                missing.append(n["file"])
                continue
            f["notes"] = n.get("notes", "")
            updated += 1
    data.pop("root", None)
    save(root, data)
    return {"updated": updated, "missing": missing}


def contact_sheet(root: str | Path, file: str | None = None, shot_ids: list[str] | None = None, columns: int = 6,
                  out: str | None = None) -> dict:
    """One JPEG grid of shot keyframes, each labelled with its shot id: look at a whole file (or a list of
    shots) in a single image, then annotate the shots."""
    from PIL import Image, ImageDraw

    data = load(root)
    frames = index_dir(root) / "frames"
    picks = [(f, s) for f, s in iter_shots(data) if (file is None or f["rel"] == file) and (not shot_ids or s["id"] in shot_ids)
             and s.get("keyframe")]
    if not picks:
        raise CommandError("NO_SHOTS", "no shots with keyframes match")
    picks = picks[:60]
    tw, th, label = 320, 180, 18
    cols = max(1, min(columns, len(picks)))
    rows = (len(picks) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tw, rows * (th + label)), (16, 16, 16))
    draw = ImageDraw.Draw(sheet)
    for i, (f, s) in enumerate(picks):
        x, y = (i % cols) * tw, (i // cols) * (th + label)
        with Image.open(frames / s["keyframe"]) as im:
            im = im.convert("RGB")
            im.thumbnail((tw, th))
            sheet.paste(im, (x + (tw - im.width) // 2, y + (th - im.height) // 2))
        rng = "" if f["kind"] == "image" else f" {s['start']:.1f}-{s['end']:.1f}s"
        draw.text((x + 4, y + th + 2), f"{s['id']}{rng}", fill=(235, 235, 235))
    path = Path(out) if out else index_dir(root) / "sheets" / f"{(file and file_id(file)) or 'shots'}.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path, "JPEG", quality=82)
    return {"path": str(path), "shots": [s["id"] for _f, s in picks], "width": sheet.width, "height": sheet.height}
