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
import os
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

PROBE_V = "probe@2"
CUTS_V = "cuts@2"
FILENAME_V = "filename@4"

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
        try:
            if proj.suffix == ".lumina":
                doc = json.loads(proj.read_text(encoding="utf-8"))["document"]
                names.update(s["name"] for s in doc.get("sequences", {}).values())
            else:
                from ..io import prproj
                g = prproj.load(str(proj))
                names.update(n for e in g.root if e.tag == "Sequence" and (n := prproj._text(e, "Name")))
        except Exception:  # an unreadable project only means fewer names to exclude
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
         "stream=width,height,r_frame_rate,avg_frame_rate,nb_frames,duration:format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout)
    st = (info.get("streams") or [{}])[0]
    out = {"width": st.get("width"), "height": st.get("height")}
    if kind == "video":
        fps = Fraction(st.get("r_frame_rate") or st.get("avg_frame_rate") or "30/1")
        out["fps_num"], out["fps_den"] = fps.numerator, fps.denominator
        # The picture's own length: the file's can be longer when the audio
        # runs past the last frame, and a cut there would show nothing.
        dur = float(st.get("duration") or info.get("format", {}).get("duration") or 0)
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
         "-vf", f"scale=320:-2,select='gt(scene,{SCENE_THRESHOLD})',showinfo", "-f", "null", "-"],
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
         "-vf", f"scale='if(gt(iw,ih),{THUMB_SIZE},-2)':'if(gt(iw,ih),-2,{THUMB_SIZE})',format=yuvj420p",
         "-q:v", "3", str(out)],
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


def analyze_file(path: Path, kind: str, sha256: Optional[str] = None) -> dict:
    """Everything token-free about one file, without touching the DB, so it
    can run in a worker process: hash, probe, cuts, thumbnails, pHash, luma."""
    sha256 = sha256 or db.sha256_file(path)
    info = probe(path, kind)
    measures = []
    if kind == "image":
        t = thumb_path(sha256, 0)
        image_thumb(path, t)
        cuts = [{"start_ticks": 0, "end_ticks": 0, "keyframe_ticks": 0}]
        thumbs = [t]
    else:
        start, fps, cfr = stream_timing(path)
        edges = [to_ticks(t, fps, cfr) for t in scene_times(path, start)]
        cuts = []
        for a, b in plan_cuts(edges, info["duration_ticks"]):
            k = to_ticks(Fraction(a + b, 2 * S), fps, cfr)
            cuts.append({"start_ticks": a, "end_ticks": b, "keyframe_ticks": min(max(k, a), b - 1)})
        thumbs = [thumb_path(sha256, c["keyframe_ticks"]) for c in cuts]
        for c, t in zip(cuts, thumbs):
            if not t.exists():
                extract_frame(path, start + Fraction(c["keyframe_ticks"], S), t)
    for c, t in zip(cuts, thumbs):
        with Image.open(t) as im:
            c["phash"] = phash(im)
            span = {} if kind == "image" else {"t0_ticks": c["start_ticks"], "t1_ticks": c["end_ticks"]}
            measures.append({"kind": "luma", "num": round(luma(im), 4), "status": "accepted",
                             "basis": "keyframe mean", **span})
    return {"sha256": sha256, "probe": info, "cuts": cuts, "measures": measures}


def run_cuts(con, media_id: int, path: Path) -> None:
    m = con.execute("SELECT * FROM media WHERE media_id = ?", (media_id,)).fetchone()
    r = analyze_file(path, m["kind"], m["sha256"])
    db.set_cuts(con, media_id, r["cuts"])
    db.put_facts(con, media_id, CUTS_V, r["measures"])


def _analyze_job(args):
    path, kind = args
    try:
        return path, kind, analyze_file(path, kind), None
    except Exception as e:  # reported by the caller, which keeps going
        return path, kind, None, f"{type(e).__name__}: {e}"


def ingest_many(con, roots: list[Path], *, workers: int = 6, log=print) -> dict:
    """ingest_folder for many folders, with the expensive work (hashing,
    scene detection, keyframes) in `workers` processes. Only this process
    writes the DB. Files already done at the current versions are skipped
    without being hashed again."""
    from concurrent.futures import ProcessPoolExecutor, as_completed

    jobs, folder_work, root_of = [], {}, {}
    for root in roots:
        folder_work[root] = catalog.find_entity(con, "work", [root.name])
        for path, kind in discover(root):
            root_of[path] = root
            mid = db.media_id_for_path(con, path)
            st = path.stat()
            row = con.execute("SELECT size, mtime_ms FROM media_file WHERE path = ?", (str(path.resolve()),)).fetchone()
            unchanged = row and row["size"] == st.st_size and row["mtime_ms"] == int(st.st_mtime * 1000)
            if unchanged and db.job_version(con, mid, "cuts") == CUTS_V:
                if db.job_version(con, mid, "filename") != FILENAME_V:  # cheap: no need to re-analyse
                    db.put_facts(con, mid, FILENAME_V, filename_facts(con, path, root, folder_work[root]))
                    db.job_done(con, mid, "filename", FILENAME_V)
                continue
            jobs.append((path, kind))
    con.commit()
    log(f"{len(jobs)} files to analyse in {len(roots)} folders")
    done = failed = 0
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_analyze_job, j) for j in jobs]
        for fut in as_completed(futures):
            path, kind, r, err = fut.result()
            if err:
                failed += 1
                log(f"  FAILED {path}: {err}")
                continue
            try:
                media_id, _ = db.register_file(con, path, kind, probe=r["probe"], sha256=r["sha256"])
                p = r["probe"]
                con.execute(
                    "UPDATE media SET width = ?, height = ?, duration_ticks = ?, fps_num = ?, fps_den = ? WHERE media_id = ?",
                    (p.get("width"), p.get("height"), p.get("duration_ticks"), p.get("fps_num"), p.get("fps_den"), media_id))
                db.job_done(con, media_id, "probe", PROBE_V)
                if db.job_version(con, media_id, "cuts") != CUTS_V:
                    db.set_cuts(con, media_id, r["cuts"])
                    db.put_facts(con, media_id, CUTS_V, r["measures"])
                    db.job_done(con, media_id, "cuts", CUTS_V)
                db.put_facts(con, media_id, FILENAME_V,
                             filename_facts(con, path, root_of[path], folder_work[root_of[path]]))
                db.job_done(con, media_id, "filename", FILENAME_V)
                db.set_status(con, media_id, "live")
                con.commit()
                done += 1
                if done % 50 == 0:
                    log(f"  {done}/{len(jobs)} done, {failed} failed")
            except Exception as e:
                con.rollback()
                failed += 1
                log(f"  FAILED {path}: {type(e).__name__}: {e}")
    return {"files": len(jobs), "done": done, "failed": failed}


# ---------------------------------------------------------------------------
# what file names say
# ---------------------------------------------------------------------------

_YTDLP = re.compile(r"^\[(?P<uploader>[^\]]+)\]\s*(?P<title>.+?)\s*\((?P<id>[A-Za-z0-9_-]{11})\)$")


_FULLWIDTH = str.maketrans("０１２３４５６７８９", "0123456789")
_SEASON = re.compile(r"(?<!\d)(?P<s>\d{1,2})\s*기(?![가-힣])")
_EP_PATTERNS = (
    # S2 E04 0931 저 구름 고기만두  /  S1 E3 3253 ...
    re.compile(r"(?i)\bS(?P<s>\d{1,2})\s*E(?P<e>\d{1,3})(?:\s+(?P<t>\d{4}|\d{6})\b)?"),
    # Linkkf downloads: 귀멸의 칼날 3기 대장장이 마을 편 BD 😜 11 - Anime - Linkkf ...
    re.compile(r"(?P<pre>.*?)😜\s*(?P<e>\d{1,4})\s*-\s*Anime"),
    # player tab titles: JUJUTSU KAISEN Season 2 - Episode 16  /  Episode 7
    re.compile(r"(?i)(?:\bseason\s*(?P<s>\d{1,2})\W{0,6})?\bepisode\s*(?P<e>\d{1,4})\b"),
    # E01 0005 청산싫어  /  EP1 001256 재의 왕을 ...
    re.compile(r"(?i)(?<![A-Za-z])EP?\.?\s*(?P<e>\d{1,3})(?:\s+(?P<t>\d{4}|\d{6})\b)?(?![A-Za-z])"),
    # 리제로 4기 11화 고점  /  4기 8화 레이드  /  3화
    re.compile(r"(?:(?P<s>\d{1,2})\s*기\s*)?(?P<e>\d{1,3})\s*화(?![가-힣])"),
    # 第1話  /  第４話
    re.compile(r"第\s*(?P<e>\d{1,3})\s*話"),
)
# Not an episode, but the same kind of "where in the work": opening and
# ending numbers, a season or part alone, a game's version.
_OTHER = (
    (re.compile(r"(?i)(?<![A-Za-z0-9])(?P<k>opening|ending|op|ed)[\s_]*(?P<n>\d{1,2})(?!\d)"),
     lambda m: ("OP" if m["k"].lower().startswith("op") else "ED") + str(int(m["n"]))),
    (re.compile(r"(?<!\d)(?P<n>\d{1,2})\s*부(?![가-힣])"), lambda m: f"Part {int(m['n'])}"),
    (_SEASON, lambda m: f"S{int(m['s'])}"),
    (re.compile(r"(?<![\d.])(?P<v>\d\.\d)(?![\d.])(?=\s*(?:컷신|개척|버전|ver|PV|업데이트))", re.I), lambda m: f"v{m['v']}"),
)


def episode_of(name: str) -> Optional[tuple[str, Optional[int]]]:
    """('S2 E04', seconds into the episode) from the channel's clip names,
    or None. The time is the 4-digit mmss or 6-digit hhmmss after the
    episode: where the clip was cut from, to find the moment again. Without
    an episode, an opening/ending number, a season or part, or a game
    version is the label."""
    name = name.translate(_FULLWIDTH)
    for pat in _EP_PATTERNS:
        m = pat.search(name)
        if not m:
            continue
        d = m.groupdict()
        e = int(d["e"])
        if e == 0:
            continue
        season = d.get("s")
        if season is None and d.get("pre") is not None:
            sm = _SEASON.search(d["pre"])
            season = sm["s"] if sm else None
        if "극장판" in (d.get("pre") or ""):  # a film's download numbers its parts, not episodes
            return "Film", None
        label = (f"S{int(season)} " if season else "") + f"E{e:02d}"
        t, seconds = d.get("t"), None
        if t:
            parts = [int(t[i:i + 2]) for i in range(0, len(t), 2)]
            if all(p < 60 for p in parts[1:]):
                seconds = sum(p * 60 ** (len(parts) - 1 - i) for i, p in enumerate(parts))
        return label, seconds
    for pat, label in _OTHER:
        m = pat.search(name)
        if m:
            return label(m), None
    return None


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
    folders = path.relative_to(root).parts[:-1] if path.is_relative_to(root) else ()
    ep = episode_of(path.stem) or next((e for e in map(episode_of, reversed(folders)) if e), None)
    if ep:
        rows.append({"kind": "episode", "value": ep[0], "status": "accepted", "basis": "file name"})
        if ep[1] is not None:
            rows.append({"kind": "source_time", "num": ep[1], "status": "accepted", "basis": "file name"})
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
    doc = json.loads(project_path.read_text(encoding="utf-8"))["document"]
    return _usage_from_document(con, project_path, doc, sequences=sequences, approved=approved)


CHANNEL_ROOT = Path("D:/유튜브 채널")


def locate(path: str) -> str:
    """Where a project's media path is now. Projects made on another machine
    point at C:/Users/<them>/OneDrive/유튜브 채널/...; the same tree now lives
    at CHANNEL_ROOT, so the part after the channel folder's name is kept."""
    if os.path.exists(path):
        return path
    parts = Path(path).parts
    for i, part in enumerate(parts):
        if part == CHANNEL_ROOT.name:
            moved = CHANNEL_ROOT.joinpath(*parts[i + 1:])
            if moved.exists():
                return str(moved)
    return path


TEMPLATE_PATH = Path(__file__).resolve().parents[3] / "templates" / "caddynote.lumina"


def template_sequences() -> set[str]:
    """The channel template's own sequences (intro, frame, the empty Shorts
    sequence): every project has them, and their clips say nothing."""
    try:
        doc = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))["document"]
        return {s["name"] for s in doc["sequences"].values() if s["name"] != "시퀀스 02 (shorts)"}
    except (OSError, ValueError, KeyError):
        return set()


def rendered_names(*folders: Path) -> set[str]:
    """Stems of rendered videos: a sequence rendered under its own name was
    finished, which is the best available sign it was published."""
    out = set()
    for f in folders:
        if f.is_dir():
            out.update(p.stem for p in f.rglob("*") if p.suffix.lower() in VIDEO_EXT)
    return out


def usage_from_project(con, project_path: Path, *, rendered: set[str] = frozenset()) -> int:
    """Usage from a .lumina or a .prproj (opened through Lumina's own Premiere
    import, headless). Template sequences are skipped; a sequence counts as
    approved when a render of it exists."""
    if project_path.suffix.lower() == ".lumina":
        doc = json.loads(project_path.read_text(encoding="utf-8"))["document"]
    else:
        from ..session import Session

        sess = Session(services=None)
        sess.execute("project.import_premiere", {"path": str(project_path), "into_root": True}, record=False)
        doc = sess.doc
    skip = template_sequences()
    names = [s["name"] for s in doc["sequences"].values() if s["name"] not in skip]
    return _usage_from_document(con, project_path, doc, sequences=names,
                                approved=[n for n in names if n in rendered])


def _usage_from_document(con, project_path: Path, doc: dict, *, sequences: Optional[Iterable[str]] = None,
                         approved: Iterable[str] = ()) -> int:
    """Every picture placed on a video track, with the words on screen over it.

    The words are caption cues and graphic text overlapping the clip. The
    Shorts title and any graphic spanning most of the sequence (a frame, a
    logo) say nothing about one shot and are left out.
    """
    from ..commands.shorts import TITLE_CLIP
    from ..model import Project

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
                media_id = db.media_id_for_path(con, locate(item.path))
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
        work = catalog.find_entity(con, "work", [root.name])
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


def reprobe(con, log=print) -> int:
    """Bring media probed by an older probe version up to date without
    re-cutting: the new length clips a last cut that ran past it."""
    fixed = 0
    rows = con.execute("SELECT media_id, kind FROM media WHERE status != 'missing'").fetchall()
    for r in rows:
        if db.job_version(con, r["media_id"], "probe") == PROBE_V:
            continue
        path = db.media_path(con, r["media_id"])
        if path is None:
            continue
        try:
            info = probe(Path(path), r["kind"])
        except Exception as e:
            log(f"  FAILED {path}: {e}")
            continue
        con.execute("UPDATE media SET width = ?, height = ?, duration_ticks = ?, fps_num = ?, fps_den = ? WHERE media_id = ?",
                    (info.get("width"), info.get("height"), info.get("duration_ticks"),
                     info.get("fps_num"), info.get("fps_den"), r["media_id"]))
        dur = info.get("duration_ticks")
        if dur:
            n = con.execute("UPDATE cut SET end_ticks = ? WHERE media_id = ? AND end_ticks > ? AND start_ticks < ?",
                            (dur, r["media_id"], dur, dur)).rowcount
            if n:
                fixed += n
                db.rebuild_docs(con, r["media_id"])
        db.job_done(con, r["media_id"], "probe", PROBE_V)
        con.commit()
    return fixed
