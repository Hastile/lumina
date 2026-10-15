"""Keyframe selections in Effect Controls: change interpolation, move, delete.

A selection spans properties: Motion/Opacity/Volume properties (``prop``) and
effect parameters (``effect_id`` + ``param``), each with the keyframe times
(source ticks, as stored) that are selected.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..effects import param as effect_param
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
