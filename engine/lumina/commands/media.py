"""Media management: Link Media (relink), Locate (search a folder for missing
files), Make Offline, and the offline / duplicate scan (``media.scan`` RPC)."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field

from .project import media_of
from .registry import CommandError, Context, Params, command

# re-probed fields a relink may update (the item's id, name, bin, label, marks stay)
PROBED = ("kind", "duration", "video", "audio")


def is_offline(item: dict) -> bool:
    return bool(item.get("offline")) or not Path(item["path"]).exists()


def _compatible(old: dict, new: dict) -> str | None:
    if old["kind"] != new["kind"]:
        return f"the new file is {new['kind']}, the media is {old['kind']}"
    if old["kind"] != "image" and new["duration"] + 1 < old["duration"] * 0.98:
        return "the new file is shorter than the media (clips would run past its end)"
    return None


def _relink(ctx: Context, item: dict, path: str, force: bool) -> dict:
    if ctx.services is None:
        raise CommandError("NO_SERVICES", "media services unavailable")
    try:
        info = ctx.services.probe(path)
    except Exception as exc:  # noqa: BLE001
        raise CommandError("PROBE_FAILED", f"cannot read {path}: {exc}") from None
    problem = _compatible(item, info)
    if problem and not force:
        raise CommandError("INCOMPATIBLE_MEDIA", problem)
    base = ["media", item["id"]]
    ctx.m.set(base + ["path"], path)
    for k in PROBED:
        if k in info and info[k] != item.get(k):
            ctx.m.set(base + [k], info[k])
    # derived files belong to the old file
    for k in ("proxy_path", "thumbnail_path", "waveform_path", "filmstrip_path"):
        if item.get(k):
            ctx.m.set(base + [k], None)
    if item.get("offline"):
        ctx.m.set(base + ["offline"], False)
    return {"media_id": item["id"], "path": path, "warning": problem}


class Relink(Params):
    media_id: str
    path: str
    force: bool = Field(default=False, description="relink even if the new file is a different kind or shorter")


@command("media.relink", Relink)
def media_relink(ctx: Context, p: Relink):
    """Link Media: point a media item (and every clip using it) at another file."""
    return _relink(ctx, media_of(ctx.doc, p.media_id), p.path, p.force)


class Locate(Params):
    folder: str = Field(description="searched recursively")
    media_ids: list[str] = Field(default_factory=list, description="default: every offline media item")
    match: str = Field(default="name", description="name: same file name; stem: same name ignoring the extension")


@command("media.locate", Locate)
def media_locate(ctx: Context, p: Locate):
    """Locate missing media: search a folder for files with the same names and relink them (Premiere's
    Link Media > Search)."""
    root = Path(p.folder)
    if not root.is_dir():
        raise CommandError("NO_FOLDER", f"not a folder: {p.folder}")
    items = [media_of(ctx.doc, m) for m in p.media_ids] if p.media_ids else [m for m in ctx.doc["media"].values() if is_offline(m)]
    key = (lambda n: n.lower()) if p.match == "name" else (lambda n: Path(n).stem.lower())
    index: dict[str, str] = {}
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            index.setdefault(key(f), os.path.join(dirpath, f))
    linked, missing, rejected = [], [], []
    for item in items:
        hit = index.get(key(Path(item["path"]).name))
        if hit is None:
            missing.append(item["id"])
            continue
        try:
            _relink(ctx, item, hit, False)
            linked.append({"media_id": item["id"], "path": hit})
        except CommandError as exc:
            rejected.append({"media_id": item["id"], "path": hit, "reason": exc.message})
    return {"linked": linked, "missing": missing, "rejected": rejected}


DERIVED = ("proxy_path", "thumbnail_path", "waveform_path", "filmstrip_path")


class Refresh(Params):
    media_ids: list[str] = Field(min_length=1)


@command("media.refresh", Refresh, undoable=False)
def media_refresh(ctx: Context, p: Refresh):
    """The file changed on disk (saved in Audition, re-rendered): re-read it, bump its revision and
    forget its generated files so they are made again (cache data, not undoable)."""
    if ctx.services is None:
        raise CommandError("NO_SERVICES", "media services unavailable")
    out = []
    for mid in p.media_ids:
        item = media_of(ctx.doc, mid)
        try:
            info = ctx.services.probe(item["path"])
        except Exception as exc:  # noqa: BLE001
            raise CommandError("PROBE_FAILED", f"cannot read {item['path']}: {exc}") from None
        base = ["media", mid]
        for k in ("duration", "video", "audio"):  # the kind stays: its clips depend on it
            if k in info and info[k] != item.get(k):
                ctx.m.set(base + [k], info[k])
        for k in DERIVED:
            if item.get(k):
                ctx.m.set(base + [k], None)
        revision = int(item.get("revision") or 0) + 1
        ctx.m.set(base + ["revision"], revision)
        out.append({"media_id": mid, "revision": revision, "duration": info.get("duration", item["duration"])})
    return {"refreshed": out}


class AudioForEdit(Params):
    clip_ids: list[str] = Field(min_length=1)
    sequence_id: str | None = None


def _extracted_path(src: str, clip_name: str) -> Path:
    """'<clip name> Audio Extracted.wav' next to the source, numbered when taken (Premiere's naming)."""
    folder = Path(src).parent
    stem = clip_name
    if Path(clip_name).suffix.lower() == Path(src).suffix.lower():
        stem = Path(clip_name).stem
    stem = "".join("_" if ch in '<>:"/\\|?*' else ch for ch in stem).strip() or Path(src).stem
    path, n = folder / f"{stem} Audio Extracted.wav", 1
    while path.exists():
        path, n = folder / f"{stem} Audio Extracted {n}.wav", n + 1
    return path


def _shift_keyframes(kfs: dict, delta: int) -> dict:
    return {k: [{**kf, "t": kf["t"] - delta} for kf in lst] for k, lst in kfs.items()}


@command("clip.prepare_audio_edit", AudioForEdit)
def clip_prepare_audio_edit(ctx: Context, p: AudioForEdit):
    """Edit in Adobe Audition (Premiere's Edit > Edit in Adobe Audition > Clip): the file each audio clip is
    edited in. A clip that plays its whole audio file untouched (from its start to its end at 100 %) edits that
    file. Any other clip has the source range it plays rendered to '<clip> Audio Extracted.wav' next to the
    source; the copy is imported and the clip switched to it, keeping its timing, speed, gain, effects and
    keyframes. Undo puts the clip back and, as in Premiere, leaves the rendered file on disk."""
    from .. import timebase
    from .project import new_media_item, seq_of
    from .timeline import find_clip

    if ctx.services is None:
        raise CommandError("NO_SERVICES", "media services unavailable")
    seq = seq_of(ctx.doc, p.sequence_id)
    st = seq["settings"]
    frame = timebase.ticks_per_frame(st["fps_num"], st["fps_den"])
    out = []
    for cid in p.clip_ids:
        tid, c = find_clip(seq, cid)
        media = ctx.doc["media"].get(c.get("media_id") or "")
        if seq["tracks"][tid]["kind"] != "audio" or media is None or not media.get("audio"):
            raise CommandError("NOT_AUDIO_CLIP", f"not an audio clip of a media file: {cid}")
        span = round((c["end"] - c["start"]) * c["speed"])
        lo, hi = sorted((c["source_in"], c["source_in"] + span))
        lo, hi = max(0, lo), min(media["duration"], hi)
        untouched = (media["kind"] == "audio" and c["speed"] == 1 and lo <= frame // 2
                     and hi >= media["duration"] - frame // 2)
        if untouched:
            out.append({"clip_id": cid, "media_id": media["id"], "path": media["path"], "extracted": False})
            continue
        if hi <= lo:
            raise CommandError("EMPTY_RANGE", f"clip {cid} plays no audio of its media")
        dest = _extracted_path(media["path"], c["name"])
        try:
            ctx.services.extract_audio(media["path"], lo, hi - lo, str(dest), media["audio"])
            info = ctx.services.probe(str(dest))
        except Exception as exc:  # noqa: BLE001
            raise CommandError("EXTRACT_FAILED", f"cannot render {dest.name}: {exc}") from None
        item = new_media_item(str(dest), media["bin_id"], info)
        ctx.m.set(["media", item["id"]], item)
        base = ["sequences", seq["id"], "tracks", tid, "clips", cid]
        ctx.m.set(base + ["media_id"], item["id"])
        ctx.m.set(base + ["name"], item["name"])
        ctx.m.set(base + ["source_in"], c["source_in"] - lo)
        if c.get("keyframes"):  # keyframes sit on source time
            ctx.m.set(base + ["keyframes"], _shift_keyframes(c["keyframes"], lo))
        if any(fx.get("keyframes") for fx in c.get("effects") or []):
            ctx.m.set(base + ["effects"], [{**fx, "keyframes": _shift_keyframes(fx["keyframes"], lo)} if fx.get("keyframes") else fx
                                           for fx in c["effects"]])
        out.append({"clip_id": cid, "media_id": item["id"], "path": str(dest), "extracted": True})
    return {"clips": out}


class MakeOffline(Params):
    media_ids: list[str] = Field(min_length=1)
    offline: bool = Field(default=True, description="false brings it back online (if the file exists)")


@command("media.make_offline", MakeOffline)
def media_make_offline(ctx: Context, p: MakeOffline):
    """Make Offline: the media renders as missing (the file is kept); offline=false undoes it."""
    for mid in p.media_ids:
        item = media_of(ctx.doc, mid)
        if bool(item.get("offline")) != p.offline:
            ctx.m.set(["media", mid, "offline"], p.offline)
    return {"media_ids": p.media_ids, "offline": p.offline}


def scan(doc: dict) -> dict:
    """Offline media (missing file or Made Offline) and duplicate imports (the same file, or files
    with the same name, size and duration)."""
    offline, by_path, by_sig = [], {}, {}
    for m in doc["media"].values():
        exists = Path(m["path"]).exists()
        if m.get("offline") or not exists:
            offline.append({"media_id": m["id"], "name": m["name"], "path": m["path"],
                            "reason": "made offline" if m.get("offline") and exists else "file missing"})
        by_path.setdefault(os.path.normcase(os.path.abspath(m["path"])), []).append(m["id"])
        if exists:
            sig = (Path(m["path"]).name.lower(), os.path.getsize(m["path"]), m["duration"])
            by_sig.setdefault(sig, []).append(m["id"])
    groups = {tuple(sorted(v)) for v in list(by_path.values()) + list(by_sig.values()) if len(v) > 1}
    used: dict[str, int] = {}
    for seq in doc["sequences"].values():
        for t in seq["tracks"].values():
            for c in t["clips"].values():
                if c.get("media_id"):
                    used[c["media_id"]] = used.get(c["media_id"], 0) + 1
    return {
        "offline": offline,
        "duplicates": [{"media_ids": list(g), "names": [doc["media"][i]["name"] for i in g],
                        "clip_counts": [used.get(i, 0) for i in g]} for g in sorted(groups)],
        "unused": [m["id"] for m in doc["media"].values() if m["id"] not in used],
    }
