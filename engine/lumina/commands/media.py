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
