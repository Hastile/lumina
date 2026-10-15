"""Keyframe selections in Effect Controls: change interpolation, move, delete.

A selection spans properties: Motion/Opacity/Volume properties (``prop``) and
effect parameters (``effect_id`` + ``param``), each with the keyframe times
(source ticks, as stored) that are selected.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..effects import EFFECTS, param as effect_param
from ..model import KeyframeProp
from ..motion import source_size, value_at
from .effects import _effect, _effects_path
from .motion import MENU_INTERPS, _ctx, _write_static, set_interp
from .registry import CommandError, Context, Params, command
from .timeline import clip_path


class KeyframeTarget(BaseModel):
    prop: KeyframeProp | None = Field(default=None, description="a Motion/Opacity/Volume property")
    effect_id: str | None = Field(default=None, description="or an effect's parameter: the effect ...")
    param: str | None = Field(default=None, description="... and the parameter name")
    times: list[int] = Field(min_length=1, description="the selected keyframes' times (source ticks, as stored)")


class KeyframeSelection(Params):
    sequence_id: str | None = None
    clip_id: str
    targets: list[KeyframeTarget] = Field(min_length=1)


def _apply(ctx: Context, p: KeyframeSelection, fn) -> int:
    """Run ``fn(kfs, times) -> new kfs | None`` on every target and write the results in one patch
    per property / effect stack. Returns how many keyframes were selected."""
    seq, tid, clip, media = _ctx(ctx, p.sequence_id, p.clip_id)
    path = clip_path(seq, tid, clip["id"])
    stack = None
    n = 0
    for tg in p.targets:
        times = set(tg.times)
        if tg.prop:
            kfs = clip["keyframes"].get(tg.prop)
            if not kfs:
                raise CommandError("NOT_ANIMATED", f"{tg.prop} has no keyframes")
            n += sum(1 for k in kfs if k["t"] in times)
            new = fn(kfs, times)
            if new:
                ctx.m.set(path + ["keyframes", tg.prop], new)
            else:
                _write_static(ctx, seq, tid, clip, tg.prop, value_at(clip, tg.prop, seq["playhead"], seq, media))
                ctx.m.delete(path + ["keyframes", tg.prop])
        elif tg.effect_id and tg.param:
            if stack is None:
                stack = [dict(e, keyframes=dict(e.get("keyframes", {})), params=dict(e["params"])) for e in clip["effects"]]
            i, e = _effect(clip, tg.effect_id)
            kfs = stack[i]["keyframes"].get(tg.param)
            if not kfs:
                raise CommandError("NOT_ANIMATED", f"{tg.param} has no keyframes")
            n += sum(1 for k in kfs if k["t"] in times)
            new = fn(kfs, times)
            if new:
                stack[i]["keyframes"][tg.param] = new
            else:
                w, h = source_size(clip, seq, media)
                stack[i]["params"][tg.param] = effect_param(e, tg.param, clip, seq["playhead"], w, h)
                del stack[i]["keyframes"][tg.param]
        else:
            raise CommandError("BAD_TARGET", "a target needs prop, or effect_id and param")
    if stack is not None:
        ctx.m.set(_effects_path(seq, tid, clip), stack)
    if not n:
        raise CommandError("NO_KEYFRAME", "no keyframes at those times")
    return n


class SetInterp(KeyframeSelection):
    interp: Literal["linear", "bezier", "auto_bezier", "continuous_bezier", "hold", "ease_in", "ease_out", "ease"] = Field(
        description="Premiere's Temporal Interpolation menu; ease = Easy Ease (both sides)")


@command("keyframes.set_interp", SetInterp)
def keyframes_set_interp(ctx: Context, p: SetInterp):
    """Temporal interpolation of selected keyframes (Linear, Bezier, Auto Bezier, Continuous Bezier,
    Hold, Ease In, Ease Out), keeping the curve's shape where the new type has handles."""
    assert p.interp in MENU_INTERPS
    n = _apply(ctx, p, lambda kfs, times: set_interp(kfs, times, p.interp))
    return {"count": n, "interp": p.interp}


class MoveKeyframes(KeyframeSelection):
    delta: int = Field(description="sequence ticks to move by (snapped to frames by the caller)")


@command("keyframes.move", MoveKeyframes)
def keyframes_move(ctx: Context, p: MoveKeyframes):
    """Drag selected keyframes in time; a keyframe they land on is replaced (as in Premiere)."""
    _, _, clip, _ = _ctx(ctx, p.sequence_id, p.clip_id)
    d = round(p.delta * clip["speed"])

    def move(kfs, times):
        moved = {k["t"] + d: dict(k, t=k["t"] + d) for k in kfs if k["t"] in times}
        kept = [k for k in kfs if k["t"] not in times and k["t"] not in moved]
        return sorted(kept + list(moved.values()), key=lambda k: k["t"])

    n = _apply(ctx, p, move)
    return {"count": n, "delta_source": d}


@command("keyframes.delete", KeyframeSelection)
def keyframes_delete(ctx: Context, p: KeyframeSelection):
    """Delete selected keyframes; a property left without keyframes keeps the value at the playhead."""
    n = _apply(ctx, p, lambda kfs, times: [k for k in kfs if k["t"] not in times])
    return {"count": n}


class ScaleKeyframes(KeyframeSelection):
    pivot: int = Field(description="sequence time that stays put")
    factor: float = Field(gt=0, le=100, description="new spacing / old spacing (Alt-drag the outer keyframe of a selection)")


@command("keyframes.scale", ScaleKeyframes)
def keyframes_scale(ctx: Context, p: ScaleKeyframes):
    """Stretch or squeeze the selected keyframes in time around a pivot, frame-snapped; Bezier handle
    speeds scale with them so the curves keep their shape. A keyframe they land on is replaced."""
    seq, _, clip, _ = _ctx(ctx, p.sequence_id, p.clip_id)
    from .. import timebase

    tpf = timebase.ticks_per_frame(seq["settings"]["fps_num"], seq["settings"]["fps_den"])
    pivot = clip["source_in"] + (p.pivot - clip["start"]) * clip["speed"]
    step = tpf * clip["speed"]

    def scale(kfs, times):
        moved = {}
        for k in kfs:
            if k["t"] not in times:
                continue
            nt = pivot + (k["t"] - pivot) * p.factor
            nt = round(clip["source_in"] + round((nt - clip["source_in"]) / step) * step)
            nk = dict(k, t=nt)
            for side in ("ease_in", "ease_out"):
                if nk.get(side):
                    nk[side] = [nk[side][0] / p.factor, nk[side][1]]
            moved[nt] = nk
        kept = [k for k in kfs if k["t"] not in times and k["t"] not in moved]
        return sorted(kept + list(moved.values()), key=lambda k: k["t"])

    n = _apply(ctx, p, scale)
    return {"count": n}


class PastedProperty(BaseModel):
    prop: KeyframeProp | None = None
    effect_type: str | None = Field(default=None, description="an effect parameter: the first effect of this type on the clip ...")
    param: str | None = None
    keyframes: list[dict] = Field(min_length=1, description="keyframes with `t` relative to the first copied keyframe (sequence ticks)")


class PasteKeyframes(Params):
    sequence_id: str | None = None
    clip_id: str
    at: int = Field(description="sequence time where the first copied keyframe lands (the playhead)")
    properties: list[PastedProperty] = Field(min_length=1)


@command("keyframes.paste", PasteKeyframes)
def keyframes_paste(ctx: Context, p: PasteKeyframes):
    """Paste copied keyframes (Ctrl+V in Effect Controls) at the playhead, into the same properties of
    this clip: Motion / Opacity / Volume, or the first effect of the copied effect's type. Keyframes at
    the same times are replaced; a property that was static becomes animated."""
    from ..model import Keyframe

    seq, tid, clip, media = _ctx(ctx, p.sequence_id, p.clip_id)
    path = clip_path(seq, tid, clip["id"])
    to_src = lambda dt: round(clip["source_in"] + (p.at + dt - clip["start"]) * clip["speed"])  # noqa: E731
    stack = None
    skipped = []
    count = 0
    for prop in p.properties:
        new = [dict(Keyframe.model_validate({**k, "t": to_src(k["t"])}).model_dump(mode="json")) for k in prop.keyframes]
        for k in new:
            for side in ("ease_in", "ease_out"):  # handle speeds are per source second
                if k.get(side):
                    k[side] = [k[side][0] / clip["speed"], k[side][1]]
        times = {k["t"] for k in new}
        if prop.prop:
            old = [k for k in clip["keyframes"].get(prop.prop, []) if k["t"] not in times]
            ctx.m.set(path + ["keyframes", prop.prop], sorted(old + new, key=lambda k: k["t"]))
            count += len(new)
        elif prop.effect_type and prop.param:
            if stack is None:
                stack = [dict(e, keyframes=dict(e.get("keyframes", {}))) for e in clip["effects"]]
            e = next((e for e in stack if e["type"] == prop.effect_type), None)
            if e is None or prop.param not in EFFECTS[e["type"]]["params"]:
                skipped.append(f"{prop.effect_type}.{prop.param}")
                continue
            old = [k for k in e["keyframes"].get(prop.param, []) if k["t"] not in times]
            e["keyframes"][prop.param] = sorted(old + new, key=lambda k: k["t"])
            count += len(new)
        else:
            raise CommandError("BAD_TARGET", "a pasted property needs prop, or effect_type and param")
    if stack is not None:
        ctx.m.set(_effects_path(seq, tid, clip), stack)
    if not count:
        raise CommandError("NOTHING_PASTED", "this clip has none of the copied properties" + (f" ({', '.join(skipped)})" if skipped else ""))
    return {"count": count, "skipped": skipped}
