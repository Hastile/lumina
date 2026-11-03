"""Caption effects and sound effects for a Short, each in one command.

After clip.place_shots, the rest of a Short's edit was still one call per
caption: move it to the slam or fade track, set its size, check the frame; and
three or four per sound: place it, tag it, match it, fix its volume. Both
follow fixed rules (docs/shorts-editing/shorts-caption-effect-grammar.md and
shorts-sfx-grammar.md), so here they are code. Like place_shots, each command
checks everything first: a problem refuses the whole list and changes nothing,
and one undo removes it.

shorts.caption_effects  pop / slam / fade by moving cues between the template's
                        caption tracks (커졌다 줄어들기 / 꽝 박히기 / 밑에서 스무쓰하게
                        올라오기); a slam is set to 120 when it fits one line
                        (810 px), else it stays 100.
shorts.place_sfx        sounds by name (W1-W7, Kick 1/2/5, Epic Hit 3, Bells 2,
                        뚜훅, 티딩, 난감한 물방울, H:Cash/Guns/Impactor ...) on their
                        tracks: whooshes A2, hits A3, bells/물방울 A4; Epic Hit
                        pre-rolled 21 frames; tails trimmed so they never run
                        into the next sound on the track; tagged SFX; Auto-Match
                        on A2/A3; 티딩/Kick 5/Epic Hit 2 at volume -3, 티딩 at
                        gain -7.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal, Optional

from pydantic import Field

from .. import timebase
from ..tracks import caption_layer, is_caption_track
from .project import seq_of
from .registry import REGISTRY, CommandError, Context, Params, TimeValue, command, resolve_time
from .timeline import find_clip, is_free, write_clip

# ---------------------------------------------------------------- caption effects

EFFECT_TRACK_WORDS = {"pop": ("커졌다", "pop"), "slam": ("꽝", "slam"), "fade": ("밑에서", "스무쓰", "fade")}
SLAM_SIZE = 120.0
LINE_WIDTH = 810.0


def effect_track(seq: dict, effect: str) -> Optional[str]:
    for tid in seq["video_tracks"]:
        t = seq["tracks"][tid]
        name = (t.get("name") or "").lower()
        if is_caption_track(t) and any(w in name for w in EFFECT_TRACK_WORDS[effect]):
            return tid
    return None


def line_width(style: dict, caption: dict, font_size: float) -> Optional[float]:
    """Ink width of a cue drawn on one line (no box wrap) at a font size."""
    from ..render import text as text_

    layer = caption_layer(style, {**caption, "style": {**(caption.get("style") or {}), "font_size": font_size}})
    layer["box"] = None
    img, _ax, _ay = text_.render_text_layer(layer)
    box = img.getchannel("A").getbbox()
    if box is None:
        return None
    return (box[2] - box[0]) * float(layer.get("scale", 100.0)) / 100


class CaptionEffect(Params):
    clip_id: str
    effect: Literal["pop", "slam", "fade"]
    size: Optional[float] = Field(default=None, description="font size override; default: 120 for a slam that fits one "
                                                           "line, otherwise the track's size")


class CaptionEffects(Params):
    sequence_id: Optional[str] = None
    items: list[CaptionEffect] = Field(min_length=1)
    max_width: float = Field(default=LINE_WIDTH, description="a slam is enlarged only if it stays within this width")


@command("shorts.caption_effects", CaptionEffects)
def shorts_caption_effects(ctx: Context, p: CaptionEffects):
    """Give caption cues their effect (pop / slam / fade) by moving them to that effect's caption track, and size
    slams 120 where they fit one line, all as one edit. Any problem refuses the whole list."""
    seq = seq_of(ctx.doc, p.sequence_id)
    tracks = {e: effect_track(seq, e) for e in EFFECT_TRACK_WORDS}
    problems, moves = [], []
    for it in p.items:
        try:
            src, clip = find_clip(seq, it.clip_id)
        except CommandError:
            problems.append(f"{it.clip_id}: no such clip")
            continue
        if clip.get("caption") is None:
            problems.append(f"{it.clip_id}: not a caption cue")
            continue
        dest = tracks[it.effect]
        if dest is None:
            problems.append(f"no caption track for {it.effect} (expected a name with {'/'.join(EFFECT_TRACK_WORDS[it.effect])})")
            continue
        moves.append((it, src, dest, clip))
    if problems:
        raise CommandError("EFFECTS_REFUSED", "; ".join(dict.fromkeys(problems)), problems=list(dict.fromkeys(problems)))
    moving = {it.clip_id for it, src, dest, _ in moves if src != dest}
    for it, src, dest, clip in moves:
        if src != dest and not is_free(seq["tracks"][dest], clip["start"], clip["end"], exclude=moving):
            problems.append(f"{it.clip_id} ('{clip['caption']['text']}'): another cue is on the {it.effect} track there")
    if problems:
        raise CommandError("EFFECTS_REFUSED", "; ".join(problems), problems=problems)

    out = []
    for it, src, dest, clip in moves:
        style = seq["tracks"][dest]["caption_style"]
        cap = dict(clip["caption"])
        overrides = dict(cap.get("style") or {})
        size, note = it.size, None
        if size is None and it.effect == "slam":
            w = line_width(style, cap, SLAM_SIZE)
            if w is not None and w <= p.max_width:
                size = SLAM_SIZE
            else:
                note = f"stays {style.get('font_size')}: {w:.0f}px at {SLAM_SIZE:.0f} is wider than {p.max_width:.0f}px" if w else None
        if size is None:
            overrides.pop("font_size", None)
        else:
            overrides["font_size"] = float(size)
        cap["style"] = overrides or None
        new = {**clip, "caption": cap}
        if src != dest:
            ctx.m.delete(["sequences", seq["id"], "tracks", src, "clips", clip["id"]])
        write_clip(ctx, seq, dest, new)
        row = {"clip_id": clip["id"], "text": cap["text"], "effect": it.effect, "track_id": dest,
               "font_size": overrides.get("font_size", style.get("font_size"))}
        if note:
            row["note"] = note
        out.append(row)
    return {"captions": out}


# ---------------------------------------------------------------- sound effects

FPS30_PREROLL_FRAMES = 21  # Epic Hit lands 0.7 s after its start


def sound_rule(name: str) -> dict:
    """Track (A#), pre-roll frames, volume and fixed gain for a sound name from the SFX grammar."""
    n = name.strip().lower()
    rule = {"track": 3, "preroll": 0, "volume_db": None, "gain_db": None, "match": True}
    if re.fullmatch(r"w\d", n) or "whoosh" in n:
        rule["track"] = 2
    elif "bells" in n or "물방울" in n:
        rule.update(track=4, match=False)
    if "epic hit" in n:
        rule["preroll"] = FPS30_PREROLL_FRAMES
    if n in ("티딩", "티딩!") or "티딩" in n:
        rule.update(volume_db=-3.0, gain_db=-7.0, match=False)
    if re.fullmatch(r"(kick|k) ?5", n) or re.fullmatch(r"epic hit ?2", n):
        rule["volume_db"] = -3.0
    return rule


def _norm(s: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", " ", s.lower()).strip()


def _words(name: str) -> tuple[list[str], list[str]]:
    """Search words and numbers for a sound name; W3 means 'Whoosh Dramatic 3', H:Cash means 'Cash'."""
    n = name.strip()
    m = re.fullmatch(r"[Ww](\d)", n)
    if m:
        n = f"Whoosh Dramatic {m.group(1)}"
    n = re.sub(r"^[Hh]\s*:\s*", "", n)
    toks = _norm(n).split()
    return [t for t in toks if not t.isdigit()], [t for t in toks if t.isdigit()]


def _matches(stem: str, words: list[str], nums: list[str]) -> bool:
    s = _norm(stem)
    toks = s.split()
    return all(w in s for w in words) and all(d in toks for d in nums)


_SCAN: dict[str, list[str]] = {}
AUDIO_EXTS = {".wav", ".mp3", ".aif", ".aiff", ".m4a", ".ogg", ".flac"}


def sfx_dirs(given: Optional[list[str]]) -> list[str]:
    if given:
        return given
    env = os.environ.get("LUMINA_SFX_DIRS")
    if env:
        return [d for d in env.split(os.pathsep) if d]
    return [d for d in ("D:/유튜브 채널/SFX", "D:/Motion Bro") if Path(d).is_dir()]


def _scan(root: str) -> list[str]:
    if root not in _SCAN:
        _SCAN[root] = [str(p) for p in Path(root).rglob("*") if p.suffix.lower() in AUDIO_EXTS] if Path(root).is_dir() else []
    return _SCAN[root]


def resolve_sound(doc: dict, sound: str, dirs: list[str]) -> tuple[Optional[str], Optional[str], list[str]]:
    """(media_id, path, alternatives) for a media id, a file path or a sound name."""
    if sound in doc["media"]:
        return sound, None, []
    if Path(sound).is_file():
        return None, sound, []
    words, nums = _words(sound)
    if not words and not nums:
        return None, None, []
    media = sorted((m for m in doc["media"].values() if m["kind"] == "audio" and _matches(Path(m["name"]).stem, words, nums)),
                   key=lambda m: len(m["name"]))
    if media:
        return media[0]["id"], None, [m["name"] for m in media[1:4]]
    files = sorted((f for d in dirs for f in _scan(d) if _matches(Path(f).stem, words, nums)), key=lambda f: (len(Path(f).stem), f))
    if files:
        return None, files[0], [Path(f).name for f in files[1:4]]
    return None, None, []


class Sound(Params):
    at: TimeValue = Field(description="where the sound should land: a caption's first frame (Epic Hit is pre-rolled itself)")
    sound: str = Field(description="sound name (W1-W7, Kick 2, Epic Hit 3, Bells 2, 뚜훅, 티딩, 난감한 물방울, H:Cash ...), "
                                   "a project media id or a file path")
    track_id: Optional[str] = Field(default=None, description="default by the sound: whoosh A2, hits A3, bells/물방울 A4")


class PlaceSfx(Params):
    sequence_id: Optional[str] = None
    sounds: list[Sound] = Field(min_length=1)
    end_at: Optional[TimeValue] = Field(default=None, description="the video's end: sound tails are cut there")
    sfx_dirs: Optional[list[str]] = Field(default=None, description="folders searched for sound names not in the project "
                                                                    "(default LUMINA_SFX_DIRS, else D:/유튜브 채널/SFX and D:/Motion Bro)")
    auto_match: bool = Field(default=True, description="Loudness Auto-Match the new A2/A3 sounds (SFX -21 LUFS)")
    allow_cover: bool = Field(default=False, description="let a sound overwrite clips already on its track")


@command("shorts.place_sfx", PlaceSfx)
def shorts_place_sfx(ctx: Context, p: PlaceSfx):
    """Place a Short's sound effects by name in one edit: each on its grammar track (whoosh A2, hits A3, bells A4),
    Epic Hit pre-rolled 21 f, tails cut before the next sound on the track and at end_at, tagged SFX, A2/A3
    Auto-Matched, 티딩/Kick 5/Epic Hit 2 at volume -3 and 티딩 at gain -7. Any problem refuses the whole list."""
    doc = ctx.doc
    seq = seq_of(doc, p.sequence_id)
    st = seq["settings"]
    tpf = timebase.ticks_per_frame(st["fps_num"], st["fps_den"])
    dirs = sfx_dirs(p.sfx_dirs)
    end_at = resolve_time(p.end_at, st) if p.end_at is not None else None
    problems, warnings, todo = [], [], []
    for n, s in enumerate(p.sounds):
        rule = sound_rule(s.sound)
        tid = s.track_id
        if tid is None:
            k = rule["track"] - 1
            if k >= len(seq["audio_tracks"]):
                problems.append(f"{s.sound}: the sequence has no A{rule['track']}")
                continue
            tid = seq["audio_tracks"][k]
        if tid not in seq["tracks"] or seq["tracks"][tid]["kind"] != "audio":
            problems.append(f"{s.sound}: {tid} is not an audio track")
            continue
        mid, path, alts = resolve_sound(doc, s.sound, dirs)
        if mid is None and path is None:
            problems.append(f"{s.sound}: no such sound in the project or in {dirs or 'any SFX folder'}")
            continue
        land = resolve_time(s.at, st)
        at = max(0, land - rule["preroll"] * tpf)
        todo.append({"n": n, "sound": s.sound, "rule": rule, "track": tid, "media_id": mid, "path": path, "alts": alts,
                     "at": at, "land": land})
    if problems:
        raise CommandError("SFX_REFUSED", "; ".join(problems), problems=problems)

    # import what is missing, then lay each track's sounds in time order
    from .shots import _media_for_path

    for x in todo:
        if x["media_id"] is None:
            x["media_id"] = _media_for_path(ctx, x["path"])
        x["dur"] = doc["media"][x["media_id"]]["duration"]
    todo.sort(key=lambda x: (x["track"], x["at"]))
    for a, b in zip(todo, todo[1:]):
        if a["track"] == b["track"] and b["at"] <= a["at"]:
            problems.append(f"{a['sound']} and {b['sound']} start at the same time on one track")
    for x, nxt in zip(todo, todo[1:] + [None]):
        end = x["at"] + x["dur"]
        if nxt is not None and nxt["track"] == x["track"]:
            end = min(end, nxt["at"])
        if end_at is not None:
            end = min(end, end_at)
        end = (end // tpf) * tpf
        if end - x["at"] < tpf:
            problems.append(f"{x['sound']} at frame {x['at'] // tpf}: no room (next sound or the end is right there)")
            continue
        x["end"] = end
        if not p.allow_cover and not is_free(seq["tracks"][x["track"]], x["at"], end):
            problems.append(f"{x['sound']} at frame {x['at'] // tpf}: another clip is on that track there")
    if problems:
        raise CommandError("SFX_REFUSED", "; ".join(problems), problems=problems)
    lands = sorted(x["land"] for x in todo)
    for a, b in zip(lands, lands[1:]):
        if b - a < timebase.seconds_to_ticks(0.5):
            warnings.append(f"sounds at frames {a // tpf} and {b // tpf} are under 0.5 s apart")

    place = REGISTRY.get("clip.place")
    clip_set = REGISTRY.get("clip.set")
    tag = REGISTRY.get("clip.set_audio_tag")
    gain = REGISTRY.get("clip.audio_gain")
    placed, match_ids = [], []
    for x in todo:
        r = place.handler(ctx, place.params.model_validate({
            "sequence_id": seq["id"], "media_id": x["media_id"], "at": x["at"], "source_in": 0,
            "source_out": x["end"] - x["at"], "take_video": False, "audio_track_id": x["track"]}))
        cid = r["audio_clip_id"]
        tag.handler(ctx, tag.params.model_validate({"sequence_id": seq["id"], "clip_ids": [cid], "tag": "sfx"}))
        if x["rule"]["volume_db"] is not None:
            clip_set.handler(ctx, clip_set.params.model_validate({"sequence_id": seq["id"], "clip_ids": [cid],
                                                                  "volume_db": x["rule"]["volume_db"]}))
        if x["rule"]["gain_db"] is not None:
            gain.handler(ctx, gain.params.model_validate({"sequence_id": seq["id"], "clip_ids": [cid], "mode": "set",
                                                          "db": x["rule"]["gain_db"]}))
        elif x["rule"]["match"] and x["track"] in seq["audio_tracks"][1:3]:
            match_ids.append(cid)
        row = {"clip_id": cid, "sound": x["sound"], "file": doc["media"][x["media_id"]]["name"],
               "track": f"A{seq['audio_tracks'].index(x['track']) + 1}", "at_frame": x["at"] // tpf,
               "frames": (x["end"] - x["at"]) // tpf}
        if x["at"] != x["land"]:
            row["pre_roll_frames"] = (x["land"] - x["at"]) // tpf
        if x["end"] - x["at"] < x["dur"] - tpf:
            row["tail_cut"] = True
        if x["alts"]:
            row["also_matched"] = x["alts"]
        placed.append(row)
    matched = None
    if p.auto_match and match_ids:
        am = REGISTRY.get("audio.auto_match")
        matched = am.handler(ctx, am.params.model_validate({"sequence_id": seq["id"], "clip_ids": match_ids}))
    for row in placed:  # the Auto-Match result per clip, when there is one
        g = seq_of(doc, seq["id"])
        tid, c = find_clip(g, row["clip_id"])
        row["gain_db"] = round(c.get("gain_db", 0.0), 1)
    out = {"sounds": sorted(placed, key=lambda r: r["at_frame"]), "warnings": warnings}
    if matched is not None:
        out["auto_matched"] = len(match_ids)
    return out
