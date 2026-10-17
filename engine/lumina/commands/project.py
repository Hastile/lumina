"""Project, bin, media, sequence and track commands."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ..model import LabelColor, new_id
from ..patch import get
from .registry import CommandError, Context, Params, TimeValue, command, resolve_time


# ---------------------------------------------------------------- lookups

def seq_of(doc: dict, sequence_id: str | None) -> dict:
    sid = sequence_id or doc.get("active_sequence_id")
    if not sid or sid not in doc["sequences"]:
        raise CommandError("NO_SEQUENCE", f"sequence not found: {sid}")
    return doc["sequences"][sid]


def media_of(doc: dict, media_id: str) -> dict:
    try:
        return doc["media"][media_id]
    except KeyError:
        raise CommandError("NO_MEDIA", f"media not found: {media_id}") from None


def bin_id_or_root(doc: dict, bin_id: str | None) -> str:
    bid = bin_id or doc["root_bin_id"]
    if bid not in doc["bins"]:
        raise CommandError("NO_BIN", f"bin not found: {bid}")
    return bid


# ---------------------------------------------------------------- project

class RenameProject(Params):
    name: str = Field(min_length=1)


@command("project.rename", RenameProject)
def project_rename(ctx: Context, p: RenameProject):
    """Rename the project."""
    ctx.m.set(["name"], p.name)
    ctx.m.set(["bins", ctx.doc["root_bin_id"], "name"], p.name)


# ---------------------------------------------------------------- bins

class CreateBin(Params):
    name: str = "Bin"
    parent_id: str | None = None


@command("bin.create", CreateBin)
def bin_create(ctx: Context, p: CreateBin):
    """Create a bin in the Project panel (root when parent_id is omitted)."""
    parent = bin_id_or_root(ctx.doc, p.parent_id)
    bid = new_id()
    ctx.m.set(["bins", bid], {"id": bid, "name": p.name, "parent_id": parent})
    return {"bin_id": bid}


class RenameBin(Params):
    bin_id: str
    name: str = Field(min_length=1)


@command("bin.rename", RenameBin)
def bin_rename(ctx: Context, p: RenameBin):
    """Rename a bin."""
    bin_id_or_root(ctx.doc, p.bin_id)
    ctx.m.set(["bins", p.bin_id, "name"], p.name)


class DeleteBin(Params):
    bin_id: str


@command("bin.delete", DeleteBin)
def bin_delete(ctx: Context, p: DeleteBin):
    """Delete an empty bin."""
    doc = ctx.doc
    if p.bin_id == doc["root_bin_id"]:
        raise CommandError("ROOT_BIN", "cannot delete the root bin")
    bin_id_or_root(doc, p.bin_id)
    used = (
        any(b["parent_id"] == p.bin_id for b in doc["bins"].values())
        or any(mi["bin_id"] == p.bin_id for mi in doc["media"].values())
        or any(s["bin_id"] == p.bin_id for s in doc["sequences"].values())
    )
    if used:
        raise CommandError("BIN_NOT_EMPTY", "bin is not empty")
    ctx.m.delete(["bins", p.bin_id])


class MoveItems(Params):
    item_ids: list[str] = Field(description="media, sequence or bin ids")
    bin_id: str


@command("bin.move_items", MoveItems)
def bin_move_items(ctx: Context, p: MoveItems):
    """Move project items into a bin."""
    doc = ctx.doc
    bin_id_or_root(doc, p.bin_id)
    for iid in p.item_ids:
        if iid in doc["media"]:
            ctx.m.set(["media", iid, "bin_id"], p.bin_id)
        elif iid in doc["sequences"]:
            ctx.m.set(["sequences", iid, "bin_id"], p.bin_id)
        elif iid in doc["bins"] and iid != doc["root_bin_id"]:
            # refuse cycles
            cur: str | None = p.bin_id
            while cur:
                if cur == iid:
                    raise CommandError("BIN_CYCLE", "cannot move a bin into itself")
                cur = doc["bins"][cur]["parent_id"]
            ctx.m.set(["bins", iid, "parent_id"], p.bin_id)
        else:
            raise CommandError("NO_ITEM", f"project item not found: {iid}")


# ---------------------------------------------------------------- media

class ImportMedia(Params):
    paths: list[str] = Field(min_length=1)
    bin_id: str | None = None


@command("media.import", ImportMedia)
def media_import(ctx: Context, p: ImportMedia):
    """Import media files into the project (probes each with ffprobe)."""
    bid = bin_id_or_root(ctx.doc, p.bin_id)
    if ctx.services is None:
        raise CommandError("NO_SERVICES", "media services unavailable")
    ids, errors = [], []
    for path in p.paths:
        try:
            info = ctx.services.probe(path)
        except Exception as exc:  # noqa: BLE001 - reported per file
            errors.append({"path": path, "error": str(exc)})
            continue
        mid = new_id()
        item = {
            "id": mid,
            "path": path,
            "bin_id": bid,
            "label": "iris" if info["kind"] == "video" else ("forest" if info["kind"] == "audio" else "lavender"),
            "proxy_path": None,
            "thumbnail_path": None,
            "waveform_path": None,
            "filmstrip_path": None,
            "in_point": None,
            "out_point": None,
            "offline": False,
            **info,
        }
        ctx.m.set(["media", mid], item)
        ids.append(mid)
    if not ids:
        raise CommandError("IMPORT_FAILED", "no files imported", errors=errors)
    return {"media_ids": ids, "errors": errors}


class SetMediaMarks(Params):
    media_id: str
    in_point: int | None = Field(default=None, description="source ticks; omitted = keep")
    out_point: int | None = Field(default=None, description="source ticks; omitted = keep")
    clear_in: bool = False
    clear_out: bool = False


def _merge_marks(cur_in, cur_out, p) -> tuple[int | None, int | None]:
    """Premiere semantics: only the given mark changes; a mark that would cross the other clears it."""
    new_in = None if p.clear_in else (p.in_point if p.in_point is not None else cur_in)
    new_out = None if p.clear_out else (p.out_point if p.out_point is not None else cur_out)
    if new_in is not None and new_out is not None and new_out <= new_in:
        if p.in_point is not None and p.out_point is None:
            new_out = None
        elif p.out_point is not None and p.in_point is None:
            new_in = None
        else:
            raise CommandError("BAD_RANGE", "out must be after in")
    return new_in, new_out


@command("media.set_marks", SetMediaMarks)
def media_set_marks(ctx: Context, p: SetMediaMarks):
    """Set or clear the source monitor In/Out marks (only the given mark changes)."""
    item = media_of(ctx.doc, p.media_id)
    new_in, new_out = _merge_marks(item["in_point"], item["out_point"], p)
    if item["in_point"] != new_in:
        ctx.m.set(["media", p.media_id, "in_point"], new_in)
    if item["out_point"] != new_out:
        ctx.m.set(["media", p.media_id, "out_point"], new_out)
    return {"in_point": new_in, "out_point": new_out}


class RenameMedia(Params):
    media_id: str
    name: str = Field(min_length=1)
    label: LabelColor | None = None


@command("media.rename", RenameMedia)
def media_rename(ctx: Context, p: RenameMedia):
    """Rename a project media item and optionally change its label color."""
    media_of(ctx.doc, p.media_id)
    ctx.m.set(["media", p.media_id, "name"], p.name)
    if p.label:
        ctx.m.set(["media", p.media_id, "label"], p.label)


class RemoveMedia(Params):
    media_ids: list[str]
    remove_clips: bool = Field(default=False, description="also remove timeline clips that use the media")


@command("media.remove", RemoveMedia)
def media_remove(ctx: Context, p: RemoveMedia):
    """Remove media from the project."""
    doc = ctx.doc
    for mid in p.media_ids:
        media_of(doc, mid)
        users = [
            (sid, tid, cid)
            for sid, s in doc["sequences"].items()
            for tid, t in s["tracks"].items()
            for cid, c in t["clips"].items()
            if c["media_id"] == mid
        ]
        if users and not p.remove_clips:
            raise CommandError("MEDIA_IN_USE", f"media {mid} is used by {len(users)} clip(s)", clips=[u[2] for u in users])
        for sid, tid, cid in users:
            ctx.m.delete(["sequences", sid, "tracks", tid, "clips", cid])
        ctx.m.delete(["media", mid])


class SetDerived(Params):
    media_id: str
    proxy_path: str | None = None
    thumbnail_path: str | None = None
    waveform_path: str | None = None
    filmstrip_path: str | None = None


@command("media.set_derived", SetDerived, undoable=False)
def media_set_derived(ctx: Context, p: SetDerived):
    """Record generated proxy/thumbnail/waveform files (cache data, not undoable)."""
    media_of(ctx.doc, p.media_id)
    for key in ("proxy_path", "thumbnail_path", "waveform_path", "filmstrip_path"):
        value = getattr(p, key)
        if value is not None:
            ctx.m.set(["media", p.media_id, key], value)


# ---------------------------------------------------------------- sequences

class SequenceSettingsParams(Params):
    width: int = 1920
    height: int = 1080
    fps_num: int = 30
    fps_den: int = 1
    sample_rate: int = 48000


def _make_track(kind: str, index: int) -> dict:
    tid = new_id()
    return {
        "id": tid,
        "kind": kind,
        "name": f"{'Video' if kind == 'video' else 'Audio'} {index}",
        "locked": False,
        "sync_lock": True,
        "hidden": False,
        "muted": False,
        "solo": False,
        "targeted": index == 1,
        "volume_db": 0.0,
        "pan": 0.0,
        "clips": {},
        "transitions": {},
        "effects": [],
        "caption_style": None,
    }


def build_sequence(name: str, bin_id: str, settings: dict, video_tracks: int, audio_tracks: int) -> dict:
    from .. import timebase

    timebase.ticks_per_frame(settings["fps_num"], settings["fps_den"])  # validate
    sid = new_id()
    vt = [_make_track("video", i + 1) for i in range(video_tracks)]
    at = [_make_track("audio", i + 1) for i in range(audio_tracks)]
    return {
        "id": sid,
        "name": name,
        "bin_id": bin_id,
        "settings": settings,
        "tracks": {t["id"]: t for t in vt + at},
        "video_tracks": [t["id"] for t in vt],
        "audio_tracks": [t["id"] for t in at],
        "markers": {},
        "playhead": 0,
        "in_point": None,
        "out_point": None,
        "master_volume_db": 0.0,
        "source_video_track": None,
        "source_audio_track": None,
    }


class CreateSequence(Params):
    name: str = "Sequence 01"
    bin_id: str | None = None
    settings: SequenceSettingsParams = Field(default_factory=SequenceSettingsParams)
    video_tracks: int = Field(default=3, ge=1, le=99)
    audio_tracks: int = Field(default=3, ge=1, le=99)
    activate: bool = True


@command("sequence.create", CreateSequence)
def sequence_create(ctx: Context, p: CreateSequence):
    """Create an empty sequence (File > New > Sequence)."""
    bid = bin_id_or_root(ctx.doc, p.bin_id)
    try:
        seq = build_sequence(p.name, bid, p.settings.model_dump(), p.video_tracks, p.audio_tracks)
    except ValueError as exc:
        raise CommandError("BAD_FRAME_RATE", str(exc)) from None
    ctx.m.set(["sequences", seq["id"]], seq)
    if p.activate:
        ctx.m.set(["active_sequence_id"], seq["id"])
    return {"sequence_id": seq["id"], "video_tracks": seq["video_tracks"], "audio_tracks": seq["audio_tracks"]}


class CreateSequenceFromMedia(Params):
    media_id: str
    name: str | None = None


@command("sequence.create_from_media", CreateSequenceFromMedia)
def sequence_create_from_media(ctx: Context, p: CreateSequenceFromMedia):
    """New Sequence From Clip: match the clip's settings and place it at 0."""
    from .timeline import PlaceMedia, clip_place

    item = media_of(ctx.doc, p.media_id)
    v = item.get("video") or {}
    settings = {
        "width": v.get("width") or 1920,
        "height": v.get("height") or 1080,
        "fps_num": v.get("fps_num") or 30,
        "fps_den": v.get("fps_den") or 1,
        "sample_rate": (item.get("audio") or {}).get("sample_rate") or 48000,
    }
    name = p.name or item["name"].rsplit(".", 1)[0]
    seq = build_sequence(name, item["bin_id"], settings, 3, 3)
    ctx.m.set(["sequences", seq["id"]], seq)
    ctx.m.set(["active_sequence_id"], seq["id"])
    placed = clip_place(ctx, PlaceMedia(sequence_id=seq["id"], media_id=p.media_id, at=0))
    return {"sequence_id": seq["id"], **placed}


class SequenceRef(Params):
    sequence_id: str


class RenameSequence(Params):
    sequence_id: str
    name: str = Field(min_length=1)


@command("sequence.rename", RenameSequence)
def sequence_rename(ctx: Context, p: RenameSequence):
    """Rename a sequence."""
    seq_of(ctx.doc, p.sequence_id)
    ctx.m.set(["sequences", p.sequence_id, "name"], p.name)


@command("sequence.delete", SequenceRef)
def sequence_delete(ctx: Context, p: SequenceRef):
    """Delete a sequence."""
    seq_of(ctx.doc, p.sequence_id)
    ctx.m.delete(["sequences", p.sequence_id])
    if ctx.doc.get("active_sequence_id") == p.sequence_id:
        ctx.m.set(["active_sequence_id"], next(iter(ctx.doc["sequences"]), None))


@command("sequence.duplicate", SequenceRef)
def sequence_duplicate(ctx: Context, p: SequenceRef):
    """Duplicate a sequence (new ids for every track, clip and marker)."""
    import copy

    src = copy.deepcopy(seq_of(ctx.doc, p.sequence_id))
    sid = new_id()
    track_map = {tid: new_id() for tid in src["tracks"]}
    link_map: dict[str, str] = {}
    tracks = {}
    for tid, t in src["tracks"].items():
        t["id"] = track_map[tid]
        clips = {}
        for c in t["clips"].values():
            c["id"] = new_id()
            if c.get("link_id"):
                c["link_id"] = link_map.setdefault(c["link_id"], new_id())
            clips[c["id"]] = c
        t["clips"] = clips
        tracks[t["id"]] = t
    markers = {}
    for mk in src["markers"].values():
        mk["id"] = new_id()
        markers[mk["id"]] = mk
    src.update(
        id=sid,
        name=f"{src['name']} Copy",
        tracks=tracks,
        markers=markers,
        video_tracks=[track_map[t] for t in src["video_tracks"]],
        audio_tracks=[track_map[t] for t in src["audio_tracks"]],
    )
    ctx.m.set(["sequences", sid], src)
    return {"sequence_id": sid}


@command("sequence.set_active", SequenceRef, undoable=False)
def sequence_set_active(ctx: Context, p: SequenceRef):
    """Open a sequence in the Timeline / Program monitor."""
    seq_of(ctx.doc, p.sequence_id)
    ctx.m.set(["active_sequence_id"], p.sequence_id)


class SetPlayhead(Params):
    sequence_id: str | None = None
    time: TimeValue


@command("sequence.set_playhead", SetPlayhead, undoable=False)
def sequence_set_playhead(ctx: Context, p: SetPlayhead):
    """Move the playhead (current time indicator)."""
    seq = seq_of(ctx.doc, p.sequence_id)
    t = max(0, resolve_time(p.time, seq["settings"]))
    ctx.m.set(["sequences", seq["id"], "playhead"], t)
    return {"playhead": t}


class SetSequenceInOut(Params):
    sequence_id: str | None = None
    in_point: TimeValue | None = Field(default=None, description="omitted = keep")
    out_point: TimeValue | None = Field(default=None, description="omitted = keep")
    clear_in: bool = False
    clear_out: bool = False


@command("sequence.set_in_out", SetSequenceInOut)
def sequence_set_in_out(ctx: Context, p: SetSequenceInOut):
    """Set or clear the sequence In/Out marks (only the given mark changes)."""
    seq = seq_of(ctx.doc, p.sequence_id)
    resolved = SetMediaMarks(
        media_id="-",
        in_point=None if p.in_point is None else resolve_time(p.in_point, seq["settings"]),
        out_point=None if p.out_point is None else resolve_time(p.out_point, seq["settings"]),
        clear_in=p.clear_in, clear_out=p.clear_out,
    )
    new_in, new_out = _merge_marks(seq["in_point"], seq["out_point"], resolved)
    if seq["in_point"] != new_in:
        ctx.m.set(["sequences", seq["id"], "in_point"], new_in)
    if seq["out_point"] != new_out:
        ctx.m.set(["sequences", seq["id"], "out_point"], new_out)
    return {"in_point": new_in, "out_point": new_out}


# ---------------------------------------------------------------- tracks

class AddTrack(Params):
    sequence_id: str | None = None
    kind: Literal["video", "audio", "caption"] = Field(description="caption: a video track for caption cues (see caption.*)")
    index: int | None = Field(default=None, description="position in the track list; default appends (top video / bottom audio)")
    name: str | None = None


@command("track.add", AddTrack)
def track_add(ctx: Context, p: AddTrack):
    """Add a video, audio or caption track."""
    seq = seq_of(ctx.doc, p.sequence_id)
    key = "audio_tracks" if p.kind == "audio" else "video_tracks"
    order = list(seq[key])
    track = _make_track("audio" if p.kind == "audio" else "video", len(order) + 1)
    track["targeted"] = False
    if p.kind == "caption":
        from ..tracks import default_caption_style

        track["caption_style"] = default_caption_style(seq["settings"])
        track["name"] = "Subtitles"
    if p.name:
        track["name"] = p.name
    idx = len(order) if p.index is None else max(0, min(p.index, len(order)))
    order.insert(idx, track["id"])
    ctx.m.set(["sequences", seq["id"], "tracks", track["id"]], track)
    ctx.m.set(["sequences", seq["id"], key], order)
    return {"track_id": track["id"]}


class RemoveTrack(Params):
    sequence_id: str | None = None
    track_id: str


@command("track.remove", RemoveTrack)
def track_remove(ctx: Context, p: RemoveTrack):
    """Delete a track and its clips."""
    seq = seq_of(ctx.doc, p.sequence_id)
    track = seq["tracks"].get(p.track_id)
    if not track:
        raise CommandError("NO_TRACK", f"track not found: {p.track_id}")
    key = "video_tracks" if track["kind"] == "video" else "audio_tracks"
    if len(seq[key]) <= 1:
        raise CommandError("LAST_TRACK", "a sequence needs at least one track of each kind")
    ctx.m.set(["sequences", seq["id"], key], [t for t in seq[key] if t != p.track_id])
    ctx.m.delete(["sequences", seq["id"], "tracks", p.track_id])
    for k in ("source_video_track", "source_audio_track"):
        if seq.get(k) == p.track_id:
            ctx.m.set(["sequences", seq["id"], k], None)


class SetTrack(Params):
    sequence_id: str | None = None
    track_id: str
    name: str | None = None
    locked: bool | None = None
    sync_lock: bool | None = None
    hidden: bool | None = None
    muted: bool | None = None
    solo: bool | None = None
    targeted: bool | None = None
    volume_db: float | None = Field(default=None, ge=-96, le=6, description="audio track fader (dB)")
    pan: float | None = Field(default=None, ge=-100, le=100, description="audio track pan, -100 L .. 100 R")


@command("track.set", SetTrack)
def track_set(ctx: Context, p: SetTrack):
    """Set track header toggles (lock, sync lock, eye, mute, solo, target), name, or an audio track's
    mixer volume / pan."""
    seq = seq_of(ctx.doc, p.sequence_id)
    track = seq["tracks"].get(p.track_id)
    if not track:
        raise CommandError("NO_TRACK", f"track not found: {p.track_id}")
    for key in ("name", "locked", "sync_lock", "hidden", "muted", "solo", "targeted", "volume_db", "pan"):
        value = getattr(p, key)
        if value is not None and track.get(key) != value:
            ctx.m.set(["sequences", seq["id"], "tracks", p.track_id, key], value)


def track_path(seq: dict, track_id: str) -> list[str]:
    get({"s": seq}, ["s", "tracks", track_id])
    return ["sequences", seq["id"], "tracks", track_id]


class SetMaster(Params):
    sequence_id: str | None = None
    volume_db: float = Field(ge=-96, le=6, description="the Mix (master) fader, dB")


@command("sequence.set_master", SetMaster)
def sequence_set_master(ctx: Context, p: SetMaster):
    """Audio Track Mixer's Mix fader: the sequence's master volume."""
    seq = seq_of(ctx.doc, p.sequence_id)
    if seq.get("master_volume_db", 0.0) != p.volume_db:
        ctx.m.set(["sequences", seq["id"], "master_volume_db"], p.volume_db)


class SetPatch(Params):
    sequence_id: str | None = None
    video_track_id: str | None = Field(default=None, description="track that source video is patched to")
    audio_track_id: str | None = Field(default=None, description="track that source audio is patched to")
    clear_video: bool = Field(default=False, description="unpatch video: Insert/Overwrite places no video")
    clear_audio: bool = False
    auto: bool = Field(default=False, description="back to following targeting (the first targeted track)")


@command("sequence.set_patch", SetPatch)
def sequence_set_patch(ctx: Context, p: SetPatch):
    """Source patching (the V1/A1 source indicators): which tracks Insert/Overwrite put source video and
    audio on, independent of track targeting (which drives paste, Match Frame and edit-point navigation)."""
    seq = seq_of(ctx.doc, p.sequence_id)
    base = ["sequences", seq["id"]]
    for key, tid, clear, kind in (("source_video_track", p.video_track_id, p.clear_video, "video"),
                                  ("source_audio_track", p.audio_track_id, p.clear_audio, "audio")):
        if p.auto:
            value = None
        elif clear:
            value = ""
        elif tid is not None:
            t = seq["tracks"].get(tid)
            if t is None or t["kind"] != kind:
                raise CommandError("BAD_TRACK", f"{tid} is not a {kind} track")
            value = tid
        else:
            continue
        if seq.get(key) != value:
            ctx.m.set(base + [key], value)
    return {"video": seq.get("source_video_track"), "audio": seq.get("source_audio_track")}
