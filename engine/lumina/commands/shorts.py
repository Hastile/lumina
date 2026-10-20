"""Shorts: the title copy (sub line above, main line below) on the channel's title graphic."""

from __future__ import annotations

from pydantic import Field

from .. import text_fit
from .motion import SetLayer, graphic_set_layer
from .project import seq_of
from .registry import CommandError, Context, Params, TimeValue, command, resolve_time
from .timeline import clip_path, find_clip, split_clip

TITLE_CLIP = "쇼츠 타이틀"


class SetCopy(Params):
    sequence_id: str | None = None
    sub: str | None = Field(default=None, description="sub copy: the upper, smaller line (null keeps it)")
    main: str | None = Field(default=None, description="main copy: the lower, larger line (null keeps it)")
    hide: list[str] = Field(default_factory=list, description="words of the copy shown as '?' (one per character, spaces kept) until reveal_at")
    reveal_at: TimeValue | None = Field(default=None, description="when the hidden words appear: the start of the caption where the narration first says them")
    clip_id: str | None = Field(default=None, description=f"title graphic; default: every graphic clip named '{TITLE_CLIP}'")
    margin: float = Field(default=0.0, ge=0, description="px to keep clear at the left and right frame edges")
    dry_run: bool = Field(default=False, description="only measure the copy; change nothing")


def _title_clips(seq: dict, clip_id: str | None) -> list[tuple[str, dict]]:
    """The title graphic; a title already cut for a reveal is several clips named alike, in time order."""
    if clip_id:
        return [find_clip(seq, clip_id)]
    found = [(tid, c) for tid in seq["video_tracks"] for c in seq["tracks"][tid]["clips"].values()
             if c.get("graphic") and c["name"] == TITLE_CLIP]
    if not found:
        raise CommandError("NO_TITLE", f"no graphic clip named '{TITLE_CLIP}' in this sequence; pass clip_id")
    return sorted(found, key=lambda tc: tc[1]["start"])


def _copy_layers(clip: dict) -> tuple[dict, dict]:
    texts = [l for l in clip["graphic"]["layers"] if l.get("type", "text") == "text" and l.get("visible", True)]
    if len(texts) < 2:
        raise CommandError("NOT_COPY", "the title graphic needs two visible text layers (sub above, main below)")
    texts.sort(key=lambda l: l["position"][1])
    return texts[0], texts[1]


def mask(text: str, words: list[str]) -> str:
    for w in words:
        text = text.replace(w, "".join(ch if ch.isspace() else "?" for ch in w))
    return text


def _measure(seq: dict, clip: dict, layer: dict, text: str, margin: float) -> dict:
    candidate = dict(layer, text=text, runs=layer.get("runs") if text == layer["text"] else None)
    bounds = text_fit.layer_bounds(seq, clip, candidate)
    line = {
        "layer_id": layer["id"], "text": text,
        "bounds": [round(v, 1) for v in bounds] if bounds else None,
        "fits": text_fit.fits(bounds, seq["settings"]["width"], margin),
        "max_hangul": text_fit.max_repeat(seq, clip, candidate, margin=margin),
        "missing_glyphs": text_fit.missing_glyphs(candidate),
    }
    if bounds:
        line["overflow_px"] = round(max(0.0, margin - bounds[0], bounds[2] - (seq["settings"]["width"] - margin)), 1)
    return line


@command("shorts.set_copy", SetCopy)
def shorts_set_copy(ctx: Context, p: SetCopy):
    """Set the Shorts title copy (sub line above, main line below) and check that each line stays inside the frame.

    Each line is rendered in its own font, size, stroke and shadow and measured where it lands
    on screen. An overflowing line, a line break, or a character the font cannot draw is refused
    with the measured bounds and max_hangul (how many Hangul syllables fit on that line).

    hide + reveal_at is the channel's reveal: the title opens with those words as '?' and cuts to
    the full copy at reveal_at (the title clip is cut there). Without hide every title clip gets
    the full copy. dry_run measures without editing."""
    seq = seq_of(ctx.doc, p.sequence_id)
    titles = _title_clips(seq, p.clip_id)
    # the revealed (last) title holds the full copy when a line is kept
    full = dict(zip(("sub", "main"), (l["text"] for l in _copy_layers(titles[-1][1]))))
    full.update({k: v for k, v in (("sub", p.sub), ("main", p.main)) if v is not None})
    masked = {k: mask(v, p.hide) for k, v in full.items()}

    problems = []
    for w in p.hide:
        if not w.strip() or not any(w in v for v in full.values()):
            problems.append(f"hide: '{w}' is not in the copy")
    reveal = None
    if p.hide:
        if p.reveal_at is None:
            problems.append("hide needs reveal_at")
        else:
            reveal = resolve_time(p.reveal_at, seq["settings"])
            if not titles[0][1]["start"] < reveal < titles[-1][1]["end"]:
                problems.append("reveal_at must fall inside the title")

    clip = titles[0][1]
    report = {}
    for role, layer in zip(("sub", "main"), _copy_layers(clip)):
        line = _measure(seq, clip, layer, full[role], p.margin)
        if p.hide:
            line["masked"] = _measure(seq, clip, layer, masked[role], p.margin)
        report[role] = line
        given = p.sub if role == "sub" else p.main
        if given is not None and (not given.strip() or "\n" in given or "\r" in given):
            problems.append(f"{role}: one non-empty line")
            continue
        for shown in [line] + ([line["masked"]] if p.hide else []):
            if shown["missing_glyphs"]:
                problems.append(f"{role}: the font cannot draw {''.join(shown['missing_glyphs'])}")
            elif not shown["fits"]:
                problems.append(f"{role}: '{shown['text']}' is {shown['overflow_px']}px past the frame (max {shown['max_hangul']} Hangul)")
    result = {"clip_id": clip["id"], "frame_width": seq["settings"]["width"], "margin": p.margin, **report}
    if problems:
        raise CommandError("COPY_DOES_NOT_FIT", "; ".join(dict.fromkeys(problems)), **result)
    if p.dry_run:
        return result

    if reveal is not None:
        for tid, c in titles:
            if c["start"] < reveal < c["end"]:
                rid = split_clip(ctx, seq, tid, c, reveal)
                titles = sorted([find_clip(seq, x["id"]) for _, x in titles] + [find_clip(seq, rid)], key=lambda tc: tc[1]["start"])
                break
    shown = []
    for tid, c in titles:
        texts = masked if reveal is not None and c["end"] <= reveal else full
        name = c["name"]
        for role, layer in zip(("sub", "main"), _copy_layers(c)):
            if layer["text"] != texts[role]:
                graphic_set_layer(ctx, SetLayer(sequence_id=seq["id"], clip_id=c["id"], layer_id=layer["id"], text=texts[role]))
        # set_layer names a clip after its first layer's text; the title clip keeps its name
        c = seq["tracks"][tid]["clips"][c["id"]]
        if c["name"] != name:
            ctx.m.set(clip_path(seq, tid, c["id"]) + ["name"], name)
        shown.append({"clip_id": c["id"], "start": c["start"], "end": c["end"], "sub": texts["sub"], "main": texts["main"]})
    return {**result, "titles": shown}
