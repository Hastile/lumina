"""Motion, opacity/volume keyframing and graphics (text layers)."""

from __future__ import annotations

import copy
from typing import Literal

from pydantic import Field

from .. import timebase
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
    v = _check_value(p.prop, p.value)
    kfs = clip["keyframes"].get(p.prop)
    if not kfs:
        _write_static(ctx, seq, tid, clip, p.prop, v)
        return {"animated": False}
    t = source_time(clip, _at(seq, clip, p.at))
    new = [k for k in kfs if k["t"] != t] + [{"t": t, "v": v, "interp": _interp_near(kfs, t)}]
    ctx.m.set(clip_path(seq, tid, clip["id"]) + ["keyframes", p.prop], sorted(new, key=lambda k: k["t"]))
    return {"animated": True, "keyframe_t": t}


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
    interp: Literal["linear", "ease", "hold"] | None = None


@command("clip.keyframe", KeyframeOp)
def clip_keyframe(ctx: Context, p: KeyframeOp):
    """Add / remove / toggle a keyframe at a time (the diamond button), or change its interpolation."""
    seq, tid, clip, media = _ctx(ctx, p.sequence_id, p.clip_id)
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
        ctx.m.set(path, [dict(k, interp=p.interp) if k is hit else k for k in kfs])
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
                    "interp": k.get("interp", "linear")})
        if out[-1]["interp"] not in ("linear", "ease", "hold"):
            raise CommandError("BAD_VALUE", "interp is linear | ease | hold")
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
             "background": None, "align": "center", "position": position, "scale": 100.0, "rotation": 0.0,
             "opacity": 100.0, "approximate": False}
    for k, v in style.items():
        if v is not None:
            layer[k] = v
    return layer


def graphic_clip(name: str, start: int, end: int, layers: list[dict], source: str | None = None, label: str = "rose") -> dict:
    return {
        "id": new_id(), "media_id": None, "name": name, "start": start, "end": end, "source_in": 0, "speed": 1.0,
        "enabled": True, "link_id": None, "label": label, "opacity": 100.0, "volume_db": 0.0,
        "motion": {"position": None, "scale": 100.0, "scale_width": 100.0, "uniform": True, "rotation": 0.0, "anchor": None,
                   "crop_left": 0.0, "crop_top": 0.0, "crop_right": 0.0, "crop_bottom": 0.0},
        "keyframes": {}, "graphic": {"layers": layers, "source": source},
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


def _free_track_above(ctx: Context, seq: dict, start: int, end: int) -> str:
    vt = seq["video_tracks"]
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
    idx = next((i for i, l in enumerate(layers) if p.layer_id in (None, l["id"])), None)
    if idx is None:
        raise CommandError("NO_LAYER", f"layer not found: {p.layer_id}")
    changes = p.model_dump(exclude={"sequence_id", "clip_id", "layer_id"}, exclude_none=True)
    # explicit nulls for nullable colors are allowed through model_fields_set
    for k in ("stroke_color", "background"):
        if k in p.model_fields_set and getattr(p, k) is None:
            changes[k] = None
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
    """Remove a text layer from a graphic clip."""
    seq, tid, clip, _ = _ctx(ctx, p.sequence_id, p.clip_id)
    layers = [l for l in (clip.get("graphic") or {}).get("layers", []) if l["id"] != p.layer_id]
    if len(layers) == len((clip.get("graphic") or {}).get("layers", [])):
        raise CommandError("NO_LAYER", f"layer not found: {p.layer_id}")
    ctx.m.set(clip_path(seq, tid, clip["id"]) + ["graphic", "layers"], layers)

