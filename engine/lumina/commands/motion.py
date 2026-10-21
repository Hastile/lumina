"""Motion, opacity/volume keyframing and graphics (text and shape layers)."""

from __future__ import annotations

import copy
from typing import Literal

from pydantic import Field

from .. import shapes, timebase
from ..model import KeyframeProp, new_id
from ..motion import POINT_PROPS, source_time, value_at
from .project import seq_of
from .registry import CommandError, Context, Params, TimeValue, command, resolve_time
from .timeline import clip_path, find_clip, require_unlocked, write_clip

Value = float | list[float]


def _check_value(prop: str, v: Value) -> Value:
    if prop in POINT_PROPS:
        if not isinstance(v, list) or len(v) != 2:
            raise CommandError("BAD_VALUE", f"{prop} needs [x, y]")
        return [float(v[0]), float(v[1])]
    if isinstance(v, list):
        raise CommandError("BAD_VALUE", f"{prop} needs a number")
    v = float(v)
    if prop == "opacity" and not 0 <= v <= 100:
        raise CommandError("BAD_VALUE", "opacity is 0-100")
    if prop.startswith("crop_") and not 0 <= v <= 100:
        raise CommandError("BAD_VALUE", "crop is 0-100 %")
    if prop in ("scale", "scale_width") and not 0 <= v <= 10000:
        raise CommandError("BAD_VALUE", "scale is 0-10000 %")
    return v


def _ctx(ctx: Context, sequence_id: str | None, clip_id: str):
    seq = seq_of(ctx.doc, sequence_id)
    tid, clip = find_clip(seq, clip_id)
    require_unlocked(seq, tid)
    media = ctx.doc["media"].get(clip["media_id"]) if clip["media_id"] else None
    return seq, tid, clip, media


def no_caption_transform(clip: dict, prop: str | None = None) -> None:
    """Caption cues take their track's transform (Premiere): position, scale, rotation and
    opacity are set on the caption track (caption.set_style), never per cue."""
    if clip.get("caption") is not None and prop != "volume_db":
        raise CommandError("CAPTION_TRANSFORM", "a caption cue uses its track's transform: change it on the caption track "
                                                "(caption.set_style position/scale/rotation/opacity)")


def _write_static(ctx: Context, seq: dict, tid: str, clip: dict, prop: str, v: Value) -> None:
    path = clip_path(seq, tid, clip["id"])
    if prop in ("opacity", "volume_db"):
        ctx.m.set(path + [prop], v)
    else:
        ctx.m.set(path + ["motion", prop], v)


def _at(seq: dict, clip: dict, at: TimeValue | None) -> int:
    t = seq["playhead"] if at is None else resolve_time(at, seq["settings"])
    return min(max(t, clip["start"]), clip["end"] - 1)


class SetProperty(Params):
    sequence_id: str | None = None
    clip_id: str
    prop: KeyframeProp
    value: Value
    at: TimeValue | None = Field(default=None, description="sequence time used when the property is animated; default playhead")


@command("clip.set_property", SetProperty)
def clip_set_property(ctx: Context, p: SetProperty):
    """Set a Motion/Opacity/Volume property. If it is animated, adds or updates the keyframe at `at` (like Premiere)."""
    seq, tid, clip, _ = _ctx(ctx, p.sequence_id, p.clip_id)
    no_caption_transform(clip, p.prop)
    v = _check_value(p.prop, p.value)
    kfs = clip["keyframes"].get(p.prop)
    if not kfs:
        _write_static(ctx, seq, tid, clip, p.prop, v)
        return {"animated": False}
    t = source_time(clip, _at(seq, clip, p.at))
    old = next((k for k in kfs if k["t"] == t), None)
    new = [k for k in kfs if k["t"] != t] + [{**old, "v": v} if old else {"t": t, "v": v, "interp": _interp_near(kfs, t)}]
    ctx.m.set(clip_path(seq, tid, clip["id"]) + ["keyframes", p.prop], sorted(new, key=lambda k: k["t"]))
    return {"animated": True, "keyframe_t": t}


INTERPS = ("linear", "ease", "hold", "bezier", "auto_bezier", "continuous_bezier")
HANDLE_KEYS = ("ease_in", "ease_out", "tangent_in", "tangent_out")


def handles_of(k: dict) -> dict:
    """Bezier handle fields of a user-supplied keyframe ([speed, influence] / [dx, dy])."""
    out = {}
    for key in HANDLE_KEYS:
        v = k.get(key)
        if v is not None:
            if not isinstance(v, list) or len(v) != 2:
                raise CommandError("BAD_VALUE", f"{key} needs two numbers")
            out[key] = [float(v[0]), float(v[1])]
    return out


def with_interp(k: dict, interp: str) -> dict:
    """Changing to Easy Ease / Auto Bezier resets the temporal handles; other types keep them."""
    out = dict(k, interp=interp)
    if interp in ("ease", "auto_bezier"):
        out.pop("ease_in", None)
        out.pop("ease_out", None)
    return out


# Effect Controls' keyframe context menu (Premiere's Temporal Interpolation)
MENU_INTERPS = ("linear", "bezier", "auto_bezier", "continuous_bezier", "hold", "ease_in", "ease_out", "ease")


def set_interp(kfs: list[dict], times: set[int], interp: str) -> list[dict]:
    """Change the interpolation of the keyframes at ``times``, keeping the curve's current shape
    where the new type has handles (Bezier, Continuous Bezier, the untouched side of Ease In/Out)."""
    out = [dict(k) for k in kfs]
    for i, k in enumerate(out):
        if k["t"] not in times:
            continue
        hin, hout = effective_handle(kfs, i, "in"), effective_handle(kfs, i, "out")
        if interp == "linear":
            k["interp"] = "linear"
            k.pop("ease_in", None)
            k.pop("ease_out", None)
        elif interp == "hold":
            k["interp"] = "hold"
        elif interp == "bezier":
            k.update(interp="bezier", ease_in=hin, ease_out=hout)
        elif interp == "continuous_bezier":
            speed = (hin[0] + hout[0]) / 2
            k.update(interp="continuous_bezier", ease_in=[speed, hin[1]], ease_out=[speed, hout[1]])
        elif interp == "ease_in":
            k.update(interp="bezier", ease_in=list(EASY), ease_out=hout)
        elif interp == "ease_out":
            k.update(interp="bezier", ease_in=hin, ease_out=list(EASY))
        else:  # ease, auto_bezier
            out[i] = with_interp(k, interp)
    return out


EASY = (0.0, 1 / 3)


def effective_handle(kfs: list[dict], i: int, side: str) -> list[float]:
    """[speed, influence] a keyframe's side behaves as now: its Bezier handle, Easy Ease, or for a
    linear side the handle that draws the same straight segment (segment speed, 1/3)."""
    from ..motion import EASY_EASE

    from ..motion import auto_speed

    k = kfs[i]
    if k.get("interp") == "auto_bezier":
        return [auto_speed(kfs, i), 1 / 3]
    if k.get("interp") in ("bezier", "ease", "continuous_bezier"):
        h = k.get("ease_" + side)
        if h:
            return [float(h[0]), float(h[1])]
        return [auto_speed(kfs, i), 1 / 3] if k["interp"] == "continuous_bezier" else list(EASY_EASE)
    j = i + 1 if side == "out" else i - 1
    if j < 0 or j >= len(kfs):
        return [0.0, 1 / 3]
    n = kfs[j]
    dt = abs(n["t"] - k["t"]) / timebase.TICKS_PER_SECOND
    if dt <= 0:
        return [0.0, 1 / 3]
    if isinstance(k["v"], list):
        import math
        return [math.dist(k["v"], n["v"]) / dt, 1 / 3]
    dv = (n["v"] - k["v"]) if side == "out" else (k["v"] - n["v"])
    return [dv / dt, 1 / 3]


def with_ease(kfs: list[dict], t: int, side: str, speed: float, influence: float) -> list[dict]:
    """Set one Bezier handle (Effect Controls' value/velocity graphs). The keyframe becomes Bezier;
    its other side keeps its current shape."""
    i = next((n for n, k in enumerate(kfs) if k["t"] == t), None)
    if i is None:
        raise CommandError("NO_KEYFRAME", "no keyframe at that time")
    out = [dict(k) for k in kfs]
    k = out[i]
    for sd in ("in", "out"):
        k["ease_" + sd] = effective_handle(kfs, i, sd)
    k["ease_" + side] = [float(speed), min(1.0, max(0.01, float(influence)))]
    if kfs[i].get("interp") in ("auto_bezier", "continuous_bezier"):
        # like Premiere: dragging an Auto/Continuous Bezier handle keeps both sides at one speed
        other = "in" if side == "out" else "out"
        k["ease_" + other] = [float(speed), k["ease_" + other][1]]
        k["interp"] = "continuous_bezier"
    else:
        k["interp"] = "bezier"
    return out


class KeyframeEase(Params):
    sequence_id: str | None = None
    clip_id: str
    prop: KeyframeProp
    t: int = Field(description="the keyframe's time (source ticks, as stored)")
    side: Literal["in", "out"]
    speed: float = Field(description="value units per second (points: pixels per second along the path)")
    influence: float = Field(ge=0, le=1, description="fraction of the segment, 0-1")


@command("clip.set_keyframe_ease", KeyframeEase)
def clip_set_keyframe_ease(ctx: Context, p: KeyframeEase):
    """Bezier handle of a Motion/Opacity/Volume keyframe: speed and influence on one side."""
    seq, tid, clip, _ = _ctx(ctx, p.sequence_id, p.clip_id)
    no_caption_transform(clip, p.prop)
    kfs = clip["keyframes"].get(p.prop)
    if not kfs:
        raise CommandError("NOT_ANIMATED", f"{p.prop} has no keyframes")
    ctx.m.set(clip_path(seq, tid, clip["id"]) + ["keyframes", p.prop], with_ease(kfs, p.t, p.side, p.speed, p.influence))


def _interp_near(kfs: list[dict], t: int) -> str:
    return min(kfs, key=lambda k: abs(k["t"] - t))["interp"] if kfs else "linear"


class ToggleAnimation(Params):
    sequence_id: str | None = None
    clip_id: str
    prop: KeyframeProp
    at: TimeValue | None = None


@command("clip.toggle_animation", ToggleAnimation)
def clip_toggle_animation(ctx: Context, p: ToggleAnimation):
    """The stopwatch: start animating (keyframe at `at` with the current value) or stop (drop keyframes, keep value at `at`)."""
    seq, tid, clip, media = _ctx(ctx, p.sequence_id, p.clip_id)
    no_caption_transform(clip, p.prop)
    at = _at(seq, clip, p.at)
    path = clip_path(seq, tid, clip["id"])
    current = value_at(clip, p.prop, at, seq, media)
    if clip["keyframes"].get(p.prop):
        ctx.m.delete(path + ["keyframes", p.prop])
        _write_static(ctx, seq, tid, clip, p.prop, current)
        return {"animated": False}
    ctx.m.set(path + ["keyframes", p.prop], [{"t": source_time(clip, at), "v": current, "interp": "linear"}])
    return {"animated": True}


class KeyframeOp(Params):
    sequence_id: str | None = None
    clip_id: str
    prop: KeyframeProp
    at: TimeValue | None = None
    op: Literal["add", "remove", "toggle"] = "toggle"
    interp: Literal["linear", "ease", "hold", "bezier", "auto_bezier", "continuous_bezier"] | None = None


@command("clip.keyframe", KeyframeOp)
def clip_keyframe(ctx: Context, p: KeyframeOp):
    """Add / remove / toggle a keyframe at a time (the diamond button), or change its interpolation."""
    seq, tid, clip, media = _ctx(ctx, p.sequence_id, p.clip_id)
    no_caption_transform(clip, p.prop)
    at = _at(seq, clip, p.at)
    t = source_time(clip, at)
    tol = round(timebase.ticks_per_frame(seq["settings"]["fps_num"], seq["settings"]["fps_den"]) * clip["speed"] / 2)
    kfs = list(clip["keyframes"].get(p.prop) or [])
    hit = next((k for k in kfs if abs(k["t"] - t) <= tol), None)
    path = clip_path(seq, tid, clip["id"]) + ["keyframes", p.prop]
    if hit and p.op in ("remove", "toggle") and p.interp is None:
        kfs.remove(hit)
        if kfs:
            ctx.m.set(path, kfs)
        else:
            _write_static(ctx, seq, tid, clip, p.prop, hit["v"])
            ctx.m.delete(path)
        return {"removed": True}
    if hit and p.interp:
        ctx.m.set(path, [with_interp(k, p.interp) if k is hit else k for k in kfs])
        return {"interp": p.interp}
    if p.op == "remove":
        raise CommandError("NO_KEYFRAME", "no keyframe at that time")
    v = value_at(clip, p.prop, at, seq, media)
    kfs.append({"t": t, "v": v, "interp": p.interp or _interp_near(kfs, t)})
    ctx.m.set(path, sorted(kfs, key=lambda k: k["t"]))
    return {"added": True, "t": t}


class SetKeyframes(Params):
    sequence_id: str | None = None
    clip_id: str
    prop: KeyframeProp
    keyframes: list[dict] = Field(description="[{t: clip-relative time (ticks or '1.5s'), v, interp}] - replaces all; [] stops animating")


@command("clip.set_keyframes", SetKeyframes)
def clip_set_keyframes(ctx: Context, p: SetKeyframes):
    """Replace a property's keyframes in one go (times are relative to the clip's start)."""
    seq, tid, clip, media = _ctx(ctx, p.sequence_id, p.clip_id)
    no_caption_transform(clip, p.prop)
    path = clip_path(seq, tid, clip["id"]) + ["keyframes", p.prop]
    if not p.keyframes:
        if clip["keyframes"].get(p.prop):
            _write_static(ctx, seq, tid, clip, p.prop, value_at(clip, p.prop, clip["start"], seq, media))
            ctx.m.delete(path)
        return {"count": 0}
    out = []
    for k in p.keyframes:
        rel = resolve_time(k["t"], seq["settings"])
        out.append({"t": source_time(clip, clip["start"] + rel), "v": _check_value(p.prop, k["v"]),
                    "interp": k.get("interp", "linear"), **handles_of(k)})
        if out[-1]["interp"] not in INTERPS:
            raise CommandError("BAD_VALUE", "interp is linear | ease | hold | bezier | auto_bezier | continuous_bezier")
    ctx.m.set(path, sorted(out, key=lambda k: k["t"]))
    return {"count": len(out)}


class ResetMotion(Params):
    sequence_id: str | None = None
    clip_ids: list[str] = Field(min_length=1)


@command("clip.reset_motion", ResetMotion)
def clip_reset_motion(ctx: Context, p: ResetMotion):
    """Reset Motion and Opacity to defaults and remove their keyframes."""
    for cid in p.clip_ids:
        seq, tid, clip, _ = _ctx(ctx, p.sequence_id, cid)
        no_caption_transform(clip)
        path = clip_path(seq, tid, cid)
        ctx.m.set(path + ["motion"], {"position": None, "scale": 100.0, "scale_width": 100.0, "uniform": True, "rotation": 0.0,
                                      "anchor": None, "crop_left": 0.0, "crop_top": 0.0, "crop_right": 0.0, "crop_bottom": 0.0})
        ctx.m.set(path + ["opacity"], 100.0)
        kept = {k: v for k, v in clip["keyframes"].items() if k == "volume_db"}
        if kept != clip["keyframes"]:
            ctx.m.set(path + ["keyframes"], kept)


class SetUniform(Params):
    sequence_id: str | None = None
    clip_id: str
    uniform: bool


@command("clip.set_uniform_scale", SetUniform)
def clip_set_uniform_scale(ctx: Context, p: SetUniform):
    """Uniform Scale checkbox."""
    seq, tid, clip, _ = _ctx(ctx, p.sequence_id, p.clip_id)
    no_caption_transform(clip, None)
    ctx.m.set(clip_path(seq, tid, clip["id"]) + ["motion", "uniform"], p.uniform)


class FitToFrame(Params):
    sequence_id: str | None = None
    clip_ids: list[str] = Field(min_length=1)
    mode: Literal["fit", "fill"] = "fit"


@command("clip.fit_to_frame", FitToFrame)
def clip_fit_to_frame(ctx: Context, p: FitToFrame):
    """Set to Frame Size (fit) / Scale to fill (fill): centers the clip and sets Scale."""
    for cid in p.clip_ids:
        seq, tid, clip, media = _ctx(ctx, p.sequence_id, cid)
        no_caption_transform(clip)
        if not media or not media.get("video"):
            continue
        st = seq["settings"]
        w, h = media["video"]["width"], media["video"]["height"]
        f = (min if p.mode == "fit" else max)(st["width"] / w, st["height"] / h)
        path = clip_path(seq, tid, cid) + ["motion"]
        ctx.m.set(path + ["position"], None)
        ctx.m.set(path + ["anchor"], None)
        ctx.m.set(path + ["uniform"], True)
        ctx.m.set(path + ["scale"], round(f * 100, 2))
        if clip["keyframes"].get("scale"):
            ctx.m.delete(clip_path(seq, tid, cid) + ["keyframes", "scale"])


# ---------------------------------------------------------------- graphics

def _text_layer(text: str, position: list[float], **style) -> dict:
    layer = {"id": new_id(), "type": "text", "text": text, "font_family": "Pretendard", "font_size": 100.0,
             "bold": False, "italic": False, "color": "#ffffff", "stroke_color": None, "stroke_width": 0.0,
             "background": None, "align": "center", "position": position, "box": None, "vertical_align": "top",
             "tracking": 0.0, "shadow": None, "runs": None, "scale": 100.0, "rotation": 0.0,
             "opacity": 100.0, "visible": True, "approximate": False}
    for k, v in style.items():
        if v is not None:
            layer[k] = v
    return layer


def graphic_clip(name: str, start: int, end: int, layers: list[dict], source: str | None = None, label: str = "rose") -> dict:
    return {
        "id": new_id(), "media_id": None, "name": name, "start": start, "end": end, "source_in": 0, "speed": 1.0,
        "enabled": True, "link_id": None, "label": label, "opacity": 100.0, "volume_db": 0.0, "gain_db": 0.0, "audio_tag": None, "sequence_id": None, "muted": False, "auto_match": None,
        "motion": {"position": None, "scale": 100.0, "scale_width": 100.0, "uniform": True, "rotation": 0.0, "anchor": None,
                   "crop_left": 0.0, "crop_top": 0.0, "crop_right": 0.0, "crop_bottom": 0.0},
        "keyframes": {}, "graphic": {"layers": layers, "source": source}, "caption": None, "effects": [], "freeze": None, "adjustment": False,
    }


class TextStyle(Params):
    font_family: str | None = None
    font_size: float | None = Field(default=None, gt=0, le=2000)
    bold: bool | None = None
    italic: bool | None = None
    color: str | None = None
    stroke_color: str | None = None
    stroke_width: float | None = Field(default=None, ge=0, le=200)
    background: str | None = None
    align: Literal["left", "center", "right"] | None = None
    position: list[float] | None = None
    scale: float | None = Field(default=None, ge=0, le=10000)
    rotation: float | None = None
    opacity: float | None = Field(default=None, ge=0, le=100)
    visible: bool | None = None
    tracking: float | None = Field(default=None, ge=-1000, le=10000)
    box: list[float] | None = Field(default=None, description="area text [w, h]; pass null to make point text")
    vertical_align: Literal["top", "center", "bottom"] | None = None
    shadow: dict | None = Field(default=None, description="{color, opacity, angle, distance, blur}; null removes it")


class CreateText(TextStyle):
    sequence_id: str | None = None
    text: str = "새 텍스트 레이어"
    at: TimeValue | None = Field(default=None, description="default playhead")
    duration: TimeValue = "5s"
    track_id: str | None = Field(default=None, description="default: first empty video track above the clips at that time")


@command("graphic.create_text", CreateText)
def graphic_create_text(ctx: Context, p: CreateText):
    """Type tool: create a graphic clip with one text layer."""
    seq = seq_of(ctx.doc, p.sequence_id)
    st = seq["settings"]
    start = seq["playhead"] if p.at is None else resolve_time(p.at, st)
    end = start + max(resolve_time(p.duration, st), timebase.ticks_per_frame(st["fps_num"], st["fps_den"]))
    tid = p.track_id or _free_track_above(ctx, seq, start, end)
    if tid not in seq["tracks"] or seq["tracks"][tid]["kind"] != "video":
        raise CommandError("BAD_TRACK", "graphics go on a video track")
    require_unlocked(seq, tid)
    style = p.model_dump(exclude={"sequence_id", "text", "at", "duration", "track_id"})
    if style.get("position") is None:
        style["position"] = [st["width"] / 2, st["height"] / 2]
    layer = _text_layer(p.text, **style)
    from .timeline import clear_range
    clear_range(ctx, seq, tid, start, end)
    clip = graphic_clip(p.text.splitlines()[0][:40] or "Graphic", start, end, [layer])
    write_clip(ctx, seq, tid, clip)
    return {"clip_id": clip["id"], "layer_id": layer["id"], "track_id": tid}


class CreateGraphic(Params):
    sequence_id: str | None = None
    at: TimeValue | None = Field(default=None, description="default playhead")
    duration: TimeValue = "5s"
    track_id: str | None = Field(default=None, description="default: first empty video track above the clips at that time")


@command("graphic.create", CreateGraphic)
def graphic_create(ctx: Context, p: CreateGraphic):
    """New Graphic (Properties panel): an empty graphic clip to add text and shape layers to."""
    seq = seq_of(ctx.doc, p.sequence_id)
    st = seq["settings"]
    start = seq["playhead"] if p.at is None else resolve_time(p.at, st)
    end = start + max(resolve_time(p.duration, st), timebase.ticks_per_frame(st["fps_num"], st["fps_den"]))
    tid = p.track_id or _free_track_above(ctx, seq, start, end)
    if tid not in seq["tracks"] or seq["tracks"][tid]["kind"] != "video" or seq["tracks"][tid].get("caption_style"):
        raise CommandError("BAD_TRACK", "graphics go on a video track")
    require_unlocked(seq, tid)
    from .timeline import clear_range
    clear_range(ctx, seq, tid, start, end)
    clip = graphic_clip("Graphic", start, end, [])
    write_clip(ctx, seq, tid, clip)
    return {"clip_id": clip["id"], "track_id": tid}


class PlaceMogrt(Params):
    sequence_id: str | None = None
    path: str = Field(description="a .mogrt (or .aegraphic) Motion Graphics Template made in After Effects")
    at: TimeValue | None = Field(default=None, description="default playhead")
    duration: TimeValue = "5s"
    track_id: str | None = Field(default=None, description="default: the first free video track above what is there")
    texts: dict[str, str] = Field(default_factory=dict, description="template text fields by layer name -> new text")


@command("graphic.place_mogrt", PlaceMogrt)
def graphic_place_mogrt(ctx: Context, p: PlaceMogrt):
    """Place a Motion Graphics Template: its text layers become an editable graphic clip (styling is
    best effort; animation in the template is not reproduced)."""
    from ..io.aep import read_mogrt
    from ..io.prproj import _mogrt_layers

    ae = read_mogrt(p.path)
    if not ae or not ae.get("layers"):
        raise CommandError("BAD_MOGRT", f"no text layers could be read from {p.path}")
    seq = seq_of(ctx.doc, p.sequence_id)
    st = seq["settings"]
    start = seq["playhead"] if p.at is None else resolve_time(p.at, st)
    end = start + max(resolve_time(p.duration, st), timebase.ticks_per_frame(st["fps_num"], st["fps_den"]))
    tid = p.track_id or _free_track_above(ctx, seq, start, end)
    if tid not in seq["tracks"] or seq["tracks"][tid]["kind"] != "video":
        raise CommandError("BAD_TRACK", "graphics go on a video track")
    require_unlocked(seq, tid)
    fields = {k: {"textEditValue": v} for k, v in p.texts.items()}
    layers = [dict(l, id=new_id(), approximate=True) for l in _mogrt_layers(ae, fields, st["width"], st["height"])]
    from .timeline import clear_range
    clear_range(ctx, seq, tid, start, end)
    from pathlib import Path as _P
    clip = graphic_clip(_P(p.path).stem, start, end, layers, source=f"mogrt:{_P(p.path).stem}")
    write_clip(ctx, seq, tid, clip)
    return {"clip_id": clip["id"], "track_id": tid, "layer_ids": [l["id"] for l in layers],
            "fields": [l.get("name") for l in ae["layers"]]}


class ApplyTextStyle(Params):
    sequence_id: str | None = None
    clip_ids: list[str] = Field(min_length=1)
    style_id: str = Field(description="a text style from styles.list")


@command("graphic.apply_style", ApplyTextStyle)
def graphic_apply_style(ctx: Context, p: ApplyTextStyle):
    """Apply a text style to every text layer of graphic clips (formatting only; positions stay)."""
    from .. import text_styles

    st = text_styles.get(p.style_id)
    if st is None:
        raise CommandError("NO_STYLE", f"text style not found: {p.style_id}")
    fmt = {k: v for k, v in st["style"].items() if k in text_styles.FORMAT_FIELDS}
    seq = seq_of(ctx.doc, p.sequence_id)
    changed = 0
    for cid in p.clip_ids:
        tid, clip = find_clip(seq, cid)
        require_unlocked(seq, tid)
        if not clip.get("graphic"):
            continue
        layers = [dict(l, **fmt, runs=None) if l.get("type", "text") == "text" else l for l in clip["graphic"]["layers"]]
        changed += sum(1 for l in clip["graphic"]["layers"] if l.get("type", "text") == "text")
        ctx.m.set(clip_path(seq, tid, clip["id"]) + ["graphic", "layers"], layers)
    return {"layers": changed}


def _free_track_above(ctx: Context, seq: dict, start: int, end: int) -> str:
    # caption tracks hold cues, not graphics
    vt = [t for t in seq["video_tracks"] if not seq["tracks"][t].get("caption_style")]
    top_used = -1
    for i, tid in enumerate(vt):
        if any(c["start"] < end and c["end"] > start for c in seq["tracks"][tid]["clips"].values()):
            top_used = i
    for tid in vt[top_used + 1:]:
        if not seq["tracks"][tid]["locked"]:
            return tid
    from .project import AddTrack, track_add
    return track_add(ctx, AddTrack(sequence_id=seq["id"], kind="video"))["track_id"]


class SetLayer(TextStyle):
    sequence_id: str | None = None
    clip_id: str
    layer_id: str | None = Field(default=None, description="default: first layer")
    text: str | None = None


@command("graphic.set_layer", SetLayer)
def graphic_set_layer(ctx: Context, p: SetLayer):
    """Edit a text layer: text, font, size, colors, stroke, alignment, position, scale, rotation, opacity."""
    seq, tid, clip, _ = _ctx(ctx, p.sequence_id, p.clip_id)
    if not clip.get("graphic"):
        raise CommandError("NOT_GRAPHIC", "clip is not a graphic")
    layers = copy.deepcopy(clip["graphic"]["layers"])
    idx = next((i for i, l in enumerate(layers) if (l["id"] == p.layer_id if p.layer_id else l.get("type", "text") == "text")), None)
    if idx is None:
        raise CommandError("NO_LAYER", f"layer not found: {p.layer_id}")
    if layers[idx].get("type", "text") != "text":
        raise CommandError("NOT_TEXT", "use graphic.set_shape for shape layers")
    changes = p.model_dump(exclude={"sequence_id", "clip_id", "layer_id"}, exclude_none=True)
    # explicit nulls for nullable fields are allowed through model_fields_set
    for k in ("stroke_color", "background", "box", "shadow"):
        if k in p.model_fields_set and getattr(p, k) is None:
            changes[k] = None
    if changes.get("shadow"):
        from ..model import TextShadow
        changes["shadow"] = TextShadow(**changes["shadow"]).model_dump()
    layer = layers[idx]
    if layer.get("runs") and set(changes) - {"position", "box", "align", "vertical_align", "scale", "rotation", "opacity", "visible", "shadow", "background"}:
        # a style or text edit applies to the whole layer: fold the first run into it
        first = {k: v for k, v in layer["runs"][0].items() if v is not None and k != "text"}
        layer.update(first)
        layer["runs"] = None
    layers[idx].update(changes)
    layers[idx]["approximate"] = False if changes else layers[idx]["approximate"]
    path = clip_path(seq, tid, clip["id"])
    ctx.m.set(path + ["graphic", "layers"], layers)
    if "text" in changes and idx == 0:
        ctx.m.set(path + ["name"], changes["text"].splitlines()[0][:40] or clip["name"])


class AddLayer(TextStyle):
    sequence_id: str | None = None
    clip_id: str
    text: str = "새 텍스트 레이어"


@command("graphic.add_layer", AddLayer)
def graphic_add_layer(ctx: Context, p: AddLayer):
    """Add another text layer to a graphic clip."""
    seq, tid, clip, _ = _ctx(ctx, p.sequence_id, p.clip_id)
    if not clip.get("graphic"):
        raise CommandError("NOT_GRAPHIC", "clip is not a graphic")
    style = p.model_dump(exclude={"sequence_id", "clip_id", "text"})
    if style.get("position") is None:
        style["position"] = [seq["settings"]["width"] / 2, seq["settings"]["height"] / 2]
    layer = _text_layer(p.text, **style)
    ctx.m.set(clip_path(seq, tid, clip["id"]) + ["graphic", "layers"], clip["graphic"]["layers"] + [layer])
    return {"layer_id": layer["id"]}


class RemoveLayer(Params):
    sequence_id: str | None = None
    clip_id: str
    layer_id: str


@command("graphic.remove_layer", RemoveLayer)
def graphic_remove_layer(ctx: Context, p: RemoveLayer):
    """Remove a text, shape or image layer from a graphic clip."""
    seq, tid, clip, _ = _ctx(ctx, p.sequence_id, p.clip_id)
    layers = [l for l in (clip.get("graphic") or {}).get("layers", []) if l["id"] != p.layer_id]
    if len(layers) == len((clip.get("graphic") or {}).get("layers", [])):
        raise CommandError("NO_LAYER", f"layer not found: {p.layer_id}")
    ctx.m.set(clip_path(seq, tid, clip["id"]) + ["graphic", "layers"], layers)



# ---------------------------------------------------------------- shape layers

class ShapeStyle(Params):
    fill: str | None = Field(default=None, description="fill color; null (or fill_enabled=false) = no fill")
    fill_enabled: bool | None = None
    stroke_color: str | None = Field(default=None, description="null = no stroke")
    stroke_width: float | None = Field(default=None, ge=0, le=500)
    position: list[float] | None = None
    anchor: list[float] | None = None
    scale: float | None = Field(default=None, ge=0, le=10000)
    scale_width: float | None = Field(default=None, ge=0, le=10000)
    uniform: bool | None = None
    rotation: float | None = None
    opacity: float | None = Field(default=None, ge=0, le=100)
    visible: bool | None = None
    blend: Literal["normal", "multiply"] | None = None


def _shape_changes(p: ShapeStyle) -> dict:
    changes = p.model_dump(include=set(ShapeStyle.model_fields) - {"fill_enabled"}, exclude_none=True)
    if p.fill_enabled is False or ("fill" in p.model_fields_set and p.fill is None):
        changes["fill"] = None
    elif p.fill_enabled and "fill" not in changes:
        changes["fill"] = "#ffffff"
    if "stroke_color" in p.model_fields_set and p.stroke_color is None:
        changes["stroke_color"] = None
    for k in ("position", "anchor"):
        if k in changes:
            if len(changes[k]) != 2:
                raise CommandError("BAD_VALUE", f"{k} needs [x, y]")
            changes[k] = [float(v) for v in changes[k]]
    return changes


class NewShape(ShapeStyle):
    shape: Literal["rectangle", "ellipse"] = "rectangle"
    rect: list[float] | None = Field(default=None, description="[x0, y0, x1, y1] sequence pixels; default a centered box")


def _shape_layer(p: NewShape, st: dict) -> dict:
    if p.rect is not None and len(p.rect) != 4:
        raise CommandError("BAD_VALUE", "rect needs [x0, y0, x1, y1]")
    W, H = st["width"], st["height"]
    x0, y0, x1, y1 = p.rect if p.rect else (W * 0.3, H * 0.35, W * 0.7, H * 0.65)
    x0, x1 = sorted((float(x0), float(x1)))
    y0, y1 = sorted((float(y0), float(y1)))
    w, h = max(x1 - x0, 1.0), max(y1 - y0, 1.0)
    layer = {
        "id": new_id(), "type": "shape", "shape": p.shape, "path": shapes.make(p.shape, -w / 2, -h / 2, w / 2, h / 2),
        "closed": True, "fill": "#ffffff", "stroke_color": None, "stroke_width": 0.0,
        "position": [x0 + w / 2, y0 + h / 2], "anchor": [0.0, 0.0], "scale": 100.0, "scale_width": 100.0,
        "uniform": True, "rotation": 0.0, "opacity": 100.0, "visible": True, "blend": "normal",
    }
    layer.update(_shape_changes(p))
    return layer


class CreateShape(NewShape):
    sequence_id: str | None = None
    at: TimeValue | None = Field(default=None, description="default playhead")
    duration: TimeValue = "5s"
    track_id: str | None = Field(default=None, description="default: first empty video track above the clips at that time")


@command("graphic.create_shape", CreateShape)
def graphic_create_shape(ctx: Context, p: CreateShape):
    """Rectangle/Ellipse tool: create a graphic clip with one shape layer."""
    seq = seq_of(ctx.doc, p.sequence_id)
    st = seq["settings"]
    start = seq["playhead"] if p.at is None else resolve_time(p.at, st)
    end = start + max(resolve_time(p.duration, st), timebase.ticks_per_frame(st["fps_num"], st["fps_den"]))
    tid = p.track_id or _free_track_above(ctx, seq, start, end)
    if tid not in seq["tracks"] or seq["tracks"][tid]["kind"] != "video":
        raise CommandError("BAD_TRACK", "graphics go on a video track")
    require_unlocked(seq, tid)
    layer = _shape_layer(p, st)
    from .timeline import clear_range
    clear_range(ctx, seq, tid, start, end)
    clip = graphic_clip("Graphic", start, end, [layer])
    write_clip(ctx, seq, tid, clip)
    return {"clip_id": clip["id"], "layer_id": layer["id"], "track_id": tid}


class AddShape(NewShape):
    sequence_id: str | None = None
    clip_id: str


@command("graphic.add_shape", AddShape)
def graphic_add_shape(ctx: Context, p: AddShape):
    """Add a rectangle or ellipse layer on top of a graphic clip's layers."""
    seq, tid, clip, _ = _ctx(ctx, p.sequence_id, p.clip_id)
    if not clip.get("graphic"):
        raise CommandError("NOT_GRAPHIC", "clip is not a graphic")
    layer = _shape_layer(p, seq["settings"])
    ctx.m.set(clip_path(seq, tid, clip["id"]) + ["graphic", "layers"], clip["graphic"]["layers"] + [layer])
    return {"layer_id": layer["id"]}


class SetShape(ShapeStyle):
    sequence_id: str | None = None
    clip_id: str
    layer_id: str | None = Field(default=None, description="default: first shape layer")
    width: float | None = Field(default=None, gt=0, le=100000, description="resize the path about its center")
    height: float | None = Field(default=None, gt=0, le=100000)
    path: list[list[float]] | None = Field(default=None, description="replace the path (vertices [x, y, in_x, in_y, out_x, out_y])")
    closed: bool | None = None


@command("graphic.set_shape", SetShape)
def graphic_set_shape(ctx: Context, p: SetShape):
    """Edit a shape layer: fill, stroke, size, path, position, anchor, scale, rotation, opacity."""
    seq, tid, clip, _ = _ctx(ctx, p.sequence_id, p.clip_id)
    if not clip.get("graphic"):
        raise CommandError("NOT_GRAPHIC", "clip is not a graphic")
    layers = copy.deepcopy(clip["graphic"]["layers"])
    idx = next((i for i, l in enumerate(layers) if (l["id"] == p.layer_id if p.layer_id else l.get("type") == "shape")), None)
    if idx is None:
        raise CommandError("NO_LAYER", f"shape layer not found: {p.layer_id}")
    layer = layers[idx]
    if layer.get("type") != "shape":
        raise CommandError("NOT_SHAPE", "use graphic.set_layer for text layers")
    changes = _shape_changes(p)
    if p.path is not None:
        if len(p.path) < 2 or any(len(v) != 6 for v in p.path):
            raise CommandError("BAD_VALUE", "path needs at least 2 vertices of [x, y, in_x, in_y, out_x, out_y]")
        changes["path"] = [[float(x) for x in v] for v in p.path]
        changes["shape"] = "path"
    if p.closed is not None:
        changes["closed"] = p.closed
    if p.width is not None or p.height is not None:
        changes["path"] = shapes.resize(changes.get("path", layer["path"]), p.width, p.height)
    layer.update(changes)
    ctx.m.set(clip_path(seq, tid, clip["id"]) + ["graphic", "layers"], layers)


# ---------------------------------------------------------------- image layers

class ImageStyle(Params):
    position: list[float] | None = None
    anchor: list[float] | None = Field(default=None, description="image pixels; default the centre")
    scale: float | None = Field(default=None, ge=0, le=10000)
    scale_width: float | None = Field(default=None, ge=0, le=10000)
    uniform: bool | None = None
    rotation: float | None = None
    opacity: float | None = Field(default=None, ge=0, le=100)
    crop_left: float | None = Field(default=None, ge=0, le=100, description="percent of the image hidden from the left")
    crop_top: float | None = Field(default=None, ge=0, le=100)
    crop_right: float | None = Field(default=None, ge=0, le=100)
    crop_bottom: float | None = Field(default=None, ge=0, le=100)
    visible: bool | None = None
    blend: Literal["normal", "multiply"] | None = None


def _image_size(path: str) -> list[float]:
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(path) as im:
            return [float(im.width), float(im.height)]
    except FileNotFoundError as exc:
        raise CommandError("NO_FILE", f"file not found: {path}") from exc
    except (UnidentifiedImageError, OSError):
        from ..render.compositor import RenderError, decode_still

        try:
            im = decode_still(path)
        except RenderError as exc:
            raise CommandError("BAD_IMAGE", f"not an image: {path}") from exc
        return [float(im.width), float(im.height)]


def _image_changes(p: ImageStyle) -> dict:
    changes = p.model_dump(include=set(ImageStyle.model_fields), exclude_none=True)
    if "anchor" in p.model_fields_set and p.anchor is None:
        changes["anchor"] = None
    for k in ("position", "anchor"):
        if changes.get(k) is not None:
            if len(changes[k]) != 2:
                raise CommandError("BAD_VALUE", f"{k} needs [x, y]")
            changes[k] = [float(v) for v in changes[k]]
    return changes


def _follow_uniform(layer: dict, changes: dict) -> dict:
    """With uniform scale the width follows the scale, so turning uniform off starts from the same size."""
    if "scale" in changes and "scale_width" not in changes and changes.get("uniform", layer.get("uniform", True)):
        changes["scale_width"] = changes["scale"]
    return changes


class AddImage(ImageStyle):
    sequence_id: str | None = None
    clip_id: str
    path: str = Field(description="image file (png, jpg, webp, ...)")


@command("graphic.add_image", AddImage)
def graphic_add_image(ctx: Context, p: AddImage):
    """New Layer > From File: put an image on top of a graphic clip's layers. By default it sits in
    the middle of the frame at full size, scaled down to fit when it is larger than the frame."""
    seq, tid, clip, _ = _ctx(ctx, p.sequence_id, p.clip_id)
    if not clip.get("graphic"):
        raise CommandError("NOT_GRAPHIC", "clip is not a graphic")
    w, h = _image_size(p.path)
    st = seq["settings"]
    fit = min(100.0, 100.0 * st["width"] / w, 100.0 * st["height"] / h)
    layer = {"id": new_id(), "type": "image", "path": p.path, "size": [w, h], "position": [st["width"] / 2, st["height"] / 2],
             "anchor": None, "scale": round(fit, 2), "scale_width": round(fit, 2), "uniform": True, "rotation": 0.0, "opacity": 100.0,
             "crop_left": 0.0, "crop_top": 0.0, "crop_right": 0.0, "crop_bottom": 0.0, "visible": True, "blend": "normal"}
    layer.update(_follow_uniform(layer, _image_changes(p)))
    ctx.m.set(clip_path(seq, tid, clip["id"]) + ["graphic", "layers"], clip["graphic"]["layers"] + [layer])
    return {"layer_id": layer["id"], "size": [w, h]}


class SetImage(ImageStyle):
    sequence_id: str | None = None
    clip_id: str
    layer_id: str
    path: str | None = Field(default=None, description="replace the picture (keeps position and scale)")


@command("graphic.set_image", SetImage)
def graphic_set_image(ctx: Context, p: SetImage):
    """Edit an image layer: replace its file, position, anchor, scale, rotation, opacity, crop (per layer, which
    Premiere cannot do inside a graphic), visibility, blend."""
    seq, tid, clip, _ = _ctx(ctx, p.sequence_id, p.clip_id)
    layers = copy.deepcopy((clip.get("graphic") or {}).get("layers", []))
    layer = next((l for l in layers if l["id"] == p.layer_id), None)
    if layer is None:
        raise CommandError("NO_LAYER", f"layer not found: {p.layer_id}")
    if layer.get("type") != "image":
        raise CommandError("NOT_IMAGE", "use graphic.set_layer / graphic.set_shape for text and shape layers")
    changes = _follow_uniform(layer, _image_changes(p))
    if p.path is not None:
        changes.update(path=p.path, size=_image_size(p.path))
    layer.update(changes)
    if layer.get("crop_left", 0) + layer.get("crop_right", 0) >= 100 or layer.get("crop_top", 0) + layer.get("crop_bottom", 0) >= 100:
        raise CommandError("BAD_VALUE", "the crop would hide the whole image")
    ctx.m.set(clip_path(seq, tid, clip["id"]) + ["graphic", "layers"], layers)


class MoveLayer(Params):
    sequence_id: str | None = None
    clip_id: str
    layer_id: str
    index: int = Field(description="new position in the stack, 0 = bottom; negative counts from the top")


@command("graphic.move_layer", MoveLayer)
def graphic_move_layer(ctx: Context, p: MoveLayer):
    """Reorder a graphic's layers (Essential Graphics stacking order)."""
    seq, tid, clip, _ = _ctx(ctx, p.sequence_id, p.clip_id)
    layers = list((clip.get("graphic") or {}).get("layers", []))
    idx = next((i for i, l in enumerate(layers) if l["id"] == p.layer_id), None)
    if idx is None:
        raise CommandError("NO_LAYER", f"layer not found: {p.layer_id}")
    target = max(0, min(len(layers) - 1, p.index if p.index >= 0 else len(layers) + p.index))
    if target != idx:
        layers.insert(target, layers.pop(idx))
        ctx.m.set(clip_path(seq, tid, clip["id"]) + ["graphic", "layers"], layers)


# ---------------------------------------------------------------- batch text

TEXT_STYLE_KEYS = ("font_family", "font_size", "bold", "italic", "color", "stroke_color", "stroke_width", "background", "align",
                   "vertical_align", "tracking", "shadow", "scale", "rotation", "opacity")


class MatchStyle(Params):
    sequence_id: str | None = None
    source_clip_id: str
    source_layer_id: str | None = Field(default=None, description="default: the source's first text layer")
    clip_ids: list[str] = Field(min_length=1, description="graphic clips whose text layers take the style")
    include_position: bool = Field(default=False, description="also copy the layer position and area box")


@command("graphic.match_style", MatchStyle)
def graphic_match_style(ctx: Context, p: MatchStyle):
    """Give every text layer of the clips the style of one text layer (font, size, colours, stroke,
    shadow, alignment, tracking; optionally position) in one step - the text itself stays."""
    seq, _, src, _ = _ctx(ctx, p.sequence_id, p.source_clip_id)
    layers = (src.get("graphic") or {}).get("layers") or []
    ref = next((l for l in layers if (l["id"] == p.source_layer_id if p.source_layer_id else l.get("type", "text") == "text")), None)
    if ref is None or ref.get("type", "text") != "text":
        raise CommandError("NO_LAYER", "the source clip has no such text layer")
    style = {k: copy.deepcopy(ref.get(k)) for k in TEXT_STYLE_KEYS}
    if ref.get("runs"):  # a styled layer: its first run is the look
        style.update({k: v for k, v in ref["runs"][0].items() if v is not None and k in TEXT_STYLE_KEYS})
    if p.include_position:
        style["position"] = list(ref["position"])
        style["box"] = copy.deepcopy(ref.get("box"))
    changed = 0
    for cid in p.clip_ids:
        if cid == src["id"]:
            continue
        _, tid, clip, _ = _ctx(ctx, p.sequence_id, cid)
        if not clip.get("graphic"):
            continue
        new = copy.deepcopy(clip["graphic"]["layers"])
        hit = False
        for l in new:
            if l.get("type", "text") == "text":
                l.update(copy.deepcopy(style))
                l["runs"] = None
                l["approximate"] = False
                hit = True
        if hit:
            ctx.m.set(clip_path(seq, tid, cid) + ["graphic", "layers"], new)
            changed += 1
    return {"clips": changed}


class ReplaceText(Params):
    sequence_id: str | None = None
    find: str = Field(min_length=1)
    replace: str = ""
    match_case: bool = False
    clip_ids: list[str] | None = Field(default=None, description="default: every graphic clip of the sequence")


@command("graphic.replace_text", ReplaceText)
def graphic_replace_text(ctx: Context, p: ReplaceText):
    """Find and replace in every text layer (the Text panel's Replace All), styled spans included."""
    import re

    seq = seq_of(ctx.doc, p.sequence_id)
    pat = re.compile(re.escape(p.find), 0 if p.match_case else re.IGNORECASE)
    count = 0
    for tid, track in seq["tracks"].items():
        for clip in track["clips"].values():
            if not clip.get("graphic") or (p.clip_ids and clip["id"] not in p.clip_ids):
                continue
            new = copy.deepcopy(clip["graphic"]["layers"])
            hits = 0
            for l in new:
                if l.get("type", "text") != "text":
                    continue
                if l.get("runs"):
                    for r in l["runs"]:
                        r["text"], n = pat.subn(lambda m: p.replace, r["text"])
                        hits += n
                    l["text"] = "".join(r["text"] for r in l["runs"])
                else:
                    l["text"], n = pat.subn(lambda m: p.replace, l["text"])
                    hits += n
            if hits:
                path = clip_path(seq, tid, clip["id"])
                # a clip named after its text follows the new text
                line = lambda ls: next((l["text"] for l in ls if l.get("type", "text") == "text"), "").split(chr(10))[0][:40]  # noqa: E731
                named = clip["name"] == line(clip["graphic"]["layers"])
                ctx.m.set(path + ["graphic", "layers"], new)
                if named and line(new):
                    ctx.m.set(path + ["name"], line(new))
                count += hits
    return {"replaced": count}
