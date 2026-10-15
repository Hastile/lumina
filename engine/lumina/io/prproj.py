"""Read Adobe Premiere Pro projects (.prproj) for hand-off into Lumina.

A .prproj is gzip-compressed XML holding an object graph: elements carry
``ObjectID`` (numeric) or ``ObjectUID`` (GUID) and point at each other with
``ObjectRef`` / ``ObjectURef``. The format is undocumented; this reader was
derived from real Premiere 2025-26 projects and is deliberately tolerant:
anything it cannot map is skipped and reported in ``warnings`` instead of
failing the import. The source file is only ever read.

What is imported: the bin tree, file-based media, sequences (frame rate, frame
size, video/audio tracks, clip placement, source in, speed, link groups,
label colors, track mute), sequence markers and the playhead, Motion/Opacity
and their keyframes, Transform/Gaussian Blur effects, transitions, native
text and shape layers and MOGRT text fields. Not imported yet: other effects,
captions and nested sequences (each is listed in ``warnings``).

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
    _blobs: dict[str, str] | None = None
    path: str | None = None
    _mogrts: dict | None = None

    def mogrt(self, media: ET.Element | None) -> dict | None:
        """Text layout of an After Effects template referenced by a Media element."""
        if media is None:
            return None
        from .aep import read_mogrt

        if self._mogrts is None:
            self._mogrts = {}
        cands = []
        if fp := _text(media, "FilePath"):
            cands.append(Path(fp))
        if (rp := _text(media, "RelativePath")) and self.path:
            cands.append((Path(self.path).parent / rp.replace("\\", "/")))
        for c in cands:
            key = str(c)
            if key not in self._mogrts:
                self._mogrts[key] = read_mogrt(key) if c.suffix.lower() in (".aegraphic", ".mogrt") else None
            if self._mogrts[key]:
                return self._mogrts[key]
        return None

    def blob(self, el: ET.Element | None) -> str | None:
        """Base64 text of a binary value. Premiere writes identical blobs once
        and leaves later copies empty with the same ``BinaryHash``."""
        if el is None:
            return None
        if (el.text or "").strip():
            return el.text.strip()
        h = el.get("BinaryHash")
        if not h:
            return None
        if self._blobs is None:
            self._blobs = {}
            for e in self.root.iter():
                k = e.get("BinaryHash")
                if k and k not in self._blobs and (e.text or "").strip():
                    self._blobs[k] = e.text.strip()
        return self._blobs.get(h)

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
    return Graph(root, by_id, by_uid, path=str(p))


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
        parts.append(f"{stats['shapes']} unreadable shape layer(s)")
    if stats.get("shape_anim"):
        parts.append(f"animation on {stats['shape_anim']} shape layer(s) (imported at their first value)")
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
            # t, value, interpolation (0 linear, 4 hold, 5 Bezier; 1 seen as hold too), ?, in speed, in influence,
            # out speed, out influence[, 5, 4, in tangent x, y, out tangent x, y] (point values)
            interp = "linear" if f[2] == "0" else ("hold" if f[2] in ("1", "4") else "bezier")
            k = {"t": int(f[0]), "v": _parse_value(f[1]), "interp": interp}
            if len(f) >= 8:
                k["ease_in"] = [float(f[4]), float(f[5])]
                k["ease_out"] = [float(f[6]), float(f[7])]
            if len(f) >= 14:
                k["tangent_in"] = [float(f[10]), float(f[11])]
                k["tangent_out"] = [float(f[12]), float(f[13])]
            kfs.append(k)
        except ValueError:
            continue
    return sorted(kfs, key=lambda k: k["t"])


def _point_keyframes(kfs: list[dict], sx: float, sy: float) -> list[dict]:
    """Keyframes of a normalized point param in pixels: values and tangents scale
    per axis; speeds (normalized units/s along the path) scale by the adjacent
    segment's pixel/normalized length ratio."""
    import math

    out = [{**k, "v": [k["v"][0] * sx, k["v"][1] * sy]} for k in kfs]
    for k in out:
        for key in ("tangent_in", "tangent_out"):
            if k.get(key):
                k[key] = [k[key][0] * sx, k[key][1] * sy]

    def ratio(i: int, j: int) -> float:
        a, b = kfs[i]["v"], kfs[j]["v"]
        n = math.dist(a, b)
        return math.dist(out[i]["v"], out[j]["v"]) / n if n > 1e-12 else max(sx, sy)

    for i, k in enumerate(out):
        if k.get("ease_in") and i > 0:
            k["ease_in"] = [k["ease_in"][0] * ratio(i - 1, i), k["ease_in"][1]]
        if k.get("ease_out") and i + 1 < len(out):
            k["ease_out"] = [k["ease_out"][0] * ratio(i, i + 1), k["ease_out"][1]]
    return out


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
                    if kfs and name in ("position", "anchor"):
                        keyframes[name] = _point_keyframes(kfs, *((seq_w, seq_h) if name == "position" else (sw, sh)))
                    elif kfs and name != "uniform":
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
            if kfs and ptype == "point":
                out["keyframes"][name] = _point_keyframes(kfs, sw, sh)
            elif kfs and ptype != "bool":
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


def _fb_field(d: bytes, table: int, field: int) -> int | None:
    """Absolute offset of a FlatBuffers table field, or None when absent."""
    import struct as _st

    vt = table - _st.unpack_from("<i", d, table)[0]
    vsize = _st.unpack_from("<H", d, vt)[0]
    if 4 + 2 * field + 2 > vsize:
        return None
    off = _st.unpack_from("<H", d, vt + 4 + 2 * field)[0]
    return table + off if off else None


def _fb_ref(d: bytes, at: int | None) -> int | None:
    import struct as _st

    return None if at is None else at + _st.unpack_from("<I", d, at)[0]


def _fb_u32(d: bytes, at: int | None, default=None):
    import struct as _st

    return default if at is None else _st.unpack_from("<I", d, at)[0]


def _fb_f32(d: bytes, at: int | None, default=None):
    import struct as _st

    return default if at is None else _st.unpack_from("<f", d, at)[0]


def _fb_color(d: bytes, at: int | None) -> str | None:
    t = _fb_ref(d, at)
    if t is None:
        return None
    return "#%02x%02x%02x" % tuple(d[o] if (o := _fb_field(d, t, i)) is not None else 0 for i in range(3))


def _fb_string(d: bytes, at: int | None) -> str:
    v = _fb_ref(d, at)
    if v is None:
        return ""
    n = _fb_u32(d, v)
    return d[v + 4:v + 4 + n].decode("utf-8", "replace")


def _fb_vector(d: bytes, at: int | None) -> list[int]:
    """Absolute offsets of a vector-of-tables' elements."""
    v = _fb_ref(d, at)
    if v is None:
        return []
    return [_fb_ref(d, v + 4 + 4 * i) for i in range(_fb_u32(d, v))]


# Premiere's text FlatBuffer (12-byte header, root -> document table). Field
# numbers mapped from real Premiere 2025-26 projects against frames rendered by
# Premiere itself:
#   document: 0 runs, 1 font names, 2/3 area box w/h, 4 alignment (absent = left,
#             1 right, 2 center), 5 vertical alignment (area text: 1 center,
#             2 bottom), 10 shadow color, 11 shadow on, 12 shadow opacity,
#             14 shadow distance, 15 shadow blur
#   run: 0 text (bytes), 1 style
#   style: 0 font index, 1 size (absent = 100), 2 fill color (absent = white),
#          4 stroke color (absent = white), 6 stroke width, 8 tracking (1/1000 em)
TEXT_ALIGN = {None: "left", 0: "left", 1: "right", 2: "center"}
TEXT_VALIGN = {None: "top", 0: "top", 1: "center", 2: "bottom"}


def _text_doc(blob: bytes) -> dict | None:
    import struct as _st

    try:
        root = 12 + _st.unpack_from("<I", blob, 12)[0]
        doc = _fb_ref(blob, _fb_field(blob, root, 0))
        if doc is None:
            return None
        F = lambda i: _fb_field(blob, doc, i)
        fv = _fb_ref(blob, F(1))
        fonts_ = [_fb_string(blob, fv + 4 + 4 * i) for i in range(_fb_u32(blob, fv))] if fv is not None else []
        runs = []
        for r in _fb_vector(blob, F(0)):
            text = _fb_string(blob, _fb_field(blob, r, 0))
            st = _fb_ref(blob, _fb_field(blob, r, 1))
            S = (lambda i: _fb_field(blob, st, i)) if st is not None else (lambda i: None)
            font_idx = _fb_u32(blob, S(0), 0)
            runs.append({
                "text": text,
                "font_family": fonts_[font_idx] if font_idx < len(fonts_) else (fonts_[0] if fonts_ else None),
                "font_size": float(_fb_f32(blob, S(1), 100.0)),
                "color": _fb_color(blob, S(2)) or "#ffffff",
                "stroke_color": (_fb_color(blob, S(4)) or "#ffffff") if (_fb_f32(blob, S(6), 0.0) or 0) > 0 else None,
                "stroke_width": float(_fb_f32(blob, S(6), 0.0) or 0.0),
                "tracking": float(_fb_f32(blob, S(8), 0.0) or 0.0),
            })
        if not runs:
            return None
        box = [float(_fb_f32(blob, F(2))), float(_fb_f32(blob, F(3)))] if F(2) is not None and F(3) is not None else None
        shadow = None
        if F(11) is not None and blob[F(11)]:
            shadow = {"color": _fb_color(blob, F(10)) or "#000000", "opacity": float(_fb_f32(blob, F(12), 75.0)),
                      "angle": 135.0, "distance": float(_fb_f32(blob, F(14), 10.0)), "blur": float(_fb_f32(blob, F(15), 0.0))}
        return {"runs": runs, "box": box, "align": TEXT_ALIGN.get(_fb_u32(blob, F(4)), "left"),
                "vertical_align": TEXT_VALIGN.get(_fb_u32(blob, F(5)), "top"), "shadow": shadow}
    except (_st.error, IndexError, UnicodeDecodeError):
        return None


def _native_text(blob: bytes, params: dict[int, ET.Element], seq_w: int, seq_h: int) -> dict | None:
    doc = _text_doc(blob)
    if doc is None:
        return None
    runs = doc["runs"]
    first = runs[0]
    layer = {
        "type": "text", "text": "".join(r["text"] for r in runs).replace("\r\n", "\n").replace("\r", "\n").rstrip("\n"),
        "font_family": first["font_family"] or "Pretendard", "font_size": first["font_size"], "bold": False, "italic": False,
        "color": first["color"], "stroke_color": first["stroke_color"], "stroke_width": first["stroke_width"],
        "tracking": first["tracking"], "background": None, "align": doc["align"], "box": doc["box"],
        "vertical_align": doc["vertical_align"], "shadow": doc["shadow"],
        "runs": [{k: v for k, v in r.items()} for r in runs] if len(runs) > 1 else None,
        "scale": float(_param_static(params.get(4)) or 100.0),
        "rotation": float(_param_static(params.get(7)) or 0.0),
        "opacity": float(_param_static(params.get(8)) if _param_static(params.get(8)) is not None else 100.0),
        "approximate": False,
    }
    if layer["runs"]:
        layer["runs"][-1]["text"] = layer["runs"][-1]["text"].rstrip("\r\n")
    pos = _param_static(params.get(3))
    if not isinstance(pos, list):
        pos = [0.5, 0.5]
    x, y = pos[0] * seq_w, pos[1] * seq_h
    if layer["box"] is None:
        # Premiere puts the first baseline at Position; Lumina anchors the block's vertical center
        from ..render.text import layout

        lines, _, bh = layout(layer)
        y = y - (lines[0].baseline if lines else 0.0) + bh / 2
    layer["position"] = [x, y]
    return layer


def _shape_style(blob: bytes) -> dict:
    """Fill color and kind from the Shape component's style FlatBuffer
    (12-byte header, then root -> style table). Field numbers were mapped from
    real Premiere 2025-26 projects: 0 = fill color table (r, g, b bytes),
    23 = kind (1 rectangle, 2 ellipse), 24/25 = width/height."""
    import struct as _st

    out: dict = {}
    try:
        root = 12 + _st.unpack_from("<I", blob, 12)[0]
        style = _fb_ref(blob, _fb_field(blob, root, 0))
        if style is None:
            return out
        color = _fb_ref(blob, _fb_field(blob, style, 0))
        if color is not None:
            rgb = [blob[o] if (o := _fb_field(blob, color, i)) is not None else 0 for i in range(3)]
            out["fill"] = "#%02x%02x%02x" % tuple(rgb)
        kind = _fb_field(blob, style, 23)
        if kind is not None:
            out["shape"] = {1: "rectangle", 2: "ellipse"}.get(_st.unpack_from("<I", blob, kind)[0], "path")
    except (_st.error, IndexError):
        pass
    return out


def _shape(g: Graph, params: dict[int, ET.Element], seq_w: int, seq_h: int, stats: dict) -> dict | None:
    """One Essential Graphics shape layer. Param 1 is the path ("u32 version,
    u32 count, count x (u32 flags, 6 floats: point, in, out), u8 closed"),
    param 2 the style; 4-10 the layer transform, points normalized to the frame."""
    import base64
    import struct as _st

    def blob(pid: int) -> bytes | None:
        raw = g.blob(params[pid].find("StartKeyframeValue")) if pid in params else None
        try:
            return base64.b64decode(raw) if raw else None
        except ValueError:
            return None

    path_blob = blob(1)
    if not path_blob or len(path_blob) < 8:
        return None
    try:
        _, count = _st.unpack_from("<II", path_blob, 0)
        path = [list(_st.unpack_from("<6f", path_blob, 8 + 28 * i + 4)) for i in range(count)]
    except _st.error:
        return None
    if len(path) < 2:
        return None
    end = 8 + 28 * count
    closed = path_blob[end] != 0 if end < len(path_blob) else True
    style = _shape_style(blob(2) or b"")
    if any(_text(params.get(pid), "Keyframes") for pid in (1, 4, 5, 6, 8, 9, 10)):
        stats["shape_anim"] = stats.get("shape_anim", 0) + 1

    def static(pid: int, default):
        v = _param_static(params.get(pid))
        return default if v is None else v

    pos = static(4, [0.5, 0.5])
    anchor = static(10, [0.0, 0.0])
    if not isinstance(pos, list):
        pos = [0.5, 0.5]
    if not isinstance(anchor, list):
        anchor = [0.0, 0.0]
    return {
        "type": "shape", "shape": style.get("shape", "path"), "path": [[round(v, 4) for v in vtx] for vtx in path],
        "closed": closed, "fill": style.get("fill", "#ffffff"), "stroke_color": None, "stroke_width": 0.0,
        "position": [pos[0] * seq_w, pos[1] * seq_h], "anchor": [anchor[0] * seq_w, anchor[1] * seq_h],
        "scale": float(static(5, 100.0)), "scale_width": float(static(6, 100.0)), "uniform": bool(static(7, True)),
        "rotation": float(static(8, 0.0)), "opacity": float(static(9, 100.0)),
    }


def _mogrt_fields(g: Graph, params: dict[int, ET.Element]) -> dict[str, dict]:
    """Capsule params by field name -> the edited values (text, font, size, faux styles)."""
    import base64

    out = {}
    for pid in sorted(params):
        raw = g.blob(params[pid].find("StartKeyframeValue")) or _text(params[pid], "StartKeyframeValue")
        if not raw:
            continue
        try:
            data = json.loads(base64.b64decode(raw).decode("utf-16-le"))
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(data, dict) and "textEditValue" in data:
            out[_text(params[pid], "Name") or str(pid)] = data
    return out


def _mogrt_layers(ae: dict, fields: dict[str, dict], seq_w: int, seq_h: int) -> list[dict]:
    """Lumina text layers (bottom first) from an AE template's text layers with
    Premiere's edited field values applied. AE puts a point-text layer's first
    baseline at its origin (position - anchor); the comp is centered in the frame."""
    from ..render.text import layout

    cw, ch = ae.get("width") or seq_w, ae.get("height") or seq_h
    ox, oy = (seq_w - cw) / 2, (seq_h - ch) / 2
    out = []
    for al in reversed(ae["layers"]):
        f = fields.get(al.get("name") or "", {})
        first = lambda key, default: (f.get(key) or [default])[0]
        text = f.get("textEditValue", al.get("text", ""))
        layer = {
            "type": "text", "text": text.replace("\r\n", "\n").replace("\r", "\n"),
            "font_family": first("fontEditValue", al.get("font_family") or "Pretendard"),
            "font_size": float(first("fontSizeEditValue", al.get("font_size") or 100.0)),
            "bold": bool(first("fontFSBoldValue", False)) or bool(al.get("bold")),
            "italic": bool(first("fontFSItalicValue", False)) or bool(al.get("italic")),
            "color": al.get("color") or "#ffffff", "stroke_color": al.get("stroke_color"),
            "stroke_width": float(al.get("stroke_width") or 0.0), "tracking": float(al.get("tracking") or 0.0),
            "background": None, "align": al.get("align", "left"), "box": None, "vertical_align": "top",
            "shadow": al.get("shadow"), "runs": None, "scale": float(al.get("scale") or 100.0),
            "rotation": float(al.get("rotation") or 0.0), "opacity": float(al.get("opacity") if al.get("opacity") is not None else 100.0),
            "visible": True, "approximate": False,
        }
        px, py = al.get("position") or [cw / 2, ch / 2]
        ax, ay = al.get("anchor") or [0.0, 0.0]
        s = layer["scale"] / 100
        bx, by = ox + px - ax * s, oy + py - ay * s  # the layer origin = first baseline at the alignment point
        lines, _, bh = layout(layer)
        layer["position"] = [bx, by + (-(lines[0].baseline if lines else 0.0) + bh / 2) * s]
        out.append(layer)
    return out


def _graphic(g: Graph, cti: ET.Element, seq_w: int, seq_h: int, stats: dict) -> dict | None:
    """Text and shape layers from native Essential Graphics components, and
    text fields from MOGRT (Capsule) parameters."""
    import base64

    layers: list[dict] = []  # MOGRT fields, in template order
    native: list[dict] = []  # Text/Shape components, top-most first as Premiere lists them
    source = None
    for comp in _components(g, cti):
        match = _text(comp, "MatchName")
        params = _params(g, comp)
        bypassed = _text(comp, "Component/Bypass") == "true"  # the layer's eye toggle in Essential Graphics
        if match == "AE.ADBE Text":
            raw = g.blob(params[1].find("StartKeyframeValue")) if 1 in params else None
            if not raw:
                continue
            try:
                blob = base64.b64decode(raw)
            except ValueError:
                continue
            parsed = _native_text(blob, params, seq_w, seq_h)
            if parsed is not None:
                parsed["visible"] = not bypassed
                native.append(parsed)
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
            native.append({
                "type": "text", "text": (texts[-1] if texts else "").strip(), "font_family": font, "font_size": fs,
                "align": "left",
                # Premiere puts the baseline at Position; Lumina anchors the text block's vertical center
                "position": [pos[0] * seq_w, pos[1] * seq_h - fs * 0.35],
                "scale": float(_param_static(params.get(4)) or 100.0),
                "rotation": float(_param_static(params.get(7)) or 0.0),
                "opacity": float(_param_static(params.get(8)) if _param_static(params.get(8)) is not None else 100.0),
                "approximate": size is None, "visible": not bypassed,
            })
        elif match == "AE.ADBE Capsule":
            source = "mogrt:" + (_text(comp, "Component/DisplayName") or "graphic")
            sub = g.at(cti, "SubClip")
            _, media = _clip_source(g, g.at(sub, "Clip"))
            ae = g.mogrt(media)
            if ae and ae["layers"]:
                fields = _mogrt_fields(g, params)
                layers.extend(_mogrt_layers(ae, fields, seq_w, seq_h))
                continue
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
            shape = _shape(g, params, seq_w, seq_h, stats)
            if shape is None:
                stats["shapes"] += 1
            else:
                shape["visible"] = not bypassed
                native.append(shape)
    layers = native[::-1] + layers
    if not layers:
        return None
    defaults = {"bold": False, "italic": False, "color": "#ffffff", "stroke_color": None, "stroke_width": 0.0,
                "background": None, "scale": 100.0, "rotation": 0.0, "opacity": 100.0, "visible": True}
    out_layers = [{**(defaults if layer["type"] == "text" else {}), **layer, "id": f"L{i}"} for i, layer in enumerate(layers)]
    name = next((l["text"] for l in out_layers if l.get("text")), "Graphic").splitlines()[0][:40]
    return {"layers": out_layers, "source": source, "name": name}
