"""Standard effects (Effects panel > Video Effects) and transitions."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Literal

from pydantic import Field

from .. import timebase
from ..effects import ANIMATABLE, EFFECTS, LEVEL_RANGES, TRANSITIONS, default_params, media_of, param, transition_window
from ..model import EffectType, TransitionType, new_id
from ..motion import source_size, source_time
from .. import tracks as tracks_
from .motion import INTERPS, _at, _ctx, _interp_near, handles_of, with_ease, with_interp
from .project import seq_of
from .registry import CommandError, Context, Params, TimeValue, command, resolve_time
from .timeline import clip_path, clips_sorted, find_clip, require_unlocked

ParamValue = float | bool | list[float]


def _effect(clip: dict, effect_id: str) -> tuple[int, dict]:
    for i, e in enumerate(clip.get("effects") or []):
        if e["id"] == effect_id:
            return i, e
    raise CommandError("NO_EFFECT", f"effect not found: {effect_id}")


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
    if ptype == "level":
        lo, hi = LEVEL_RANGES[name]
        if not lo <= v <= hi:
            raise CommandError("BAD_VALUE", f"{name} is {lo}-{hi}")
        return v
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


@dataclass
class Host:
    """Where an effect stack lives: a clip, or a track (track effects). For a track,
    ``clip`` is the clip used as the time reference (its In/Out anchor the keyframes)."""
    seq: dict
    tid: str
    clip: dict | None
    track: bool
    effects: list
    path: list
    w: float
    h: float
    kind: str

    def timing(self, effect: dict) -> dict:
        if not self.track:
            return self.clip
        if self.clip is None:
            return {"start": 0, "end": 1, "source_in": 0, "speed": 1.0}
        return tracks_.timing_clip(effect, self.clip)

    def at(self, at: TimeValue | None) -> int:
        if self.clip is None:
            return self.seq["playhead"] if at is None else resolve_time(at, self.seq["settings"])
        return _at(self.seq, self.clip, at)


def _host(ctx: Context, sequence_id: str | None, clip_id: str | None, track_id: str | None) -> Host:
    if track_id is None:
        if clip_id is None:
            raise CommandError("BAD_PARAMS", "give clip_id (clip effects) or track_id (track effects)")
        seq, tid, clip, media = _ctx(ctx, sequence_id, clip_id)
        w, h = source_size(clip, seq, media)
        return Host(seq, tid, clip, False, list(clip.get("effects") or []), _effects_path(seq, tid, clip), w, h,
                    seq["tracks"][tid]["kind"])
    seq = seq_of(ctx.doc, sequence_id)
    track = seq["tracks"].get(track_id)
    if track is None:
        raise CommandError("NO_TRACK", f"track not found: {track_id}")
    require_unlocked(seq, track_id)
    if clip_id is not None:
        ref = track["clips"].get(clip_id)
        if ref is None:
            raise CommandError("NO_CLIP", f"clip {clip_id} is not on track {track['name']}")
    else:
        ph = seq["playhead"]
        ordered = clips_sorted(track)
        ref = next((c for c in ordered if c["start"] <= ph < c["end"]), ordered[0] if ordered else None)
    st = seq["settings"]
    return Host(seq, track_id, ref, True, list(track.get("effects") or []), ["sequences", seq["id"], "tracks", track_id, "effects"],
                st["width"], st["height"], track["kind"])


def _update(ctx: Context, host: Host, i: int, fn) -> None:
    """Patch one effect (lists are replaced wholesale in the patch model)."""
    e = copy.deepcopy(host.effects[i])
    e.setdefault("keyframes", {})
    fn(e)
    stack = list(host.effects)
    stack[i] = e
    ctx.m.set(host.path, stack)
    host.effects = stack


def seq_time_of(timing: dict, src: int) -> float:
    return timing["start"] + (src - timing["source_in"]) / (timing["speed"] or 1.0)


class AddEffect(Params):
    sequence_id: str | None = None
    clip_ids: list[str] = Field(default_factory=list, description="clips to apply to")
    track_ids: list[str] = Field(default_factory=list, description="or tracks: a track effect applies to every clip of the track "
                                                                  "(video: after the clip's Motion; audio: on the track bus)")
    type: EffectType
    params: dict[str, ParamValue] = Field(default_factory=dict)
    index: int | None = Field(default=None, description="position in the effect stack; default last")
    anchor: Literal["in", "out", "stretch"] = Field(default="in", description="track effects: keyframe times count from each "
                                                                              "clip's In / Out, or stretch over it")


@command("effect.add", AddEffect)
def effect_add(ctx: Context, p: AddEffect):
    """Apply an effect (drag from the Effects panel) to clips or whole tracks. Video effects run in stack
    order before Motion; audio effects after Audio Gain and before Volume. Track effects apply to every
    clip of the track without changing the clips (caption tracks included)."""
    if not p.clip_ids and not p.track_ids:
        raise CommandError("BAD_PARAMS", "give clip_ids or track_ids")
    ids = []
    kind = media_of(p.type)
    hosts = [_host(ctx, p.sequence_id, cid, None) for cid in p.clip_ids]
    hosts += [_host(ctx, p.sequence_id, None, tid) for tid in p.track_ids]
    for host in hosts:
        if host.kind != kind:
            raise CommandError("NOT_VIDEO" if kind == "video" else "NOT_AUDIO", f"{kind} effects go on {kind} clips and tracks")
        params = default_params(p.type, host.w, host.h)
        for k, v in p.params.items():
            params[k] = _check_param(p.type, k, v)
        effect = {"id": new_id(), "type": p.type, "enabled": True, "params": params, "keyframes": {}}
        if host.track:
            effect["anchor"], effect["ref_duration"] = p.anchor, None
        stack = list(host.effects)
        stack.insert(len(stack) if p.index is None else max(0, min(p.index, len(stack))), effect)
        ctx.m.set(host.path, stack)
        ids.append(effect["id"])
    return {"effect_ids": ids}


class ApplyPreset(Params):
    sequence_id: str | None = None
    clip_ids: list[str] = Field(default_factory=list)
    track_ids: list[str] = Field(default_factory=list, description="or tracks: the preset becomes track effects, its keyframes "
                                                                  "fitted to every clip of the track (each cue animates on its own)")
    preset_id: str = Field(description="an id from presets.list (the Effects panel's Presets folder)")


@command("effect.apply_preset", ApplyPreset)
def effect_apply_preset(ctx: Context, p: ApplyPreset):
    """Apply an effect preset: its effects are appended with keyframes fitted to each clip
    (anchored to the clip's in or out point, or scaled to its length, as saved). On a track, the
    effects become track effects that fit every clip of the track the same way."""
    from .. import presets

    preset = presets.get(p.preset_id)
    if preset is None:
        raise CommandError("NO_PRESET", f"preset not found: {p.preset_id}")
    if not p.clip_ids and not p.track_ids:
        raise CommandError("BAD_PARAMS", "give clip_ids or track_ids")
    ids = []
    for cid in p.clip_ids:
        host = _host(ctx, p.sequence_id, cid, None)
        if host.kind != "video":
            raise CommandError("NOT_VIDEO", "effect presets go on video clips")
        added = []
        for fx in presets.fit(preset, host.clip, host.w, host.h):
            fx["params"] = {**default_params(fx["type"], host.w, host.h), **fx["params"]}
            added.append(fx)
        ctx.m.set(host.path, host.effects + added)
        ids += [fx["id"] for fx in added]
    for tid in p.track_ids:
        host = _host(ctx, p.sequence_id, None, tid)
        if host.kind != "video":
            raise CommandError("NOT_VIDEO", "effect presets go on video tracks")
        added = []
        for fx in presets.fit_track(preset, host.w, host.h):
            fx["params"] = {**default_params(fx["type"], host.w, host.h), **fx["params"]}
            added.append(fx)
        ctx.m.set(host.path, host.effects + added)
        ids += [fx["id"] for fx in added]
    return {"effect_ids": ids}


class EffectRef(Params):
    sequence_id: str | None = None
    clip_id: str | None = Field(default=None, description="the clip whose effect it is; with track_id: the clip used as time reference")
    track_id: str | None = Field(default=None, description="a track effect: the effect is on this track")
    effect_id: str


@command("effect.remove", EffectRef)
def effect_remove(ctx: Context, p: EffectRef):
    """Delete an effect from a clip or track."""
    host = _host(ctx, p.sequence_id, p.clip_id, p.track_id)
    i, _e = _effect({"effects": host.effects}, p.effect_id)
    stack = list(host.effects)
    del stack[i]
    ctx.m.set(host.path, stack)


class SetEffect(EffectRef):
    enabled: bool | None = Field(default=None, description="the fx toggle")
    index: int | None = Field(default=None, description="move to this stack position")
    anchor: Literal["in", "out", "stretch"] | None = Field(default=None, description="track effects: what keyframe times count from")


@command("effect.set", SetEffect)
def effect_set(ctx: Context, p: SetEffect):
    """Toggle an effect on/off, reorder it in the stack, or (track effects) change its anchor."""
    host = _host(ctx, p.sequence_id, p.clip_id, p.track_id)
    i, e = _effect({"effects": host.effects}, p.effect_id)
    if p.enabled is not None and e["enabled"] != p.enabled:
        _update(ctx, host, i, lambda x: x.update(enabled=p.enabled))
    if p.anchor is not None and host.track and e.get("anchor") != p.anchor:
        ref = host.clip

        def re_anchor(x):
            # keep the keyframes where they are on the reference clip
            old_t = host.timing(x)
            x["anchor"] = p.anchor
            if p.anchor == "stretch" and ref is not None:
                x["ref_duration"] = ref["end"] - ref["start"]
            new_t = host.timing(x)
            for name, kfs in x["keyframes"].items():
                x["keyframes"][name] = [dict(k, t=source_time(new_t, round(seq_time_of(old_t, k["t"])))) for k in kfs]
        _update(ctx, host, i, re_anchor)
    if p.index is not None:
        stack = list(host.effects)
        item = stack.pop(i)
        stack.insert(max(0, min(p.index, len(stack))), item)
        if [x["id"] for x in stack] != [x["id"] for x in host.effects]:
            ctx.m.set(host.path, stack)


class SetParam(EffectRef):
    param: str
    value: ParamValue
    at: TimeValue | None = Field(default=None, description="sequence time used when the parameter is animated; default playhead")


@command("effect.set_param", SetParam)
def effect_set_param(ctx: Context, p: SetParam):
    """Set an effect parameter; if it is animated, adds or updates the keyframe at `at`."""
    host = _host(ctx, p.sequence_id, p.clip_id, p.track_id)
    i, e = _effect({"effects": host.effects}, p.effect_id)
    v = _check_param(e["type"], p.param, p.value)
    kfs = e.get("keyframes", {}).get(p.param)
    if not kfs:
        _update(ctx, host, i, lambda x: x["params"].__setitem__(p.param, v))
        return {"animated": False}
    t = source_time(host.timing(e), host.at(p.at))
    old = next((k for k in kfs if k["t"] == t), None)
    new = sorted([k for k in kfs if k["t"] != t] + [{**old, "v": v} if old else {"t": t, "v": v, "interp": _interp_near(kfs, t)}],
                 key=lambda k: k["t"])
    _update(ctx, host, i, lambda x: x["keyframes"].__setitem__(p.param, new))
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
    host = _host(ctx, p.sequence_id, p.clip_id, p.track_id)
    i, e = _effect({"effects": host.effects}, p.effect_id)
    _require_animatable(e, p.param)
    at = host.at(p.at)
    timing = host.timing(e)
    current = param(e, p.param, timing, at, host.w, host.h)
    if e.get("keyframes", {}).get(p.param):
        def stop(x):
            del x["keyframes"][p.param]
            x["params"][p.param] = current
        _update(ctx, host, i, stop)
        return {"animated": False}
    first = [{"t": source_time(timing, at), "v": current, "interp": "linear"}]
    _update(ctx, host, i, lambda x: x["keyframes"].__setitem__(p.param, first))
    return {"animated": True}


class ParamKeyframe(ParamRef):
    op: Literal["add", "remove", "toggle"] = "toggle"
    interp: Literal["linear", "ease", "hold", "bezier", "auto_bezier", "continuous_bezier"] | None = None


class ParamEase(ParamRef):
    t: int = Field(description="the keyframe's time (source ticks, as stored)")
    side: Literal["in", "out"]
    speed: float = Field(description="value units per second (points: pixels per second along the path)")
    influence: float = Field(ge=0, le=1, description="fraction of the segment, 0-1")


@command("effect.set_keyframe_ease", ParamEase)
def effect_set_keyframe_ease(ctx: Context, p: ParamEase):
    """Bezier handle of an effect-parameter keyframe: speed and influence on one side."""
    host = _host(ctx, p.sequence_id, p.clip_id, p.track_id)
    i, e = _effect({"effects": host.effects}, p.effect_id)
    kfs = e.get("keyframes", {}).get(p.param)
    if not kfs:
        raise CommandError("NOT_ANIMATED", f"{p.param} has no keyframes")
    new = with_ease(kfs, p.t, p.side, p.speed, p.influence)
    _update(ctx, host, i, lambda x: x["keyframes"].__setitem__(p.param, new))


@command("effect.keyframe", ParamKeyframe)
def effect_keyframe(ctx: Context, p: ParamKeyframe):
    """Add / remove / toggle an effect-parameter keyframe at a time, or change its interpolation."""
    host = _host(ctx, p.sequence_id, p.clip_id, p.track_id)
    i, e = _effect({"effects": host.effects}, p.effect_id)
    _require_animatable(e, p.param)
    at = host.at(p.at)
    timing = host.timing(e)
    t = source_time(timing, at)
    seq = host.seq
    tol = round(timebase.ticks_per_frame(seq["settings"]["fps_num"], seq["settings"]["fps_den"]) * abs(timing["speed"]) / 2)
    kfs = list(e.get("keyframes", {}).get(p.param) or [])
    hit = next((k for k in kfs if abs(k["t"] - t) <= tol), None)

    def put(new):
        def fn(x):
            if new:
                x["keyframes"][p.param] = new
            else:
                x["keyframes"].pop(p.param, None)
                x["params"][p.param] = hit["v"]
        _update(ctx, host, i, fn)

    if hit and p.op in ("remove", "toggle") and p.interp is None:
        kfs.remove(hit)
        put(kfs)
        return {"removed": True}
    if hit and p.interp:
        put([with_interp(k, p.interp) if k is hit else k for k in kfs])
        return {"interp": p.interp}
    if p.op == "remove":
        raise CommandError("NO_KEYFRAME", "no keyframe at that time")
    kfs.append({"t": t, "v": param(e, p.param, timing, at, host.w, host.h), "interp": p.interp or _interp_near(kfs, t)})
    put(sorted(kfs, key=lambda k: k["t"]))
    return {"added": True, "t": t}


class SetParamKeyframes(EffectRef):
    param: str
    keyframes: list[dict] = Field(description="[{t: clip-relative time, v, interp}] - replaces all; [] stops animating. "
                                              "Track effects: t counts from each clip's In (anchor in) or Out (anchor out, negative)")


@command("effect.set_keyframes", SetParamKeyframes)
def effect_set_keyframes(ctx: Context, p: SetParamKeyframes):
    """Replace an effect parameter's keyframes (times relative to the clip start)."""
    host = _host(ctx, p.sequence_id, p.clip_id, p.track_id)
    i, e = _effect({"effects": host.effects}, p.effect_id)
    _require_animatable(e, p.param)
    seq = host.seq
    if not p.keyframes:
        if e.get("keyframes", {}).get(p.param):
            timing = host.timing(e)
            v0 = param(e, p.param, timing, timing["start"], host.w, host.h)

            def stop(x):
                del x["keyframes"][p.param]
                x["params"][p.param] = v0
            _update(ctx, host, i, stop)
        return {"count": 0}
    out = []
    for k in p.keyframes:
        raw = k["t"]
        neg = isinstance(raw, str) and raw.strip().startswith("-")
        rel = resolve_time(raw.strip()[1:] if neg else raw, seq["settings"]) * (-1 if neg else 1)
        interp = k.get("interp", "linear")
        if interp not in INTERPS:
            raise CommandError("BAD_VALUE", "interp is linear | ease | hold | bezier | auto_bezier | continuous_bezier")
        t = rel if host.track else source_time(host.clip, host.clip["start"] + rel)
        out.append({"t": t, "v": _check_param(e["type"], p.param, k["v"]), "interp": interp, **handles_of(k)})
    out.sort(key=lambda k: k["t"])
    _update(ctx, host, i, lambda x: x["keyframes"].__setitem__(p.param, out))
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
