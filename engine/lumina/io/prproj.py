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

    stats = {"nested": 0, "graphics": 0, "transitions": 0, "reversed": 0, "effects": 0, "shapes": 0, "approx_text": 0}
    seq_w, seq_h = w or 1920, h or 1080

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
                graphic = None
                if src_kind != "media" or target.get("ObjectUID") not in out.media:
                    if src_kind == "media" and (info := _media_info(g, target)) is not None:
                        out.media[target.get("ObjectUID")] = {**info, "name": _text(sub, "Name") or info["title"], "label": "iris", "bin": None}
                    elif kind == "video":
                        graphic = _graphic(g, cti, seq_w, seq_h, stats)
                        if graphic is None:
                            stats["graphics"] += 1
                            continue
                    else:
                        continue
                speed_txt = _text(clip_el, "Clip/PlaybackSpeed")
                speed = float(speed_txt) if speed_txt else 1.0
                if speed < 0:
                    stats["reversed"] += 1
                    speed = -speed
                transitions = [t for t in (_transition(g, g.ref(cti.find("HeadTransition")), "head", kind, stats),
                                           _transition(g, g.ref(cti.find("TailTransition")), "tail", kind, stats)) if t]
                media = None if graphic else out.media[target.get("ObjectUID")]
                fx = _motion(g, cti, seq_w, seq_h, media, stats) if kind == "video" else {}
                items.append({
                    "media_uid": None if graphic else target.get("ObjectUID"),
                    "name": _text(sub, "Name") or (graphic["name"] if graphic else media["name"]),
                    "start": start,
                    "end": end,
                    "source_in": max(0, _int(clip_el, "Clip/InPoint", 0)),
                    "speed": speed or 1.0,
                    "label": _label(_text(clip_el, "Clip/Node/Properties/asl.clip.label.name"),
                                    "rose" if graphic else ("iris" if kind == "video" else "caribbean")),
                    "link": links.get(item.get("ObjectID")),
                    "graphic": graphic and {"layers": graphic["layers"], "source": graphic["source"]},
                    "transitions": transitions,
                    **fx,
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
    if stats["reversed"]:
        parts.append(f"{stats['reversed']} reversed clip(s) imported forward")
    if stats["effects"]:
        names = ", ".join(sorted(stats.get("effect_names", ())))
        parts.append(f"{stats['effects']} unsupported effect(s) ({names})")
    if stats.get("approx_transitions"):
        parts.append(f"transition(s) imported as dissolves: {', '.join(sorted(stats['approx_transitions']))}")
    if stats["shapes"]:
        parts.append(f"{stats['shapes']} shape layer(s)")
    if stats["approx_text"]:
        parts.append(f"{stats['approx_text']} text layer(s) with approximate styling (check font size/position)")
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


# ---------------------------------------------------------------- effects: Motion, Opacity, graphics

def _components(g: Graph, cti: ET.Element) -> list[ET.Element]:
    chain = g.at(cti, "ComponentOwner/Components")
    if chain is None:
        return []
    return [c for c in (g.ref(ref) for ref in chain.findall("ComponentChain/Components/Component")) if c is not None]


def _params(g: Graph, comp: ET.Element) -> dict[int, ET.Element]:
    out = {}
    for ref in comp.findall("Component/Params/Param"):
        pe = g.ref(ref)
        if pe is not None:
            try:
                out[int(_text(pe, "ParameterID", "-1"))] = pe
            except ValueError:
                pass
    return out


def _parse_value(raw: str):
    if ":" in raw:
        x, y = raw.split(":", 1)
        return [float(x), float(y)]
    if raw in ("true", "false"):
        return raw == "true"
    return float(raw)


def _param_static(pe: ET.Element | None):
    if pe is None:
        return None
    raw = _text(pe, "StartKeyframe")
    try:
        if raw is None:
            cur = _text(pe, "CurrentValue")
            return _parse_value(cur) if cur else None
        return _parse_value(raw.split(",")[1])
    except (IndexError, ValueError):
        return None


def _param_keyframes(pe: ET.Element | None) -> list[dict]:
    if pe is None:
        return []
    raw = _text(pe, "Keyframes")
    if not raw:
        return []
    kfs = []
    for entry in raw.split(";"):
        f = entry.split(",")
        if len(f) < 3:
            continue
        try:
            # interpolation code: 0 = linear, 1 = hold, others = Bezier variants (approximated as ease)
            interp = "linear" if f[2] == "0" else ("hold" if f[2] == "1" else "ease")
            kfs.append({"t": int(f[0]), "v": _parse_value(f[1]), "interp": interp})
        except ValueError:
            continue
    return sorted(kfs, key=lambda k: k["t"])


MOTION_PARAMS = {
    1: "position", 2: "scale", 3: "scale_width", 4: "uniform", 5: "rotation", 6: "anchor",
    8: "crop_left", 9: "crop_top", 10: "crop_right", 11: "crop_bottom",
}


def _motion(g: Graph, cti: ET.Element, seq_w: int, seq_h: int, media: dict | None, stats: dict) -> dict:
    """Map the Motion and Opacity components (and their keyframes) onto Lumina clip fields."""
    sw, sh = (media["video"]["width"], media["video"]["height"]) if media and media.get("video") else (seq_w, seq_h)

    def conv(name: str, v):
        if name == "position":
            return [v[0] * seq_w, v[1] * seq_h]
        if name == "anchor":
            return [v[0] * sw, v[1] * sh]
        if name == "uniform":
            return bool(v)
        return float(v)

    motion: dict = {}
    keyframes: dict = {}
    effects: list = []
    opacity = 100.0
    for comp in _components(g, cti):
        match = _text(comp, "MatchName")
        if match == "AE.ADBE Motion":
            for pid, pe in _params(g, comp).items():
                name = MOTION_PARAMS.get(pid)
                if name is None:
                    continue
                v = _param_static(pe)
                try:
                    if v is not None:
                        motion[name] = conv(name, v)
                    kfs = _param_keyframes(pe)
                    if kfs and name != "uniform":
                        keyframes[name] = [{**k, "v": conv(name, k["v"])} for k in kfs]
                except (TypeError, IndexError):
                    continue
        elif match == "AE.ADBE Opacity":
            pe = _params(g, comp).get(1)
            v = _param_static(pe)
            if isinstance(v, float):
                opacity = v
            kfs = _param_keyframes(pe)
            if kfs:
                keyframes["opacity"] = kfs
        elif match in ("AE.ADBE Text", "AE.ADBE Capsule", "AE.ADBE Shape"):
            pass  # graphic content, handled by _graphic
        elif match in EFFECT_PARAMS:
            effects.append(_effect(g, comp, EFFECT_PARAMS[match], sw, sh))
        else:
            stats["effects"] += 1
            stats.setdefault("effect_names", set()).add(_text(comp, "DisplayName") or match)
    return {"motion": motion, "keyframes": keyframes, "opacity": opacity, "effects": effects}


# Premiere effect MatchName -> (lumina type, {ParameterID: (name, kind)})
EFFECT_PARAMS = {
    "AE.ADBE Geometry2": ("transform", {
        1: ("anchor", "point"), 2: ("position", "point"), 3: ("scale", "number"), 4: ("scale_width", "number"),
        5: ("skew", "number"), 6: ("skew_axis", "number"), 7: ("rotation", "number"), 8: ("opacity", "number"),
        11: ("uniform", "bool"),
    }),
    "AE.ADBE Gaussian Blur 2": ("gaussian_blur", {
        1: ("blurriness", "number"), 2: ("dimensions", "number"), 3: ("repeat_edge", "bool"),
    }),
}


def _effect(g: Graph, comp: ET.Element, spec: tuple, sw: float, sh: float) -> dict:
    """Map a standard effect component; points are normalized to the clip frame in Premiere."""
    kind, params = spec

    def conv(ptype: str, v):
        if ptype == "point":
            return [float(v[0]) * sw, float(v[1]) * sh]
        if ptype == "bool":
            return bool(v)
        return float(v)

    out = {"type": kind, "enabled": _text(comp, "Component/Bypass") != "true", "params": {}, "keyframes": {}}
    for pid, pe in _params(g, comp).items():
        if pid not in params:
            continue
        name, ptype = params[pid]
        try:
            v = _param_static(pe)
            if v is not None:
                out["params"][name] = conv(ptype, v)
            kfs = _param_keyframes(pe)
            if kfs and ptype != "bool":
                out["keyframes"][name] = [{**k, "v": conv(ptype, k["v"])} for k in kfs]
        except (TypeError, IndexError, ValueError):
            continue
    return out


TRANSITION_TYPES = {
    "AE.ADBE Cross Dissolve New": "cross_dissolve", "AE.ADBE Cross Dissolve": "cross_dissolve",
    "AE.AE_Impact_Dissolve": "cross_dissolve", "AE.ADBE Additive Dissolve": "cross_dissolve",
    "AE.ADBE Film Dissolve": "cross_dissolve",
    "AE.ADBE Dip To Black": "dip_to_black", "AE.ADBE Dip To White": "dip_to_white",
    "AE.ADBE Constant Power": "constant_power", "AE.ADBE Constant Gain": "constant_power",
    "AE.ADBE Exponential Fade": "constant_power",
}


def _transition(g: Graph, el: ET.Element | None, side: str, kind: str, stats: dict) -> dict | None:
    if el is None:
        return None
    tti = el.find("TransitionTrackItem")
    if tti is None:
        return None
    start, end = _int(tti, "TrackItem/Start", 0), _int(tti, "TrackItem/End", 0)
    if end <= start:
        return None
    match = _text(tti, "MatchName") or ""
    ttype = TRANSITION_TYPES.get(match)
    if ttype is None:
        ttype = "constant_power" if kind == "audio" else "cross_dissolve"
        stats.setdefault("approx_transitions", set()).add(_text(tti, "DisplayName") or match)
    offset = _int(tti, "Alignment", (end - start) // 2)
    align = "start" if offset <= 0 else "end" if offset >= end - start else "center"
    return {"uid": el.get("ObjectID"), "side": side, "type": ttype, "duration": end - start, "alignment": align,
            "outgoing": _text(tti, "HasOutgoingClip") == "true", "incoming": _text(tti, "HasIncomingClip") == "true"}


def _length_prefixed_strings(blob: bytes) -> list[str]:
    import struct as _st

    found, i, n = [], 0, len(blob)
    while i + 4 <= n:
        (length,) = _st.unpack_from("<I", blob, i)
        if 1 <= length <= 4096 and i + 4 + length <= n:
            chunk = blob[i + 4:i + 4 + length]
            try:
                text = chunk.decode("utf-8")
            except UnicodeDecodeError:
                text = None
            if text and all(ch.isprintable() or ch in "\r\n\t" for ch in text):
                found.append(text)
                i += 4 + length
                continue
        i += 1
    return found


def _font_size(blob: bytes) -> float | None:
    """Best effort: in Premiere's text FlatBuffer the size sits 16 bytes after a
    small negative float marker. Undocumented; may miss on other versions."""
    import struct as _st

    for i in range(0, len(blob) - 20, 4):
        v = _st.unpack_from("<f", blob, i)[0]
        if -60 < v < -1 and abs(v - round(v)) < 1e-4:
            size = _st.unpack_from("<f", blob, i + 16)[0]
            return size if 4 <= size <= 1000 else None
    return None


def _graphic(g: Graph, cti: ET.Element, seq_w: int, seq_h: int, stats: dict) -> dict | None:
    """Text layers from native text components and MOGRT (Capsule) parameters."""
    import base64

    layers: list[dict] = []
    source = None
    for comp in _components(g, cti):
        match = _text(comp, "MatchName")
        params = _params(g, comp)
        if match == "AE.ADBE Text":
            raw = _text(params.get(1), "StartKeyframeValue")
            if not raw:
                continue
            try:
                blob = base64.b64decode(raw)
            except ValueError:
                continue
            strings = _length_prefixed_strings(blob)
            if not strings:
                continue
            font = next((s for s in strings if "-" in s and " " not in s and len(s) < 64), None) or strings[0]
            texts = [s for s in strings if s != font]
            size = _font_size(blob)
            if size is None:
                stats["approx_text"] += 1
            fs = size or round(seq_h / 19)
            pos = _param_static(params.get(3))
            if not isinstance(pos, list):
                pos = [0.5, 0.5]
            layers.append({
                "type": "text", "text": (texts[-1] if texts else "").strip(), "font_family": font, "font_size": fs,
                "align": "left",
                # Premiere puts the baseline at Position; Lumina anchors the text block's vertical center
                "position": [pos[0] * seq_w, pos[1] * seq_h - fs * 0.35],
                "scale": float(_param_static(params.get(4)) or 100.0),
                "rotation": float(_param_static(params.get(7)) or 0.0),
                "opacity": float(_param_static(params.get(8)) if _param_static(params.get(8)) is not None else 100.0),
                "approximate": size is None,
            })
        elif match == "AE.ADBE Capsule":
            source = "mogrt:" + (_text(comp, "Component/DisplayName") or "graphic")
            y = seq_h * 0.12
            for pid in sorted(params):
                raw = _text(params[pid], "StartKeyframeValue")
                if not raw:
                    continue
                try:
                    data = json.loads(base64.b64decode(raw).decode("utf-16-le"))
                except (ValueError, UnicodeDecodeError):
                    continue
                if not isinstance(data, dict) or "textEditValue" not in data:
                    continue
                size = float((data.get("fontSizeEditValue") or [100])[0])
                layers.append({
                    "type": "text", "text": data["textEditValue"],
                    "font_family": (data.get("fontEditValue") or ["Pretendard"])[0],
                    "font_size": size, "bold": bool((data.get("fontFSBoldValue") or [False])[0]),
                    "italic": bool((data.get("fontFSItalicValue") or [False])[0]), "align": "center",
                    # MOGRT layout lives inside the template; stack the fields as an approximation
                    "position": [seq_w / 2, y + size / 2], "approximate": True,
                })
                y += size * 1.25
        elif match == "AE.ADBE Shape":
            stats["shapes"] += 1
    if not layers:
        return None
    defaults = {"bold": False, "italic": False, "color": "#ffffff", "stroke_color": None, "stroke_width": 0.0,
                "background": None, "scale": 100.0, "rotation": 0.0, "opacity": 100.0}
    out_layers = [{**defaults, **layer, "id": f"L{i}"} for i, layer in enumerate(layers)]
    name = next((l["text"] for l in out_layers if l["text"]), "Graphic").splitlines()[0][:40]
    return {"layers": out_layers, "source": source, "name": name}
