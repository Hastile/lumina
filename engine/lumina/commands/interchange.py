"""Import from other editors (Premiere .prproj today)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import Field

from ..model import new_id
from .project import bin_id_or_root, build_sequence
from .registry import CommandError, Context, Params, command
from .timeline import DEFAULT_MOTION, source_at


class ImportPremiere(Params):
    path: str = Field(description="path to a .prproj file (read-only; never modified)")
    bin_id: str | None = Field(default=None, description="bin to import into; default: a new bin named after the project")
    into_root: bool = Field(default=False, description="import bins directly into the root (used when opening a .prproj as a new project)")
    sequences: list[str] | None = Field(default=None, description="only these sequence names (default: all)")


@command("project.import_premiere", ImportPremiere)
def project_import_premiere(ctx: Context, p: ImportPremiere):
    """Import an Adobe Premiere Pro project: bins, media, sequences, clips, links, markers."""
    from ..io import prproj

    if not Path(p.path).is_file():
        raise CommandError("NO_FILE", f"file not found: {p.path}")
    try:
        ex = prproj.extract(p.path)
    except prproj.PrprojError as exc:
        raise CommandError("BAD_PRPROJ", str(exc)) from None
    doc = ctx.doc
    warnings = list(ex.warnings)

    if p.into_root:
        top = bin_id_or_root(doc, None)
    else:
        top = bin_id_or_root(doc, p.bin_id) if p.bin_id else None
        if top is None:
            top = new_id()
            ctx.m.set(["bins", top], {"id": top, "name": ex.name, "parent_id": doc["root_bin_id"]})

    bin_map: dict[str | None, str] = {None: top}
    for b in ex.bins:  # parents always precede children (depth-first walk)
        bid = new_id()
        bin_map[b["key"]] = bid
        ctx.m.set(["bins", bid], {"id": bid, "name": b["name"], "parent_id": bin_map.get(b["parent"], top)})

    # probe real files in parallel; fall back to what the project file says
    probed: dict[str, dict] = {}
    if ctx.services is not None:
        def probe(uid_info):
            uid, info = uid_info
            try:
                return uid, ctx.services.probe(info["path"])
            except Exception:  # noqa: BLE001 - offline or unreadable media
                return uid, None
        with ThreadPoolExecutor(max_workers=8) as pool:
            probed = {uid: r for uid, r in pool.map(probe, ex.media.items()) if r}
    offline = [m["name"] for uid, m in ex.media.items() if uid not in probed and not Path(m["path"]).exists()]
    if offline:
        warnings.append(f"{len(offline)} media file(s) are offline: " + ", ".join(offline[:8]) + ("…" if len(offline) > 8 else ""))

    media_map: dict[str, str] = {}
    for uid, m in ex.media.items():
        mid = new_id()
        media_map[uid] = mid
        info = probed.get(uid) or {k: m[k] for k in ("kind", "duration", "video", "audio")}
        # Premiere can count a file longer than its decoded audio (MP3s without an exact length
        # header); its clips use that tail, which plays as silence. Keep Premiere's length so
        # those clips import as they are instead of being shortened.
        duration = max(info["duration"], m["duration"]) if info["kind"] != "image" and m.get("duration") else info["duration"]
        ctx.m.set(["media", mid], {
            "id": mid, "name": m["name"], "path": m["path"], "bin_id": bin_map.get(m["bin"], top),
            "label": m["label"], "proxy_path": None, "thumbnail_path": None, "waveform_path": None, "filmstrip_path": None,
            "in_point": None, "out_point": None, "offline": False,
            "kind": info["kind"], "duration": duration, "video": info["video"], "audio": info["audio"],
        })

    # captions files, Motion Graphics Templates and adjustment layers: project items, not media
    for a in ex.assets:
        aid = new_id()
        ctx.m.set(["assets", aid], {"id": aid, "name": a["name"], "bin_id": bin_map.get(a["bin"], top), "kind": a["kind"],
                                    "path": a.get("path"), "cues": a.get("cues"), "label": a.get("label") or "rose"})

    created = []
    clamped = 0
    # nested sequences: every sequence a chosen one shows comes along, with ids known up front
    by_uid = {s.get("uid"): s for s in ex.sequences}
    wanted = [s for s in ex.sequences if not p.sequences or s["name"] in p.sequences]
    todo = list(wanted)
    while todo:
        s = todo.pop()
        for tr in s["video_tracks"] + s["audio_tracks"]:
            for it in tr["items"]:
                inner = by_uid.get(it.get("sequence_uid"))
                if inner is not None and inner not in wanted:
                    wanted.append(inner)
                    todo.append(inner)
    seq_ids = {s.get("uid"): new_id() for s in wanted}
    for s in ex.sequences:
        if s not in wanted:
            continue
        seq = build_sequence(s["name"], bin_map.get(s["bin"], top), s["settings"],
                             max(1, len(s["video_tracks"])), max(1, len(s["audio_tracks"])))
        seq["id"] = seq_ids[s.get("uid")]
        links: dict[str, str] = {}
        for kind in ("video", "audio"):
            for track_id, tr in zip(seq[f"{kind}_tracks"], s[f"{kind}_tracks"]):
                track = seq["tracks"][track_id]
                track["muted"] = tr["muted"] if kind == "audio" else False
                track["hidden"] = tr["muted"] if kind == "video" else False
                track["targeted"] = track["targeted"]
                prev_end = 0
                pending: dict = {}
                for it in sorted(tr["items"], key=lambda i: i["start"]):
                    graphic = it.get("graphic")
                    nested = seq_ids.get(it.get("sequence_uid")) if it.get("sequence_uid") else None
                    if it.get("sequence_uid") and nested is None:
                        continue
                    if graphic or nested or it.get("adjustment"):
                        if (graphic or it.get("adjustment")) and kind != "video":
                            continue
                        mid, item = None, None
                    else:
                        mid = media_map.get(it["media_uid"])
                        if mid is None:
                            continue
                        item = doc["media"][mid]
                        if kind == "video" and item["video"] is None and item["kind"] != "image":
                            continue
                        if kind == "audio" and item["audio"] is None:
                            continue
                    clip = {
                        "id": new_id(), "media_id": mid, "name": it["name"],
                        "start": max(it["start"], prev_end), "end": it["end"],
                        "source_in": it["source_in"], "speed": it["speed"], "enabled": True,
                        "link_id": links.setdefault(it["link"], new_id()) if it["link"] else None,
                        "label": it["label"] if kind == "video" else "caribbean",
                        "opacity": float(it.get("opacity", 100.0)), "volume_db": 0.0, "gain_db": 0.0, "audio_tag": None, "sequence_id": nested, "muted": False, "auto_match": None,
                        "motion": {**DEFAULT_MOTION, **it.get("motion", {})},
                        "keyframes": it.get("keyframes", {}),
                        "graphic": graphic,
                        "caption": None,
                        "effects": [{"id": new_id(), **e} for e in it.get("effects", [])],
                        "freeze": None,
                        "adjustment": bool(it.get("adjustment")),
                    }
                    if clip["start"] != it["start"]:
                        clip["source_in"] += round((clip["start"] - it["start"]) * clip["speed"])
                    limit = None if item is None or item["kind"] == "image" else item["duration"]
                    if limit and clip["speed"] > 0 and source_at(clip, clip["end"]) > limit:
                        # project-file durations and ffprobe can disagree by a frame or two
                        clip["end"] = clip["start"] + int((limit - clip["source_in"]) / clip["speed"])
                        clamped += 1
                    elif limit and clip["speed"] < 0 and clip["source_in"] > limit:
                        # reversed: the clip starts past the media end; trim its head to the last frame that exists
                        clip["start"] += int(round((clip["source_in"] - limit) / -clip["speed"]))
                        clip["source_in"] = limit
                        clamped += 1
                    if clip["end"] <= clip["start"] or clip["source_in"] < 0:
                        continue
                    track["clips"][clip["id"]] = clip
                    prev_end = clip["end"]
                    for tr in it.get("transitions", []):
                        # one Premiere transition object is referenced as the outgoing clip's tail
                        # and the incoming clip's head
                        entry = pending.setdefault(tr["uid"], {"tr": tr, "a": None, "b": None})
                        entry["a" if tr["side"] == "tail" else "b"] = clip["id"]
                for entry in pending.values():
                    if entry["a"] is None and entry["b"] is None:
                        continue
                    tid_ = new_id()
                    tr = entry["tr"]
                    track["transitions"][tid_] = {"id": tid_, "type": tr["type"], "clip_a": entry["a"], "clip_b": entry["b"],
                                                  "duration": tr["duration"], "alignment": tr["alignment"]}
        for mk in s["markers"]:
            mkid = new_id()
            seq["markers"][mkid] = {"id": mkid, **mk}
        seq["playhead"] = s["playhead"]
        ctx.m.set(["sequences", seq["id"]], seq)
        created.append(seq["id"])
    # a nested clip may run past the end of its sequence in Premiere (black); here it ends with it
    from .timeline import sequence_end

    for sid in created:
        seq = doc["sequences"][sid]
        for tid, track in seq["tracks"].items():
            for c in list(track["clips"].values()):
                if not c.get("sequence_id") or c["sequence_id"] not in doc["sequences"]:
                    continue
                limit = sequence_end(doc["sequences"][c["sequence_id"]])
                if source_at(c, c["end"]) > limit:
                    end = c["start"] + int((limit - c["source_in"]) / c["speed"])
                    if end <= c["start"]:
                        ctx.m.delete(["sequences", sid, "tracks", tid, "clips", c["id"]])
                    else:
                        ctx.m.set(["sequences", sid, "tracks", tid, "clips", c["id"], "end"], end)
                        clamped += 1
    if clamped:
        warnings.append(f"{clamped} clip(s) were shortened by a few frames to fit the actual media length")
    if created and not doc.get("active_sequence_id"):
        ctx.m.set(["active_sequence_id"], created[-1])
    return {"bin_id": top, "media": len(media_map), "sequences": created, "bins": len(ex.bins), "warnings": warnings}
