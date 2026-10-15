"""Standard effects (Effects panel > Video Effects) and transitions."""

from __future__ import annotations

import copy
from typing import Literal

from pydantic import Field

from .. import timebase
from ..effects import ANIMATABLE, EFFECTS, TRANSITIONS, default_params, param, transition_window
from ..model import EffectType, TransitionType, new_id
from ..motion import source_size, source_time
from .motion import INTERPS, _at, _ctx, _interp_near, handles_of, with_interp
from .project import seq_of
from .registry import CommandError, Context, Params, TimeValue, command, resolve_time
from .timeline import clip_path, clips_sorted, find_clip, require_unlocked

ParamValue = float | bool | list[float]


def _effect(clip: dict, effect_id: str) -> tuple[int, dict]:
    for i, e in enumerate(clip.get("effects") or []):
        if e["id"] == effect_id:
            return i, e
    raise CommandError("NO_EFFECT", f"effect not found on clip: {effect_id}")


def _check_param(kind: str, name: str, v: ParamValue) -> ParamValue:
    spec = EFFECTS[kind]["params"].get(name)
    if spec is None:
        raise CommandError("BAD_PARAM", f"{kind} has no parameter {name!r}; one of {list(EFFECTS[kind]['params'])}")
    ptype = spec[0]
    if ptype == "point":
        if not isinstance(v, list) or len(v) != 2:
            raise CommandError("BAD_VALUE", f"{name} needs [x, y]")
        return [float(v[0]), float(v[1])]
    if ptype == "bool":
        return bool(v)
    if isinstance(v, (list, bool)):
        raise CommandError("BAD_VALUE", f"{name} needs a number")
    v = float(v)
    if name == "opacity" and not 0 <= v <= 100:
        raise CommandError("BAD_VALUE", "opacity is 0-100")
    if name in ("scale", "scale_width") and not 0 <= v <= 10000:
        raise CommandError("BAD_VALUE", "scale is 0-10000 %")
    if name == "blurriness" and not 0 <= v <= 1000:
        raise CommandError("BAD_VALUE", "blurriness is 0-1000")
    if name == "dimensions" and v not in (0, 1, 2):
        raise CommandError("BAD_VALUE", "dimensions: 0 both, 1 horizontal, 2 vertical")
    return v


def _effects_path(seq: dict, tid: str, clip: dict) -> list:
    return clip_path(seq, tid, clip["id"]) + ["effects"]


def _update(ctx: Context, seq: dict, tid: str, clip: dict, i: int, fn) -> None:
    """Patch one effect (lists are replaced wholesale in the patch model)."""
    e = copy.deepcopy(clip["effects"][i])
    e.setdefault("keyframes", {})
    fn(e)
    stack = list(clip["effects"])
    stack[i] = e
    ctx.m.set(_effects_path(seq, tid, clip), stack)


class AddEffect(Params):
    sequence_id: str | None = None
    clip_ids: list[str] = Field(min_length=1)
    type: EffectType
    params: dict[str, ParamValue] = Field(default_factory=dict)
    index: int | None = Field(default=None, description="position in the effect stack; default last")


@command("effect.add", AddEffect)
def effect_add(ctx: Context, p: AddEffect):
    """Apply a video effect (drag from the Effects panel). Effects run in stack order, before Motion."""
    ids = []
    for cid in p.clip_ids:
        seq, tid, clip, media = _ctx(ctx, p.sequence_id, cid)
        if seq["tracks"][tid]["kind"] != "video":
            raise CommandError("NOT_VIDEO", "video effects go on video clips")
        w, h = source_size(clip, seq, media)
        params = default_params(p.type, w, h)
        for k, v in p.params.items():
            params[k] = _check_param(p.type, k, v)
        effect = {"id": new_id(), "type": p.type, "enabled": True, "params": params, "keyframes": {}}
        stack = list(clip.get("effects") or [])
        stack.insert(len(stack) if p.index is None else max(0, min(p.index, len(stack))), effect)
        ctx.m.set(_effects_path(seq, tid, clip), stack)
        ids.append(effect["id"])
    return {"effect_ids": ids}


class ApplyPreset(Params):
    sequence_id: str | None = None
    clip_ids: list[str] = Field(min_length=1)
    preset_id: str = Field(description="an id from presets.list (the Effects panel's Presets folder)")


@command("effect.apply_preset", ApplyPreset)
def effect_apply_preset(ctx: Context, p: ApplyPreset):
    """Apply an effect preset: its effects are appended with keyframes fitted to each clip
    (anchored to the clip's in or out point, or scaled to its length, as saved)."""
    from .. import presets

    preset = presets.get(p.preset_id)
    if preset is None:
        raise CommandError("NO_PRESET", f"preset not found: {p.preset_id}")
    ids = []
    for cid in p.clip_ids:
        seq, tid, clip, media = _ctx(ctx, p.sequence_id, cid)
        if seq["tracks"][tid]["kind"] != "video":
            raise CommandError("NOT_VIDEO", "effect presets go on video clips")
        w, h = source_size(clip, seq, media)
        added = []
        for fx in presets.fit(preset, clip, w, h):
            fx["params"] = {**default_params(fx["type"], w, h), **fx["params"]}
            added.append(fx)
        ctx.m.set(_effects_path(seq, tid, clip), list(clip.get("effects") or []) + added)
        ids += [fx["id"] for fx in added]
    return {"effect_ids": ids}


class EffectRef(Params):
    sequence_id: str | None = None
    clip_id: str
    effect_id: str


@command("effect.remove", EffectRef)
def effect_remove(ctx: Context, p: EffectRef):
    """Delete an effect from a clip."""
    seq, tid, clip, _ = _ctx(ctx, p.sequence_id, p.clip_id)
    i, _e = _effect(clip, p.effect_id)
    stack = list(clip["effects"])
    del stack[i]
    ctx.m.set(_effects_path(seq, tid, clip), stack)


class SetEffect(EffectRef):
    enabled: bool | None = Field(default=None, description="the fx toggle")
    index: int | None = Field(default=None, description="move to this stack position")


@command("effect.set", SetEffect)
def effect_set(ctx: Context, p: SetEffect):
    """Toggle an effect on/off or reorder it in the stack."""
    seq, tid, clip, _ = _ctx(ctx, p.sequence_id, p.clip_id)
    i, e = _effect(clip, p.effect_id)
    if p.enabled is not None and e["enabled"] != p.enabled:
        _update(ctx, seq, tid, clip, i, lambda x: x.update(enabled=p.enabled))
    if p.index is not None:
        stack = list(clip["effects"])
        item = stack.pop(i)
        stack.insert(max(0, min(p.index, len(stack))), item)
        if [x["id"] for x in stack] != [x["id"] for x in clip["effects"]]:
            ctx.m.set(_effects_path(seq, tid, clip), stack)


class SetParam(EffectRef):
    param: str
    value: ParamValue
    at: TimeValue | None = Field(default=None, description="sequence time used when the parameter is animated; default playhead")


@command("effect.set_param", SetParam)
def effect_set_param(ctx: Context, p: SetParam):
    """Set an effect parameter; if it is animated, adds or updates the keyframe at `at`."""
    seq, tid, clip, _ = _ctx(ctx, p.sequence_id, p.clip_id)
    i, e = _effect(clip, p.effect_id)
    v = _check_param(e["type"], p.param, p.value)
    kfs = e.get("keyframes", {}).get(p.param)
    if not kfs:
        _update(ctx, seq, tid, clip, i, lambda x: x["params"].__setitem__(p.param, v))
        return {"animated": False}
    t = source_time(clip, _at(seq, clip, p.at))
    old = next((k for k in kfs if k["t"] == t), None)
    new = sorted([k for k in kfs if k["t"] != t] + [{**old, "v": v} if old else {"t": t, "v": v, "interp": _interp_near(kfs, t)}], key=lambda k: k["t"])
    _update(ctx, seq, tid, clip, i, lambda x: x["keyframes"].__setitem__(p.param, new))
    return {"animated": True, "keyframe_t": t}


class ParamRef(EffectRef):
    param: str
    at: TimeValue | None = None


def _require_animatable(e: dict, name: str) -> None:
    spec = EFFECTS[e["type"]]["params"].get(name)
    if spec is None or spec[0] not in ANIMATABLE:
        raise CommandError("NOT_ANIMATABLE", f"{name} cannot be keyframed")


@command("effect.toggle_animation", ParamRef)
def effect_toggle_animation(ctx: Context, p: ParamRef):
    """The stopwatch for an effect parameter."""
    seq, tid, clip, media = _ctx(ctx, p.sequence_id, p.clip_id)
    i, e = _effect(clip, p.effect_id)
    _require_animatable(e, p.param)
    at = _at(seq, clip, p.at)
    w, h = source_size(clip, seq, media)
    current = param(e, p.param, clip, at, w, h)
    if e.get("keyframes", {}).get(p.param):
        def stop(x):
            del x["keyframes"][p.param]
            x["params"][p.param] = current
        _update(ctx, seq, tid, clip, i, stop)
        return {"animated": False}
    first = [{"t": source_time(clip, at), "v": current, "interp": "linear"}]
    _update(ctx, seq, tid, clip, i, lambda x: x["keyframes"].__setitem__(p.param, first))
    return {"animated": True}


class ParamKeyframe(ParamRef):
    op: Literal["add", "remove", "toggle"] = "toggle"
    interp: Literal["linear", "ease", "hold", "bezier"] | None = None


@command("effect.keyframe", ParamKeyframe)
def effect_keyframe(ctx: Context, p: ParamKeyframe):
    """Add / remove / toggle an effect-parameter keyframe at a time, or change its interpolation."""
    seq, tid, clip, media = _ctx(ctx, p.sequence_id, p.clip_id)
    i, e = _effect(clip, p.effect_id)
    _require_animatable(e, p.param)
    at = _at(seq, clip, p.at)
    t = source_time(clip, at)
    tol = round(timebase.ticks_per_frame(seq["settings"]["fps_num"], seq["settings"]["fps_den"]) * clip["speed"] / 2)
    kfs = list(e.get("keyframes", {}).get(p.param) or [])
    hit = next((k for k in kfs if abs(k["t"] - t) <= tol), None)
    def put(new):
        def fn(x):
            if new:
                x["keyframes"][p.param] = new
            else:
                x["keyframes"].pop(p.param, None)
                x["params"][p.param] = hit["v"]
        _update(ctx, seq, tid, clip, i, fn)

    if hit and p.op in ("remove", "toggle") and p.interp is None:
        kfs.remove(hit)
        put(kfs)
        return {"removed": True}
    if hit and p.interp:
        put([with_interp(k, p.interp) if k is hit else k for k in kfs])
        return {"interp": p.interp}
    if p.op == "remove":
        raise CommandError("NO_KEYFRAME", "no keyframe at that time")
    w, h = source_size(clip, seq, media)
    kfs.append({"t": t, "v": param(e, p.param, clip, at, w, h), "interp": p.interp or _interp_near(kfs, t)})
    put(sorted(kfs, key=lambda k: k["t"]))
    return {"added": True, "t": t}


class SetParamKeyframes(EffectRef):
    param: str
    keyframes: list[dict] = Field(description="[{t: clip-relative time, v, interp}] - replaces all; [] stops animating")


@command("effect.set_keyframes", SetParamKeyframes)
def effect_set_keyframes(ctx: Context, p: SetParamKeyframes):
    """Replace an effect parameter's keyframes (times relative to the clip start)."""
    seq, tid, clip, media = _ctx(ctx, p.sequence_id, p.clip_id)
    i, e = _effect(clip, p.effect_id)
    _require_animatable(e, p.param)
    if not p.keyframes:
        if e.get("keyframes", {}).get(p.param):
            w, h = source_size(clip, seq, media)
            v0 = param(e, p.param, clip, clip["start"], w, h)

            def stop(x):
                del x["keyframes"][p.param]
                x["params"][p.param] = v0
            _update(ctx, seq, tid, clip, i, stop)
        return {"count": 0}
    out = []
    for k in p.keyframes:
        rel = resolve_time(k["t"], seq["settings"])
        interp = k.get("interp", "linear")
        if interp not in INTERPS:
            raise CommandError("BAD_VALUE", "interp is linear | ease | hold | bezier")
        out.append({"t": source_time(clip, clip["start"] + rel), "v": _check_param(e["type"], p.param, k["v"]), "interp": interp,
                    **handles_of(k)})
    out.sort(key=lambda k: k["t"])
    _update(ctx, seq, tid, clip, i, lambda x: x["keyframes"].__setitem__(p.param, out))
    return {"count": len(out)}


# ---------------------------------------------------------------- transitions


def _neighbor(track: dict, clip: dict, edge: str) -> dict | None:
    for c in track["clips"].values():
        if edge == "end" and c["start"] == clip["end"]:
            return c
        if edge == "start" and c["end"] == clip["start"]:
            return c
    return None


def _existing_at(track: dict, a: str | None, b: str | None) -> str | None:
    for tid_, tr in track["transitions"].items():
        if (a and tr.get("clip_a") == a) or (b and tr.get("clip_b") == b):
            return tid_
    return None


def _place_transition(ctx: Context, seq: dict, tid: str, clip: dict, edge: str, ttype: str, duration: int, alignment: str | None) -> str:
    track = seq["tracks"][tid]
    other = _neighbor(track, clip, edge)
    a, b = (clip, other) if edge == "end" else (other, clip)
    align = alignment or ("center" if a is not None and b is not None else "start" if a is None else "end")
    if a is not None and b is not None:
        # Premiere shortens a transition that has no media handles; we keep the requested
        # length but never let it run past either clip.
        duration = min(duration, (a["end"] - a["start"]) * (2 if align == "center" else 1),
                       (b["end"] - b["start"]) * (2 if align == "center" else 1))
    else:
        c = a or b
        duration = min(duration, c["end"] - c["start"])
    rec = {"id": new_id(), "type": ttype, "clip_a": a["id"] if a else None, "clip_b": b["id"] if b else None,
           "duration": max(1, duration), "alignment": align}
    old = _existing_at(track, rec["clip_a"], rec["clip_b"])
    path = ["sequences", seq["id"], "tracks", tid, "transitions"]
    if old:
        ctx.m.delete(path + [old])
    ctx.m.set(path + [rec["id"]], rec)
    return rec["id"]


class AddTransition(Params):
    sequence_id: str | None = None
    clip_id: str
    edge: Literal["start", "end"] = Field(description="which edit point of the clip")
    type: TransitionType | None = Field(default=None, description="default: Cross Dissolve (video) / Constant Power (audio)")
    duration: TimeValue = "1s"
    alignment: Literal["center", "start", "end"] | None = Field(
        default=None, description="center at cut (default between two clips) / start at cut / end at cut")


@command("transition.add", AddTransition)
def transition_add(ctx: Context, p: AddTransition):
    """Apply a transition at a clip's start or end (drag from Effects > Video/Audio Transitions).
    Between two adjacent clips it is a two-sided transition; otherwise a fade in/out."""
    seq, tid, clip, _ = _ctx(ctx, p.sequence_id, p.clip_id)
    kind = seq["tracks"][tid]["kind"]
    ttype = p.type or ("cross_dissolve" if kind == "video" else "constant_power")
    if TRANSITIONS[ttype]["kind"] != kind:
        raise CommandError("WRONG_TRACK_KIND", f"{TRANSITIONS[ttype]['label']} is a {TRANSITIONS[ttype]['kind']} transition")
    dur = resolve_time(p.duration, seq["settings"])
    return {"transition_id": _place_transition(ctx, seq, tid, clip, p.edge, ttype, dur, p.alignment)}


class ApplyDefault(Params):
    sequence_id: str | None = None
    kind: Literal["video", "audio", "both"] = "video"
    clip_ids: list[str] | None = Field(default=None, description="apply to both ends of these clips; default: the edit point nearest the playhead on targeted tracks")
    duration: TimeValue = "1s"


@command("transition.apply_default", ApplyDefault)
def transition_apply_default(ctx: Context, p: ApplyDefault):
    """Ctrl+D (video), Ctrl+Shift+D (audio), Shift+D (both): apply the default transition."""
    seq = seq_of(ctx.doc, p.sequence_id)
    dur = resolve_time(p.duration, seq["settings"])
    kinds = ("video", "audio") if p.kind == "both" else (p.kind,)
    made = []
    if p.clip_ids:
        for cid in p.clip_ids:
            tid, clip = find_clip(seq, cid)
            if seq["tracks"][tid]["kind"] not in kinds:
                continue
            require_unlocked(seq, tid)
            ttype = "cross_dissolve" if seq["tracks"][tid]["kind"] == "video" else "constant_power"
            for edge in ("start", "end"):
                live = seq["tracks"][tid]["clips"].get(cid)
                if live:
                    made.append(_place_transition(ctx, seq, tid, live, edge, ttype, dur, None))
        return {"transition_ids": made}
    t = seq["playhead"]
    for kind in kinds:
        for tid in seq[f"{kind}_tracks"]:
            track = seq["tracks"][tid]
            if not track["targeted"] or track["locked"]:
                continue
            best = None
            for c in clips_sorted(track):
                for edge, at in (("start", c["start"]), ("end", c["end"])):
                    if best is None or abs(at - t) < abs(best[2] - t):
                        best = (c, edge, at)
            if best is None:
                continue
            c, edge, at = best
            nb = _neighbor(track, c, edge)
            if edge == "start" and nb is not None:
                c, edge = nb, "end"  # one transition for the edit point
            ttype = "cross_dissolve" if kind == "video" else "constant_power"
            made.append(_place_transition(ctx, seq, tid, c, edge, ttype, dur, None))
    if not made:
        raise CommandError("NO_EDIT_POINT", "no clips on the targeted tracks")
    return {"transition_ids": made}


def _find_transition(seq: dict, transition_id: str) -> tuple[str, dict]:
    for tid, track in seq["tracks"].items():
        tr = track.get("transitions", {}).get(transition_id)
        if tr:
            return tid, tr
    raise CommandError("NO_TRANSITION", f"transition not found: {transition_id}")


class SetTransition(Params):
    sequence_id: str | None = None
    transition_id: str
    type: TransitionType | None = None
    duration: TimeValue | None = None
    alignment: Literal["center", "start", "end"] | None = None


@command("transition.set", SetTransition)
def transition_set(ctx: Context, p: SetTransition):
    """Change a transition's type, duration or alignment."""
    seq = seq_of(ctx.doc, p.sequence_id)
    tid, tr = _find_transition(seq, p.transition_id)
    require_unlocked(seq, tid)
    path = ["sequences", seq["id"], "tracks", tid, "transitions", tr["id"]]
    if p.type and p.type != tr["type"]:
        if TRANSITIONS[p.type]["kind"] != seq["tracks"][tid]["kind"]:
            raise CommandError("WRONG_TRACK_KIND", "transition kind does not match the track")
        ctx.m.set(path + ["type"], p.type)
    if p.duration is not None:
        d = resolve_time(p.duration, seq["settings"])
        if d <= 0:
            raise CommandError("BAD_VALUE", "duration must be positive")
        ctx.m.set(path + ["duration"], d)
    if p.alignment and p.alignment != tr["alignment"]:
        if (p.alignment == "center") and not (tr.get("clip_a") and tr.get("clip_b")):
            raise CommandError("BAD_VALUE", "a single-sided transition cannot be centered on the cut")
        ctx.m.set(path + ["alignment"], p.alignment)
    w = transition_window(seq["tracks"][tid], {**tr, **({"duration": d} if p.duration is not None else {})})
    return {"window": list(w[:2]) if w else None}


class TransitionRef(Params):
    sequence_id: str | None = None
    transition_id: str


@command("transition.remove", TransitionRef)
def transition_remove(ctx: Context, p: TransitionRef):
    """Delete a transition."""
    seq = seq_of(ctx.doc, p.sequence_id)
    tid, tr = _find_transition(seq, p.transition_id)
    require_unlocked(seq, tid)
    ctx.m.delete(["sequences", seq["id"], "tracks", tid, "transitions", tr["id"]])


def prune_transitions(ctx: Context) -> None:
    """Drop transitions whose clips were deleted or are no longer adjacent
    (run after every edit, inside the same undo step)."""
    for sid, seq in ctx.doc["sequences"].items():
        for tid, track in seq["tracks"].items():
            for trid, tr in list(track.get("transitions", {}).items()):
                if transition_window(track, tr) is None:
                    ctx.m.delete(["sequences", sid, "tracks", tid, "transitions", trid])
