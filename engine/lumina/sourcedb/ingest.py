"""Filling the source DB without spending AI tokens.

Stages, each recorded in `job` with a version so a rerun skips what is done:
  probe    - ffprobe: size, duration, frame rate
  cuts     - scene cuts on exact frames, a keyframe per cut, its thumbnail,
             pHash and luminance
  filename - what a file name says: yt-dlp's [uploader] title (id), the
             project folder's work (proposed only - folders hold other works'
             evidence too)
and usage, imported from the edits themselves.

GPU analyzers (tagger, embeddings, speech) run in the separate analyzer
environment (engine/analyzers) and write through db.py the same way.
"""

from __future__ import annotations

import json
import re
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
from PIL import Image

from . import db
from ..timebase import TICKS_PER_SECOND

S = TICKS_PER_SECOND
THUMB_ROOT = Path.home() / ".lumina" / "thumbs"
THUMB_SIZE = 512

VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif"}
SKIP_DIRS = {"Adobe Premiere Pro Auto-Save", "Adobe Premiere Pro Audio Previews", "Adobe Premiere Pro Video Previews"}

PROBE_V = "probe@1"
CUTS_V = "cuts@1"
FILENAME_V = "filename@1"

SCENE_THRESHOLD = 0.3
MIN_CUT_SECONDS = 0.25  # a shorter "shot" is a flash or a detector stutter


# ---------------------------------------------------------------------------
# time
# ---------------------------------------------------------------------------

def frame_ticks(frame: int, fps: Fraction) -> int:
    """Exact for every tick-exact rate; rounded to the nearest tick otherwise."""
    return round(Fraction(frame) * S / fps)


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------

def own_exports(folder: Path) -> set[str]:
    """Names of the folder's own renders: a project's or a sequence's name.
    A render of an edit is not footage; offering it would put an old edit
    back into a new one."""
    names = set()
    for proj in list(folder.glob("*.lumina")) + list(folder.glob("*.prproj")):
        names.add(proj.stem)
        if proj.suffix == ".lumina":
            try:
                doc = json.loads(proj.read_text(encoding="utf-8"))["document"]
                names.update(s["name"] for s in doc.get("sequences", {}).values())
            except (OSError, ValueError, KeyError):
                pass
    return names


def discover(root: Path) -> list[tuple[Path, str]]:
    out = []
    for folder in [root, *[p for p in root.rglob("*") if p.is_dir()]]:
        if any(part in SKIP_DIRS for part in folder.parts):
            continue
        exports = own_exports(folder)
        for p in sorted(folder.iterdir()):
            if not p.is_file():
                continue
            ext = p.suffix.lower()
            if ext in VIDEO_EXT:
                if any(p.stem == n or p.stem.startswith(n + " (") for n in exports):
                    continue
                out.append((p, "video"))
            elif ext in IMAGE_EXT:
                out.append((p, "image"))
    return out


# ---------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------

def probe(path: Path, kind: str) -> dict:
    info = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,r_frame_rate,avg_frame_rate,nb_frames:format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout)
    st = (info.get("streams") or [{}])[0]
    out = {"width": st.get("width"), "height": st.get("height")}
    if kind == "video":
        fps = Fraction(st.get("r_frame_rate") or st.get("avg_frame_rate") or "30/1")
        out["fps_num"], out["fps_den"] = fps.numerator, fps.denominator
        dur = float(info.get("format", {}).get("duration") or 0)
        out["duration_ticks"] = round(Fraction(dur).limit_denominator(1_000_000) * S)
    return out


# ---------------------------------------------------------------------------
# cuts
# ---------------------------------------------------------------------------

def scene_frames(path: Path, fps: Fraction) -> list[int]:
    """Frame numbers where a new shot starts (excluding 0)."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-an", "-sn",
         "-vf", f"select='gt(scene,{SCENE_THRESHOLD})',showinfo", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    frames = []
    for m in re.finditer(r"pts_time:([0-9.]+)", proc.stderr):
        frames.append(round(Fraction(m.group(1)) * fps))
    return sorted(set(f for f in frames if f > 0))


def plan_cuts(starts: list[int], total_frames: int, fps: Fraction) -> list[tuple[int, int]]:
    """[start, end) frame ranges. Too-short shots merge into the one before."""
    edges = [0, *[f for f in starts if 0 < f < total_frames], total_frames]
    min_frames = max(1, round(MIN_CUT_SECONDS * fps))
    ranges: list[list[int]] = []
    for a, b in zip(edges, edges[1:]):
        if ranges and b - a < min_frames:
            ranges[-1][1] = b
        elif ranges and ranges[-1][1] - ranges[-1][0] < min_frames:
            ranges[-1][1] = b
        else:
            ranges.append([a, b])
    return [(a, b) for a, b in ranges if b > a]


def thumb_path(sha256: str, t_ticks: int) -> Path:
    return THUMB_ROOT / sha256[:2] / f"{sha256}_{t_ticks}.jpg"


FRAMES_PER_PASS = 30  # ffmpeg's expression parser fails on much longer select expressions


def extract_frames(path: Path, frames: list[int], out: list[Path]) -> None:
    """Exact frames by number, a decode pass per FRAMES_PER_PASS frames."""
    for i in range(0, len(frames), FRAMES_PER_PASS):
        _extract_pass(path, frames[i:i + FRAMES_PER_PASS], out[i:i + FRAMES_PER_PASS])


def _extract_pass(path: Path, frames: list[int], out: list[Path]) -> None:
    expr = "+".join(f"eq(n\\,{f})" for f in frames)
    tmp = out[0].parent / f"_{out[0].stem}_%05d.jpg"
    out[0].parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(path), "-an", "-sn",
         "-vf", f"select='{expr}',scale='if(gt(iw,ih),{THUMB_SIZE},-2)':'if(gt(iw,ih),-2,{THUMB_SIZE})'",
         "-fps_mode", "passthrough", "-q:v", "3", str(tmp)],
        check=True,
    )
    made = sorted(tmp.parent.glob(f"_{out[0].stem}_*.jpg"))
    if len(made) != len(frames):
        for m in made:
            m.unlink()
        raise RuntimeError(f"asked for {len(frames)} frames, got {len(made)}")
    for src, dst in zip(made, out):
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.replace(dst)


def image_thumb(path: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(path) as im:
        im.seek(0)
        im = im.convert("RGB")
        im.thumbnail((THUMB_SIZE, THUMB_SIZE))
        im.save(dst, quality=90)


_DCT = np.cos(np.pi * (2 * np.arange(32)[None, :] + 1) * np.arange(32)[:, None] / 64)


def phash(img: Image.Image) -> int:
    """64-bit DCT hash: low frequencies above their median."""
    g = np.asarray(img.convert("L").resize((32, 32), Image.Resampling.LANCZOS), dtype=np.float64)
    low = (_DCT @ g @ _DCT.T)[:8, :8].flatten()
    bits = low > np.median(low[1:])
    return int("".join("1" if b else "0" for b in bits), 2)


def luma(img: Image.Image) -> float:
    return float(np.asarray(img.convert("L"), dtype=np.float32).mean() / 255.0)


def run_cuts(con, media_id: int, path: Path) -> None:
    m = con.execute("SELECT * FROM media WHERE media_id = ?", (media_id,)).fetchone()
    measures = []
    if m["kind"] == "image":
        t = thumb_path(m["sha256"], 0)
        image_thumb(path, t)
        cuts = [{"start_ticks": 0, "end_ticks": 0, "keyframe_ticks": 0}]
        thumbs = [t]
    else:
        fps = Fraction(m["fps_num"], m["fps_den"])
        total = int(Fraction(m["duration_ticks"]) * fps / S)
        ranges = plan_cuts(scene_frames(path, fps), total, fps)
        keys = [(a + b) // 2 for a, b in ranges]
        cuts = [
            {"start_ticks": frame_ticks(a, fps), "end_ticks": frame_ticks(b, fps), "keyframe_ticks": frame_ticks(k, fps)}
            for (a, b), k in zip(ranges, keys)
        ]
        thumbs = [thumb_path(m["sha256"], c["keyframe_ticks"]) for c in cuts]
        extract_frames(path, keys, thumbs)
    for c, t in zip(cuts, thumbs):
        with Image.open(t) as im:
            c["phash"] = phash(im)
            span = {} if m["kind"] == "image" else {"t0_ticks": c["start_ticks"], "t1_ticks": c["end_ticks"]}
            measures.append({"kind": "luma", "num": round(luma(im), 4), "status": "accepted",
                             "basis": "keyframe mean", **span})
    db.set_cuts(con, media_id, cuts)
    db.put_facts(con, media_id, CUTS_V, measures)


# ---------------------------------------------------------------------------
# what file names say
# ---------------------------------------------------------------------------

_YTDLP = re.compile(r"^\[(?P<uploader>[^\]]+)\]\s*(?P<title>.+?)\s*\((?P<id>[A-Za-z0-9_-]{11})\)$")


def filename_facts(con, path: Path, folder_work: Optional[int]) -> list[dict]:
    rows = []
    m = _YTDLP.match(path.stem)
    if m:
        rows += [
            {"kind": "credit", "value": m["uploader"], "status": "accepted", "basis": "yt-dlp file name"},
            {"kind": "title", "value": m["title"], "status": "accepted", "basis": "yt-dlp file name"},
            {"kind": "origin_url", "value": f"https://youtu.be/{m['id']}", "status": "accepted", "basis": "yt-dlp file name"},
        ]
    elif not re.fullmatch(r"[A-Za-z0-9_-]{8,}", path.stem):  # a human-written name, not a hash
        rows.append({"kind": "title", "value": path.stem, "status": "accepted", "basis": "file name"})
    if folder_work is not None:
        rows.append({"kind": "work", "ref_id": folder_work, "status": "proposed", "basis": "project folder"})
    return rows


# ---------------------------------------------------------------------------
# usage
# ---------------------------------------------------------------------------

def usage_from_lumina(con, project_path: Path, *, sequences: Optional[Iterable[str]] = None,
                      approved: Iterable[str] = ()) -> int:
    """Every picture placed on a video track, with the words on screen over it.

    The words are caption cues and graphic text overlapping the clip. The
    Shorts title and any graphic spanning most of the sequence (a frame, a
    logo) say nothing about one shot and are left out.
    """
    from ..commands.shorts import TITLE_CLIP
    from ..model import Project

    doc = json.loads(project_path.read_text(encoding="utf-8"))["document"]
    p = Project.model_validate(doc)
    approved = set(approved)
    rows = []
    for s in p.sequences.values():
        if sequences is not None and s.name not in set(sequences):
            continue
        clips = [(t, c) for t in s.tracks.values() for c in t.clips.values()]
        length = max((c.end for _, c in clips), default=0)
        words = []
        for t, c in clips:
            text = None
            if c.caption:
                text = c.caption.text
            elif c.graphic and c.name != TITLE_CLIP and (c.end - c.start) < 0.5 * length:
                text = " ".join(l.text for l in c.graphic.layers if getattr(l, "text", None))
            if text and text.strip():
                words.append((c.start, c.end, " ".join(text.split())))
        words.sort()
        for t, c in clips:
            item = p.media.get(c.media_id) if c.media_id else None
            if t.kind != "video" or item is None or item.kind not in ("video", "image") or not c.enabled:
                continue
            try:
                media_id = db.media_id_for_path(con, item.path)
            except OSError:
                media_id = None
            if media_id is None:
                continue
            dur = c.end - c.start
            src_in = c.freeze if c.freeze is not None else c.source_in
            src_out = src_in if c.freeze is not None else src_in + round(dur * c.speed)
            if item.kind == "image":
                src_in = src_out = 0
            caption = " / ".join(w for a, b, w in words if a < c.end and b > c.start)
            props = {}
            if c.keyframes:
                props["keyframes"] = {k: [kf.model_dump(exclude_none=True) for kf in v] for k, v in c.keyframes.items()}
            mo = c.motion
            crop = [mo.crop_left, mo.crop_top, mo.crop_right, mo.crop_bottom]
            if any(crop):
                props["crop"] = crop
            if mo.rotation:
                props["rotation"] = mo.rotation
            if c.freeze is not None:
                props["freeze"] = True
            rows.append({
                "media_id": media_id, "sequence": s.name, "track": t.name,
                "src_in_ticks": src_in, "src_out_ticks": src_out, "rec_in_ticks": c.start,
                "speed": c.speed, "scale": mo.scale,
                "pos_x": mo.position[0] if mo.position else None,
                "pos_y": mo.position[1] if mo.position else None,
                "caption": caption or None, "props": json.dumps(props, ensure_ascii=False) if props else None,
                "approved": s.name in approved,
            })
    return db.replace_usage(con, str(project_path.resolve()), rows)


# ---------------------------------------------------------------------------
# agreement
# ---------------------------------------------------------------------------

AGREE_KINDS = ("work", "character", "source_type", "variant")


def _family(producer: str) -> str:
    return producer.split("@")[0]


def verify(con, media_id: int) -> int:
    """Accept a proposed fact when an independent source says the same.

    Independent means another producer (the folder and Claude, the tagger
    and Claude), or the person's own words: a character named in the
    captions the span was used under. One machine alone never makes a fact
    accepted. Returns the number of facts accepted.
    """
    facts = con.execute(
        f"SELECT f.fact_id, f.kind, f.value, f.ref_id, f.t0_ticks, f.t1_ticks, f.producer, f.status, "
        f"e.name, e.aliases FROM fact f LEFT JOIN entity e ON e.entity_id = f.ref_id "
        f"WHERE f.media_id = ? AND f.kind IN ({','.join('?' * len(AGREE_KINDS))}) AND f.status != 'rejected'",
        (media_id, *AGREE_KINDS),
    ).fetchall()
    usage = con.execute(
        "SELECT src_in_ticks, src_out_ticks, caption FROM usage WHERE media_id = ? AND caption IS NOT NULL",
        (media_id,),
    ).fetchall()
    accept: dict[int, str] = {}
    for f in facts:
        if f["status"] != "proposed":
            continue
        key = (f["kind"], f["ref_id"] if f["ref_id"] is not None else f["value"])
        for g in facts:
            if g["fact_id"] == f["fact_id"] or _family(g["producer"]) == _family(f["producer"]):
                continue
            if (g["kind"], g["ref_id"] if g["ref_id"] is not None else g["value"]) != key:
                continue
            if db._overlaps(f["t0_ticks"], f["t1_ticks"], g["t0_ticks"], g["t1_ticks"]):
                accept[f["fact_id"]] = f"agrees with {g['producer']}"
                break
        if f["fact_id"] not in accept and f["kind"] == "character" and f["name"]:
            names = [f["name"], *[a for a in (f["aliases"] or "").split("\n") if a]]
            for u in usage:
                if db._overlaps(f["t0_ticks"], f["t1_ticks"], u["src_in_ticks"], u["src_out_ticks"]) \
                        and any(n in u["caption"] for n in names):
                    accept[f["fact_id"]] = "named in the caption it was used under"
                    break
    for fid, basis in accept.items():
        con.execute("UPDATE fact SET status = 'accepted', basis = ? WHERE fact_id = ?", (basis, fid))
    if accept:
        db.rebuild_docs(con, media_id)
    return len(accept)


# ---------------------------------------------------------------------------
# a folder, end to end (token-free stages)
# ---------------------------------------------------------------------------

def ingest_folder(con, root: Path, *, work: Optional[int] = None, log=print) -> list[int]:
    ids = []
    for path, kind in discover(root):
        try:
            existing = db.media_id_for_path(con, path)
            info = probe(path, kind) if existing is None else None
            media_id, _ = db.register_file(con, path, kind, probe=info)
            if db.job_version(con, media_id, "probe") != PROBE_V:
                if info is None:
                    info = probe(path, kind)
                con.execute(
                    "UPDATE media SET width = ?, height = ?, duration_ticks = ?, fps_num = ?, fps_den = ? WHERE media_id = ?",
                    (info.get("width"), info.get("height"), info.get("duration_ticks"),
                     info.get("fps_num"), info.get("fps_den"), media_id),
                )
                db.job_done(con, media_id, "probe", PROBE_V)
            if db.job_version(con, media_id, "cuts") != CUTS_V:
                run_cuts(con, media_id, path)
                db.job_done(con, media_id, "cuts", CUTS_V)
            if db.job_version(con, media_id, "filename") != FILENAME_V:
                db.put_facts(con, media_id, FILENAME_V, filename_facts(con, path, work))
                db.job_done(con, media_id, "filename", FILENAME_V)
            con.commit()
            n = con.execute("SELECT count(*) FROM cut WHERE media_id = ?", (media_id,)).fetchone()[0]
            log(f"{media_id:5d} {kind:5s} {n:4d} cuts  {path.name[:70]}")
            ids.append(media_id)
        except Exception as e:  # one bad file must not stop a folder
            con.rollback()
            log(f"  FAILED {path.name}: {e}")
    return ids
