"""Shorts: the title copy (sub line above, main line below) on the channel's title graphic."""

from __future__ import annotations

from pydantic import Field

from .. import text_fit
from .motion import SetLayer, graphic_set_layer
from .project import seq_of
from .registry import CommandError, Context, Params, command
from .timeline import clip_path, find_clip

TITLE_CLIP = "쇼츠 타이틀"


class SetCopy(Params):
    sequence_id: str | None = None
    sub: str | None = Field(default=None, description="sub copy: the upper, smaller line (null keeps it)")
    main: str | None = Field(default=None, description="main copy: the lower, larger line (null keeps it)")
    clip_id: str | None = Field(default=None, description=f"title graphic; default: the graphic clip named '{TITLE_CLIP}'")
    margin: float = Field(default=0.0, ge=0, description="px to keep clear at the left and right frame edges")
    dry_run: bool = Field(default=False, description="only measure the copy; change nothing")


def _title_clip(seq: dict, clip_id: str | None) -> tuple[str, dict]:
    if clip_id:
        return find_clip(seq, clip_id)
    found = [(tid, c) for tid in seq["video_tracks"] for c in seq["tracks"][tid]["clips"].values()
             if c.get("graphic") and c["name"] == TITLE_CLIP]
    if not found:
        raise CommandError("NO_TITLE", f"no graphic clip named '{TITLE_CLIP}' in this sequence; pass clip_id")
    return min(found, key=lambda tc: tc[1]["start"])


def _copy_layers(clip: dict) -> tuple[dict, dict]:
    texts = [l for l in clip["graphic"]["layers"] if l.get("type", "text") == "text" and l.get("visible", True)]
    if len(texts) < 2:
        raise CommandError("NOT_COPY", "the title graphic needs two visible text layers (sub above, main below)")
    texts.sort(key=lambda l: l["position"][1])
    return texts[0], texts[1]


@command("shorts.set_copy", SetCopy)
def shorts_set_copy(ctx: Context, p: SetCopy):
    """Set the Shorts title copy (sub line above, main line below) and check that each line stays inside the frame.

    Each line is rendered in its own font, size, stroke and shadow and measured where it lands
    on screen. An overflowing line, a line break, or a character the font cannot draw is refused
    with the measured bounds and max_hangul (how many Hangul syllables fit on that line).
    dry_run measures without editing."""
    seq = seq_of(ctx.doc, p.sequence_id)
    tid, clip = _title_clip(seq, p.clip_id)
    width = seq["settings"]["width"]
    report, problems = {}, []
    for role, layer, text in zip(("sub", "main"), _copy_layers(clip), (p.sub, p.main)):
        candidate = dict(layer, text=layer["text"] if text is None else text, runs=None if text is not None else layer.get("runs"))
        bounds = text_fit.layer_bounds(seq, clip, candidate)
        line = {
            "layer_id": layer["id"], "text": candidate["text"],
            "bounds": [round(v, 1) for v in bounds] if bounds else None,
            "fits": text_fit.fits(bounds, width, p.margin),
            "max_hangul": text_fit.max_repeat(seq, clip, candidate, margin=p.margin),
            "missing_glyphs": text_fit.missing_glyphs(candidate),
        }
        if bounds:
            line["overflow_px"] = round(max(0.0, p.margin - bounds[0], bounds[2] - (width - p.margin)), 1)
        report[role] = line
        if text is None:
            continue
        if not text.strip() or "\n" in text or "\r" in text:
            problems.append(f"{role}: one non-empty line")
        elif line["missing_glyphs"]:
            problems.append(f"{role}: the font cannot draw {''.join(line['missing_glyphs'])}")
        elif not line["fits"]:
            problems.append(f"{role}: {line['overflow_px']}px past the frame (max {line['max_hangul']} Hangul)")
    result = {"clip_id": clip["id"], "frame_width": width, "margin": p.margin, **report}
    if problems:
        raise CommandError("COPY_DOES_NOT_FIT", "; ".join(problems), **result)
    if not p.dry_run:
        name = clip["name"]
        for role, text in (("sub", p.sub), ("main", p.main)):
            if text is not None:
                graphic_set_layer(ctx, SetLayer(sequence_id=seq["id"], clip_id=clip["id"], layer_id=report[role]["layer_id"], text=text))
        # set_layer names a clip after its first layer's text; the title clip keeps its name
        if clip["name"] != name:
            ctx.m.set(clip_path(seq, tid, clip["id"]) + ["name"], name)
    return result
