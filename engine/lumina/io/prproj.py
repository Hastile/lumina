"""Read Adobe Premiere Pro projects (.prproj) for hand-off into Lumina.

A .prproj is gzip-compressed XML holding an object graph: elements carry
``ObjectID`` (numeric) or ``ObjectUID`` (GUID) and point at each other with
``ObjectRef`` / ``ObjectURef``. The format is undocumented; this reader was
derived from real Premiere 2025-26 projects and is deliberately tolerant:
anything it cannot map is skipped and reported in ``warnings`` instead of
failing the import. The source file is only ever read.

What is imported: the bin tree, file-based media, sequences (frame rate, frame
size, video/audio tracks, clip placement, source in, speed, link groups,
label colors, track mute), sequence markers and the playhead. Not imported
yet: effects/keyframes, transitions, graphics/MOGRTs, captions and nested
sequences (each is listed in ``warnings``).

Premiere ticks are the same 254,016,000,000/s unit Lumina uses, so times copy
over exactly.
"""

from __future__ import annotations

import gzip
import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from ..timebase import TICKS_PER_SECOND

LABELS = [
    "violet", "iris", "caribbean", "lavender", "cerulean", "forest", "rose", "mango",
    "purple", "blue", "teal", "magenta", "tan", "green", "brown", "yellow",
]
GRAPHIC_EXTS = {".aegraphic", ".mogrt", ".prtl", ".srt", ".vtt", ".scc", ".xml"}
MARKER_COLORS = ["green", "red", "purple", "orange", "yellow", "white", "blue", "cyan"]


class PrprojError(Exception):
    pass


@dataclass
class Graph:
    root: ET.Element
    by_id: dict[str, ET.Element]
    by_uid: dict[str, ET.Element]

    def ref(self, el: ET.Element | None) -> ET.Element | None:
        """Follow an ObjectRef/ObjectURef attribute on ``el``."""
        if el is None:
            return None
        if (r := el.get("ObjectRef")) is not None:
            return self.by_id.get(r)
        if (u := el.get("ObjectURef")) is not None:
            return self.by_uid.get(u)
        return None

    def at(self, el: ET.Element | None, path: str) -> ET.Element | None:
        return self.ref(el.find(path)) if el is not None else None


def load(path: str) -> Graph:
    p = Path(path[4:] if path.startswith("\\\\?\\") else path)
    raw = p.read_bytes()
    try:
        data = gzip.decompress(raw)
    except OSError:
        data = raw  # some tools save uncompressed XML
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise PrprojError(f"not a readable Premiere project: {exc}") from None
    if root.tag != "PremiereData":
        raise PrprojError("not a Premiere project (missing PremiereData root)")
    by_id, by_uid = {}, {}
    for el in root:
        if (oid := el.get("ObjectID")) is not None:
            by_id[oid] = el
        if (ouid := el.get("ObjectUID")) is not None:
            by_uid[ouid] = el
    return Graph(root, by_id, by_uid)


def _text(el: ET.Element | None, path: str, default: str | None = None) -> str | None:
    if el is None:
        return default
    x = el.find(path)
    return x.text if x is not None and x.text is not None else default


def _int(el: ET.Element | None, path: str, default: int = 0) -> int:
    t = _text(el, path)
    try:
        return int(float(t)) if t is not None else default
    except ValueError:
        return default


def _label(name: str | None, default: str = "iris") -> str:
    if name and name.startswith("BE.Prefs.LabelColors."):
        try:
            return LABELS[int(name.rsplit(".", 1)[1]) % len(LABELS)]
        except ValueError:
            pass
    return default


def _rate_from_frame_ticks(frame_ticks: int) -> tuple[int, int]:
    if frame_ticks <= 0:
        return 30, 1
    f = Fraction(TICKS_PER_SECOND, frame_ticks)
    return f.numerator, f.denominator


def _rect(text: str | None) -> tuple[int, int]:
    try:
        x0, y0, x1, y1 = (int(v) for v in (text or "").split(","))
        return x1 - x0, y1 - y0
    except ValueError:
        return 0, 0


# ---------------------------------------------------------------- extraction

@dataclass
class Extract:
    name: str
    bins: list[dict] = field(default_factory=list)          # {key, name, parent}
    media: dict[str, dict] = field(default_factory=dict)    # media uid -> info
    sequences: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _clip_source(g: Graph, clip_el: ET.Element | None) -> tuple[str, ET.Element | None]:
    """Return ("media", Media) / ("sequence", Sequence) / ("none", None) for a VideoClip/AudioClip."""
    src = g.at(clip_el, "Clip/Source")
    if src is None:
        return "none", None
    media = g.at(src, "MediaSource/Media")
    if media is not None:
        return "media", media
    seq = g.at(src, "SequenceSource/Sequence")
    if seq is not None:
        return "sequence", seq
    return "none", None


def _media_info(g: Graph, media: ET.Element) -> dict | None:
    path = _text(media, "ActualMediaFilePath") or _text(media, "FilePath")
    if not path or ("\\" not in path and "/" not in path):
        return None  # synthetic media (graphics, mattes) store an id, not a path
    if Path(path).suffix.lower() in GRAPHIC_EXTS:
        return None  # MOGRT / Essential Graphics / legacy titles: rendered by Premiere, not media files
    vstream = g.at(media, "VideoStream")
    astream = g.at(media, "AudioStream")
    info: dict = {"path": path, "title": _text(media, "Title") or Path(path).name, "video": None, "audio": None, "still": False}
    duration = 0
    if vstream is not None:
        w, h = _rect(_text(vstream, "FrameRect"))
        fn, fd = _rate_from_frame_ticks(_int(vstream, "FrameRate", 8467200000))
        info["video"] = {"width": w, "height": h, "fps_num": fn, "fps_den": fd, "codec": "", "pix_fmt": ""}
        info["still"] = _text(vstream, "IsStill") == "true"
        duration = _int(vstream, "Duration")
    if astream is not None:
        info["audio"] = {"channels": 2, "sample_rate": 48000, "codec": ""}
        duration = max(duration, _int(astream, "Duration"))
    info["duration"] = 0 if info["still"] else duration
    info["kind"] = "image" if info["still"] else ("video" if info["video"] else "audio")
    return info


def extract(path: str) -> Extract:
    g = load(path)
    out = Extract(name=Path(path).stem)
    root_item = next((e for e in g.root if e.tag == "RootProjectItem"), None)
    if root_item is None:
        raise PrprojError("project has no RootProjectItem")

    seq_bins: dict[str, str] = {}        # Sequence uid -> bin key
    seq_names: dict[str, str] = {}
    media_bins: dict[str, str] = {}      # Media uid -> bin key
    master_media: dict[str, str] = {}    # MasterClip uid -> Media uid
    skipped_items: list[str] = []

    def walk(container: ET.Element, bin_key: str | None, depth: int = 0) -> None:
        items = container.find("ProjectItemContainer/Items")
        if items is None or depth > 64:
            return
        for it in items:
            el = g.ref(it)
            if el is None:
                continue
            name = _text(el, "ProjectItem/Name", "Untitled")
            if el.tag == "BinProjectItem":
                key = el.get("ObjectUID")
                out.bins.append({"key": key, "name": name, "parent": bin_key})
                walk(el, key, depth + 1)
            elif el.tag == "ClipProjectItem":
                master = g.at(el, "MasterClip")
                clip0 = g.ref(master.find("Clips/Clip")) if master is not None and master.find("Clips/Clip") is not None else None
                kind, target = _clip_source(g, clip0)
                label = _label(_text(el, "ProjectItem/Node/Properties/Column.PropertyText.Label"))
                if kind == "sequence":
                    uid = target.get("ObjectUID")
                    seq_bins[uid] = bin_key
                    seq_names[uid] = name
                elif kind == "media":
                    info = _media_info(g, target)
                    if info is None:
                        skipped_items.append(name)
                        continue
                    uid = target.get("ObjectUID")
                    media_bins.setdefault(uid, bin_key)
                    master_media[master.get("ObjectUID")] = uid
                    if uid not in out.media:
                        out.media[uid] = {**info, "name": name, "label": label, "bin": bin_key}
                else:
                    skipped_items.append(name)

    walk(root_item, None)
    if skipped_items:
        out.warnings.append(f"{len(skipped_items)} project item(s) are not file media (graphics/MOGRT/synthetic) and were skipped: "
                            + ", ".join(skipped_items[:8]) + ("…" if len(skipped_items) > 8 else ""))

    bin_names = {b["key"]: b["name"] for b in out.bins}
    all_names = [seq_names.get(e.get("ObjectUID")) or _text(e, "Name", "Sequence") for e in g.root if e.tag == "Sequence"]
    for seq_el in (e for e in g.root if e.tag == "Sequence"):
        uid = seq_el.get("ObjectUID")
        name = seq_names.get(uid) or _text(seq_el, "Name", "Sequence")
        if all_names.count(name) > 1 and seq_bins.get(uid) in bin_names:
            name = f"{name} ({bin_names[seq_bins[uid]]})"  # Premiere allows duplicates; keep them tellable apart
        out.sequences.append(_extract_sequence(g, seq_el, name, seq_bins.get(uid), out))
    return out


def _extract_sequence(g: Graph, seq_el: ET.Element, name: str, bin_key: str | None, out: Extract) -> dict:
    groups = {}
    for tg in seq_el.findall("TrackGroups/TrackGroup"):
        grp = g.ref(tg.find("Second"))
        if grp is not None:
            groups[grp.tag] = grp
    vgroup, agroup = groups.get("VideoTrackGroup"), groups.get("AudioTrackGroup")
    fn, fd = _rate_from_frame_ticks(_int(vgroup, "TrackGroup/FrameRate", 8467200000))
    w, h = _rect(_text(vgroup, "FrameRect"))
    sample_ticks = _int(agroup, "TrackGroup/FrameRate", 5292000)
    sample_rate = round(TICKS_PER_SECOND / sample_ticks) if sample_ticks else 48000
    props = seq_el.find("Node/Properties")

    # link groups: TrackItem ObjectID -> group key
    links: dict[str, str] = {}
    for link_ref in seq_el.findall("PersistentGroupContainer/LinkContainer/Links/Link"):
        link = g.ref(link_ref)
        if link is None:
            continue
        for ti in link.findall("TrackItemGroup/TrackItems/TrackItem"):
            if ti.get("ObjectRef"):
                links[ti.get("ObjectRef")] = link.get("ObjectID") or link_ref.get("ObjectRef")

    stats = {"nested": 0, "graphics": 0, "transitions": 0, "reversed": 0}

    def tracks(group: ET.Element | None, kind: str) -> list[dict]:
        result = []
        if group is None:
            return result
        for tref in group.findall("TrackGroup/Tracks/Track"):
            tr = g.ref(tref)
            if tr is None:
                continue
            ct = tr.find("ClipTrack")
            if ct is None:
                continue  # caption/data tracks
            items = []
            for iref in ct.findall("ClipItems/TrackItems/TrackItem"):
                item = g.ref(iref)
                if item is None:
                    continue
                cti = item.find("ClipTrackItem")
                start = _int(cti, "TrackItem/Start", 0)
                end = _int(cti, "TrackItem/End", 0)
                if end <= start:
                    continue
                sub = g.at(cti, "SubClip")
                clip_el = g.at(sub, "Clip")
                src_kind, target = _clip_source(g, clip_el)
                if src_kind == "sequence":
                    stats["nested"] += 1
                    continue
                if src_kind != "media" or target.get("ObjectUID") not in out.media:
                    if src_kind == "media" and (info := _media_info(g, target)) is not None:
                        out.media[target.get("ObjectUID")] = {**info, "name": _text(sub, "Name") or info["title"], "label": "iris", "bin": None}
                    else:
                        stats["graphics"] += 1
                        continue
                speed_txt = _text(clip_el, "Clip/PlaybackSpeed")
                speed = float(speed_txt) if speed_txt else 1.0
                if speed < 0:
                    stats["reversed"] += 1
                    speed = -speed
                if cti.find("HeadTransition") is not None or cti.find("TailTransition") is not None:
                    stats["transitions"] += 1
                items.append({
                    "media_uid": target.get("ObjectUID"),
                    "name": _text(sub, "Name") or out.media[target.get("ObjectUID")]["name"],
                    "start": start,
                    "end": end,
                    "source_in": max(0, _int(clip_el, "Clip/InPoint", 0)),
                    "speed": speed or 1.0,
                    "label": _label(_text(clip_el, "Clip/Node/Properties/asl.clip.label.name"), "iris" if kind == "video" else "caribbean"),
                    "link": links.get(item.get("ObjectID")),
                })
            result.append({"muted": _text(tr, "ClipTrack/Track/IsMuted") == "true", "items": items})
        return result

    seq = {
        "name": name,
        "bin": bin_key,
        "settings": {"width": w or 1920, "height": h or 1080, "fps_num": fn, "fps_den": fd, "sample_rate": sample_rate},
        "video_tracks": tracks(vgroup, "video"),
        "audio_tracks": tracks(agroup, "audio"),
        "playhead": _int(props, "MZ.EditLine", 0),
        "markers": _sequence_markers(g, seq_el),
    }
    parts = []
    if stats["nested"]:
        parts.append(f"{stats['nested']} nested-sequence clip(s)")
    if stats["graphics"]:
        parts.append(f"{stats['graphics']} graphics/synthetic clip(s)")
    if stats["transitions"]:
        parts.append(f"{stats['transitions']} transition(s)")
    if stats["reversed"]:
        parts.append(f"{stats['reversed']} reversed clip(s) imported forward")
    if parts:
        out.warnings.append(f"Sequence '{name}': skipped " + ", ".join(parts))
    return seq


def _sequence_markers(g: Graph, seq_el: ET.Element) -> list[dict]:
    """Sequence markers live on the sequence's master clip (via its VideoSequenceSource)."""
    uid = seq_el.get("ObjectUID")
    markers = []
    for src in (e for e in g.root if e.tag == "VideoSequenceSource"):
        target = src.find("SequenceSource/Sequence")
        if target is None or target.get("ObjectURef") != uid:
            continue
        src_id = src.get("ObjectID")
        for clip in (e for e in g.root if e.tag == "VideoClip"):
            s = clip.find("Clip/Source")
            if s is None or s.get("ObjectRef") != src_id:
                continue
            owner = g.at(clip, "Clip/MarkerOwner/Markers")
            if owner is None:
                continue
            for mref in owner.findall("Markers/Marker"):
                m = g.ref(mref.find("Second"))
                raw = _text(m, "DVAMarker")
                if not raw:
                    continue
                try:
                    d = json.loads(raw).get("DVAMarker", {})
                except ValueError:
                    continue
                color = d.get("mColorIndex")
                markers.append({
                    "start": int(d.get("mStartTime", {}).get("ticks", 0)),
                    "duration": int(d.get("mDuration", {}).get("ticks", 0)) if isinstance(d.get("mDuration"), dict) else 0,
                    "name": d.get("mName", "") or "",
                    "comment": d.get("mComment", "") or "",
                    "color": MARKER_COLORS[color % 8] if isinstance(color, int) else "green",
                })
            return markers
    return markers
