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

from . import catalog, db
from ..timebase import TICKS_PER_SECOND

S = TICKS_PER_SECOND
THUMB_ROOT = Path.home() / ".lumina" / "thumbs"
THUMB_SIZE = 512

VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif"}
SKIP_DIRS = {"Adobe Premiere Pro Auto-Save", "Adobe Premiere Pro Audio Previews", "Adobe Premiere Pro Video Previews"}

PROBE_V = "probe@1"
CUTS_V = "cuts@2"
FILENAME_V = "filename@2"

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

def stream_timing(path: Path) -> tuple[Fraction, Fraction, bool]:
    """(first frame's pts in seconds, nominal fps, constant frame rate?).

    Screen recordings are often variable-rate: r_frame_rate says 59.94 while
    frames arrive at 26. Frame numbers mean nothing there, so cut edges are
    timestamps; on a constant-rate stream they snap to its frame grid."""
    st = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=r_frame_rate,avg_frame_rate,start_time", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout)["streams"][0]
    r = Fraction(st.get("r_frame_rate") or "0/1")
    avg = Fraction(st.get("avg_frame_rate") or "0/1")
    start = Fraction(st.get("start_time") or "0").limit_denominator(1_000_000)
    cfr = r > 0 and avg > 0 and abs(avg - r) / r < Fraction(1, 200)
    return start, r, cfr


def scene_times(path: Path, start: Fraction) -> list[Fraction]:
    """Seconds (from the first frame) where a new shot starts."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-an", "-sn",
         "-vf", f"select='gt(scene,{SCENE_THRESHOLD})',showinfo", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    times = {Fraction(m.group(1)).limit_denominator(1_000_000) - start
             for m in re.finditer(r"pts_time:([0-9.]+)", proc.stderr)}
    return sorted(t for t in times if t > 0)


def to_ticks(seconds: Fraction, fps: Fraction, cfr: bool) -> int:
    """Media time in ticks, on the frame grid when the stream has one."""
    if cfr:
        try:
            return frame_ticks(round(seconds * fps), fps)
        except ValueError:
            pass
    return round(seconds * S)


def plan_cuts(edges: list[int], total: int) -> list[tuple[int, int]]:
    """[start, end) tick ranges. Too-short shots merge into the one before."""
    edges = [0, *[e for e in edges if 0 < e < total], total]
    min_len = round(MIN_CUT_SECONDS * S)
    ranges: list[list[int]] = []
    for a, b in zip(edges, edges[1:]):
        if ranges and (b - a < min_len or ranges[-1][1] - ranges[-1][0] < min_len):
            ranges[-1][1] = b
        else:
            ranges.append([a, b])
    return [(a, b) for a, b in ranges if b > a]


def thumb_path(sha256: str, t_ticks: int) -> Path:
    return THUMB_ROOT / sha256[:2] / f"{sha256}_{t_ticks}.jpg"


def extract_frame(path: Path, seconds: Fraction, out: Path) -> None:
    """The frame shown at `seconds` (input seeking decodes up to it exactly)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{float(seconds):.6f}", "-i", str(path),
         "-an", "-sn", "-frames:v", "1",
         "-vf", f"scale='if(gt(iw,ih),{THUMB_SIZE},-2)':'if(gt(iw,ih),-2,{THUMB_SIZE})'", "-q:v", "3", str(out)],
        check=True,
    )
    if not out.exists():
        raise RuntimeError(f"no frame at {float(seconds):.3f}s")


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
        start, fps, cfr = stream_timing(path)
        edges = [to_ticks(t, fps, cfr) for t in scene_times(path, start)]
        ranges = plan_cuts(edges, m["duration_ticks"])
        cuts = []
        for a, b in ranges:
            k = to_ticks(Fraction(a + b, 2 * S), fps, cfr)
            cuts.append({"start_ticks": a, "end_ticks": b, "keyframe_ticks": min(max(k, a), b - 1)})
        thumbs = [thumb_path(m["sha256"], c["keyframe_ticks"]) for c in cuts]
        for c, t in zip(cuts, thumbs):
            extract_frame(path, start + Fraction(c["keyframe_ticks"], S), t)
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


def filename_facts(con, path: Path, root: Path, folder_work: Optional[int]) -> list[dict]:
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
        # A subfolder named after one of the work's characters (봇치 더 록/키타
        # 이쿠요). Only a catalog match counts: subfolders are also named 효과음,
        # 레퍼런스, 2화 ...
        ref = con.execute("SELECT catalog_ref FROM entity WHERE entity_id = ?", (folder_work,)).fetchone()[0]
        ip = catalog.catalog().by_ref(ref) if ref else None
        for part in path.relative_to(root).parts[:-1]:
            m = catalog.catalog().resolve("character", [part], ip.catalog_id if ip else None)
            if m and ip and m.ip_id == ip.catalog_id:
                eid = catalog.resolve_entity(con, "character", [part], ip_ref=ref)
                rows.append({"kind": "character", "ref_id": eid, "status": "proposed", "basis": "subfolder name"})
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


def _names_in(names: list[str], text: str) -> bool:
    """Whether text spells one of the names. Short Latin keys ('fern') are
    too common inside other words to count."""
    t = catalog.key(text)
    for n in names:
        k = catalog.key(n)
        if len(k) >= (4 if k.isascii() else 2) and k in t:
            return True
    return False


def verify(con, media_id: int) -> int:
    """Accept a proposed fact when an independent source says the same.

    Independent means another producer that did not see this one's guess
    (the folder and Claude, the tagger and Claude), the file's own title, or
    the person's own words: the captions the span was used under. One machine alone never makes a fact
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
    titles = [r[0] for r in con.execute(
        "SELECT value FROM fact WHERE media_id = ? AND kind = 'title' AND status = 'accepted'", (media_id,))]
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
        if f["fact_id"] in accept or f["ref_id"] is None:
            continue
        names = [f["name"], *[a for a in (f["aliases"] or "").split("\n") if a]]
        if any(_names_in(names, t) for t in titles):
            accept[f["fact_id"]] = "named in the file title"
            continue
        for u in usage:
            if db._overlaps(f["t0_ticks"], f["t1_ticks"], u["src_in_ticks"], u["src_out_ticks"]) \
                    and _names_in(names, u["caption"]):
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
    """Every picture and recording under root. The folder's name proposes
    the work (resolved through the catalog) unless `work` is given."""
    if work is None:
        work = catalog.resolve_entity(con, "work", [root.name])
        con.commit()  # a failing file rolls back; the work must survive it
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
                db.put_facts(con, media_id, FILENAME_V, filename_facts(con, path, root, work))
                db.job_done(con, media_id, "filename", FILENAME_V)
            con.commit()
            n = con.execute("SELECT count(*) FROM cut WHERE media_id = ?", (media_id,)).fetchone()[0]
            log(f"{media_id:5d} {kind:5s} {n:4d} cuts  {path.name[:70]}")
            ids.append(media_id)
        except Exception as e:  # one bad file must not stop a folder
            con.rollback()
            log(f"  FAILED {path.name}: {e}")
    return ids
