"""Audio clip gain (Premiere's Audio Gain dialog, G)."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import Field

from ..render.export import media_path
from .project import seq_of
from .registry import CommandError, Context, Params, command
from .timeline import clip_path, find_clip, require_unlocked


class AudioGain(Params):
    sequence_id: str | None = None
    clip_ids: list[str] = Field(min_length=1)
    mode: Literal["set", "adjust", "normalize_max", "normalize_all"] = Field(
        description="set: Set Gain to; adjust: Adjust Gain by; normalize_max: Normalize Max Peak to (one gain change "
                    "for the selection, its loudest peak lands on db); normalize_all: Normalize All Peaks to (each clip)")
    db: float = Field(ge=-96, le=96)


def _peak(ctx: Context, clip: dict) -> float:
    media = ctx.doc["media"].get(clip.get("media_id") or "")
    if not media or not media.get("audio"):
        raise CommandError("NO_AUDIO", f"clip has no audio: {clip['name']}")
    path = media_path(media)
    if path is None:
        raise CommandError("MEDIA_OFFLINE", f"media offline: {media['name']}")
    peak = getattr(ctx.services, "audio_peak", None)
    if peak is None:
        raise CommandError("NO_SERVICES", "peak analysis needs media services")
    return peak(path, clip["source_in"], round((clip["end"] - clip["start"]) * clip["speed"]), clip["speed"])


@command("clip.audio_gain", AudioGain)
def clip_audio_gain(ctx: Context, p: AudioGain):
    """Audio Gain: set, adjust, or normalize the clip gain of audio clips (peaks measured on the media)."""
    seq = seq_of(ctx.doc, p.sequence_id)
    found = []
    for cid in p.clip_ids:
        tid, c = find_clip(seq, cid)
        require_unlocked(seq, tid)
        found.append((tid, c))
    gains: dict[str, float] = {}
    peaks: dict[str, float] = {}
    if p.mode in ("normalize_max", "normalize_all"):
        for _, c in found:
            peaks[c["id"]] = _peak(ctx, c)
        loud = [v for v in peaks.values() if math.isfinite(v)]
        if not loud:
            raise CommandError("SILENT", "the selected clips are silent")
        top = max(loud)
    for _, c in found:
        g = c.get("gain_db", 0.0)
        if p.mode == "set":
            g = p.db
        elif p.mode == "adjust":
            g += p.db
        elif p.mode == "normalize_max":
            g = p.db - top  # gain_db is replaced: the peak is measured on the media
        elif math.isfinite(peaks[c["id"]]):
            g = p.db - peaks[c["id"]]
        gains[c["id"]] = round(max(-96.0, min(96.0, g)), 2)
    for tid, c in found:
        if c.get("gain_db", 0.0) != gains[c["id"]]:
            ctx.m.set(clip_path(seq, tid, c["id"]) + ["gain_db"], gains[c["id"]])
    return {"gain_db": gains, "peaks_db": {k: (round(v, 2) if math.isfinite(v) else None) for k, v in peaks.items()}}
