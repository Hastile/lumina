"""Audio clip gain (Premiere's Audio Gain dialog, G)."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import Field

from .project import seq_of
from .registry import CommandError, Context, Params, command
from .timeline import clip_path, find_clip, require_unlocked


def media_path(media: dict) -> str | None:
    from ..render.compositor import media_path as path  # lazy: render imports the command registry

    return path(media)


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


# ---------------------------------------------------------------- Essential Sound

Tag = Literal["dialogue", "music", "sfx", "ambience"]


def _audio_clips(ctx: Context, sequence_id: str | None, clip_ids: list[str] | None) -> tuple[dict, list[tuple[str, dict]]]:
    seq = seq_of(ctx.doc, sequence_id)
    if clip_ids:
        out = [find_clip(seq, cid) for cid in clip_ids]
        out = [(tid, c) for tid, c in out if seq["tracks"][tid]["kind"] == "audio"]
    else:
        out = [(tid, c) for tid in seq["audio_tracks"] for c in seq["tracks"][tid]["clips"].values()]
    if not out:
        raise CommandError("NO_AUDIO", "no audio clips")
    return seq, out


def _source(ctx: Context, clip: dict) -> tuple[str, int, int, int, dict] | None:
    media = ctx.doc["media"].get(clip.get("media_id") or "")
    if not media or not media.get("audio"):
        return None
    path = media_path(media)
    if path is None:
        return None
    dur = round((clip["end"] - clip["start"]) * clip["speed"])
    return path, clip["source_in"], dur, int((media["audio"] or {}).get("channels") or 2), media


class SetAudioTag(Params):
    sequence_id: str | None = None
    clip_ids: list[str] = Field(min_length=1)
    tag: Tag | None = Field(description="Essential Sound type; null clears it")


@command("clip.set_audio_tag", SetAudioTag)
def clip_set_audio_tag(ctx: Context, p: SetAudioTag):
    """Essential Sound: mark audio clips as Dialogue, Music, SFX or Ambience (or clear the type)."""
    seq, found = _audio_clips(ctx, p.sequence_id, p.clip_ids)
    for tid, c in found:
        require_unlocked(seq, tid)
        if c.get("audio_tag") != p.tag:
            ctx.m.set(clip_path(seq, tid, c["id"]) + ["audio_tag"], p.tag)
    return {"count": len(found)}


class AutoTag(Params):
    sequence_id: str | None = None
    clip_ids: list[str] | None = Field(default=None, description="default: every audio clip of the sequence")
    overwrite: bool = Field(default=False, description="re-tag clips that already have a type")


@command("audio.auto_tag", AutoTag)
def audio_auto_tag(ctx: Context, p: AutoTag):
    """Essential Sound Auto-Tag: classify audio clips as dialogue / music / sfx / ambience from the file
    name and the sound (syllable-rate modulation and pauses, noise flatness, length)."""
    from .. import audio_analysis

    seq, found = _audio_clips(ctx, p.sequence_id, p.clip_ids)
    feats = getattr(ctx.services, "audio_features", None)
    out: dict[str, dict] = {}
    by_media: dict[tuple, tuple] = {}
    for tid, c in found:
        if c.get("audio_tag") and not p.overwrite:
            out[c["id"]] = {"tag": c["audio_tag"], "reason": "already tagged"}
            continue
        src = _source(ctx, c)
        media = ctx.doc["media"].get(c.get("media_id") or "")
        tag = audio_analysis.tag_from_name(media["name"]) if media else None
        reason = "file name"
        if tag is None and src and feats:
            key = src[:3]
            if key not in by_media:
                by_media[key] = audio_analysis.classify(feats(src[0], src[1], src[2], src[3]))
            tag, reason = by_media[key]
        if tag is None:
            out[c["id"]] = {"tag": None, "reason": reason if src else "no audio / offline"}
            continue
        require_unlocked(seq, tid)
        if c.get("audio_tag") != tag:
            ctx.m.set(clip_path(seq, tid, c["id"]) + ["audio_tag"], tag)
        out[c["id"]] = {"tag": tag, "reason": reason}
    return {"clips": out}


class AutoMatch(Params):
    sequence_id: str | None = None
    clip_ids: list[str] | None = Field(default=None, description="default: every tagged audio clip of the sequence")
    targets: dict[str, float] = Field(default_factory=dict, description="LUFS per type, e.g. {'dialogue': -16}; defaults: "
                                      "dialogue -23, music -25, sfx -21, ambience -30")
    music_bed_db: float | None = Field(default=12.0, ge=0, le=40, description="music playing under dialogue is matched this many "
                                       "LU below the dialogue target (null: match music to its own target everywhere)")
    peak_ceiling: float = Field(default=-1.0, ge=-20, le=0, description="true-peak ceiling (dBTP) no clip may exceed after its gain")
    max_boost: float = Field(default=20.0, ge=0, le=60, description="largest gain Auto-Match will apply")


NONLINEAR = ("compressor", "hard_limiter")


def _fx(clip: dict) -> list[dict]:
    from ..effects import media_of

    return [e for e in clip.get("effects") or [] if e.get("enabled", True) and media_of(e["type"]) == "audio"]


def _overlap(a: dict, spans: list[tuple[int, int]]) -> int:
    return sum(max(0, min(a["end"], e) - max(a["start"], s)) for s, e in spans)


@command("audio.auto_match", AutoMatch)
def audio_auto_match(ctx: Context, p: AutoMatch):
    """Essential Sound Loudness Auto-Match: set each tagged clip's Audio Gain so it lands on its type's target.

    Measured as the clip plays (speed and its audio effects included), on integrated loudness (a clip under
    a second of sound on its loudest moment). Cuts of one source share one gain
    so a performance keeps its dynamics; music under dialogue is matched as a bed below the dialogue target;
    gains respect a true-peak ceiling and a boost limit; clips with a compressor or limiter are re-measured
    at the new gain."""
    import json

    from .. import audio_analysis as aa
    from ..audio_fx import ffmpeg_filters

    seq, found = _audio_clips(ctx, p.sequence_id, p.clip_ids)
    measure = getattr(ctx.services, "audio_measure", None)
    if measure is None:
        raise CommandError("NO_SERVICES", "loudness analysis needs media services")
    targets = {**aa.DEFAULT_TARGETS, **p.targets}
    opts = {"music_bed_db": p.music_bed_db, "peak_ceiling": p.peak_ceiling, "max_boost": p.max_boost}
    # dialogue anywhere in the mix (audible tracks, enabled clips): music under it is a bed
    talk = [(c["start"], c["end"]) for tid in seq["audio_tracks"] if not seq["tracks"][tid]["muted"]
            for c in seq["tracks"][tid]["clips"].values() if c.get("audio_tag") == "dialogue" and c.get("enabled", True)]
    cache: dict[tuple, dict] = {}

    def run(src, c, gain: float) -> dict:
        fx = _fx(c)
        filters = ([f"volume={gain}dB"] if gain else []) + ffmpeg_filters(fx, 48000)
        key = (src[0], src[1], src[2], c["speed"], tuple(filters))
        if key not in cache:
            cache[key] = measure(src[0], src[1], src[2], c["speed"], src[3], filters)
        return cache[key]

    items, where, out = [], {}, {}
    for tid, c in found:
        tag = c.get("audio_tag")
        src = _source(ctx, c)
        if not tag:
            continue
        if not src:
            out[c["id"]] = {"tag": tag, "lufs": None, "gain_db": c.get("gain_db", 0.0), "notes": ["offline"]}
            continue
        bed = tag == "music" and _overlap(c, talk) >= 0.5 * (c["end"] - c["start"])
        sig = json.dumps([(e["type"], e["params"]) for e in _fx(c)], sort_keys=True)
        items.append({"id": c["id"], "tag": tag, "gain_db": c.get("gain_db", 0.0), "bed": bed,
                      "group": (c.get("media_id"), tag, bed, c["speed"], sig), "m": run(src, c, 0.0)})
        where[c["id"]] = (tid, c, src)
    if not items and not out:
        raise CommandError("NOT_TAGGED", "tag the clips first (Essential Sound: Dialogue / Music / SFX / Ambience)")
    plan = aa.plan_match(items, targets, opts)
    # a compressor or limiter does not pass gain through 1:1: measure at the planned gain and correct
    for it in items:
        r = plan[it["id"]]
        tid, c, src = where[it["id"]]
        if r["lufs"] is None or "kept" in r["notes"] or not any(e["type"] in NONLINEAR for e in _fx(c)):
            continue
        want, g = r["result"], r["gain_db"]
        for _ in range(3):
            got, _metric = aa.level_of(r["tag"], run(src, c, g))
            if got is None or abs(want - got) < 0.3:
                break
            g = round(max(-96.0, min(p.max_boost, g + want - got)), 1)
            if g == p.max_boost and "boost_capped" not in r["notes"]:
                r["notes"].append("boost_capped")
        tp = run(src, c, g).get("true_peak")
        if tp is not None and tp > p.peak_ceiling + 0.1 and "peak_limited" not in r["notes"]:
            r["notes"].append("peak_limited")
        r.update(gain_db=g, result=round(got if got is not None else want, 1))
        r["notes"].append("compressed")
    for i, r in plan.items():
        tid, c, _ = where[i]
        require_unlocked(seq, tid)
        if c.get("gain_db", 0.0) != r["gain_db"]:
            ctx.m.set(clip_path(seq, tid, c["id"]) + ["gain_db"], r["gain_db"])
    return {"clips": {**out, **plan}}
