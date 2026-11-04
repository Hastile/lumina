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
                        on A2/A3 (티딩 included). Clip volume is never lowered:
                        the level comes from Auto-Match (user, 2026-11-03).
"""

from __future__ import annotations

import os
import re
from collections import Counter
from pathlib import Path
from typing import Literal, Optional

from pydantic import Field

from .. import timebase
from ..tracks import caption_layer, is_caption_track
from .project import seq_of
from .registry import REGISTRY, CommandError, Context, Params, TimeValue, command, resolve_time
from .shots import Shot as PlannedShot
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
    """Track (A#), pre-roll frames and whether Auto-Match sets its level, for a sound name from the SFX grammar.
    Levels come from Auto-Match only: no clip volume cuts, no fixed gains (user, 2026-11-03)."""
    n = name.strip().lower()
    rule = {"track": 3, "preroll": 0, "match": True}
    if re.fullmatch(r"w\d", n) or "whoosh" in n:
        rule["track"] = 2
    elif "bells" in n or "물방울" in n:
        rule.update(track=4, match=False)
    if "epic hit" in n:
        rule["preroll"] = FPS30_PREROLL_FRAMES
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
    Auto-Matched (clip volume is never lowered). Any problem refuses the whole list."""
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
    tag = REGISTRY.get("clip.set_audio_tag")
    placed, match_ids = [], []
    for x in todo:
        r = place.handler(ctx, place.params.model_validate({
            "sequence_id": seq["id"], "media_id": x["media_id"], "at": x["at"], "source_in": 0,
            "source_out": x["end"] - x["at"], "take_video": False, "audio_track_id": x["track"]}))
        cid = r["audio_clip_id"]
        tag.handler(ctx, tag.params.model_validate({"sequence_id": seq["id"], "clip_ids": [cid], "tag": "sfx"}))
        if x["rule"]["match"] and x["track"] in seq["audio_tracks"][1:3]:
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


# ---------------------------------------------------------------- the whole Short in one command

class BuildShort(Params):
    sequence_id: Optional[str] = None
    shots: Optional[list[PlannedShot]] = Field(default=None, description="clip.place_shots shots; omit to keep V1 as it is")
    edge_margin: float = 0.3
    min_speed: float = 0.87
    allow_cover: bool = Field(default=False, description="let the shots overwrite clips already on V1")
    effects: Optional[list[CaptionEffect]] = Field(default=None, description="omit: the shorts_plan draft's effects")
    sounds: Optional[list[Sound]] = Field(default=None, description="omit: the shorts_plan draft's sounds (hits marked "
                                                                    "'hit?' are left for you and listed)")
    reveal: list[int] = Field(default_factory=list, description="for the draft: caption numbers that start a new shot")
    sfx_dirs: Optional[list[str]] = None
    auto_match: bool = True
    end_pad_frames: int = Field(default=6, ge=0, le=30, description="the video ends this long after the narration (A1)")
    align_end: bool = Field(default=True, description="end V1, V5, V6, the last caption and every sound on the video's end")
    mark_shots: bool = Field(default=True, description="store the shot plan as S-markers (the given shots, or the draft "
                                                       "when none are given and none are marked yet)")


def _align_end(ctx: Context, seq: dict, end: int, notes: list[str]) -> None:
    from .timeline import source_problem

    vt, at, tracks = seq["video_tracks"], seq["audio_tracks"], seq["tracks"]
    must_reach = [vt[0]] + [vt[k] for k in (4, 5) if len(vt) > k and not is_caption_track(tracks[vt[k]])]
    caption_tids = [t for t in vt if is_caption_track(tracks[t])]
    last_cue = max((c for t in caption_tids for c in tracks[t]["clips"].values()), key=lambda c: c["end"], default=None)
    for tid in vt + at:
        for c in list(tracks[tid]["clips"].values()):
            if c["start"] >= end:
                ctx.m.delete(["sequences", seq["id"], "tracks", tid, "clips", c["id"]])
                notes.append(f"removed '{c['name']}' after the end")
            elif c["end"] > end:
                write_clip(ctx, seq, tid, dict(c, end=end))
    for tid in must_reach:
        clips = tracks[tid]["clips"]
        if not clips:
            continue
        last = max(clips.values(), key=lambda c: c["end"])
        if last["end"] < end and is_free(tracks[tid], last["end"], end):
            longer = dict(last, end=end)
            if source_problem(ctx.doc, longer):
                notes.append(f"'{last['name']}' cannot reach the end: its source runs out")
            else:
                write_clip(ctx, seq, tid, longer)
    if last_cue is not None and last_cue["end"] < end:
        tid = next(t for t in caption_tids if last_cue["id"] in tracks[t]["clips"])
        write_clip(ctx, seq, tid, dict(tracks[tid]["clips"][last_cue["id"]], end=end))


@command("shorts.build", BuildShort)
def shorts_build(ctx: Context, p: BuildShort):
    """A Short's edit in one command and one undo step: place the shots on V1 (clip.place_shots), end every track
    end_pad_frames after the narration, give the captions their effects and place the sounds (yours, or the
    shorts_plan draft's), then check the result (shorts_check) and return its problems. Anything refused rolls
    the whole command back."""
    from .. import shorts_check, shorts_plan as sp
    from .shots import PlaceShots, clip_place_shots

    seq = seq_of(ctx.doc, p.sequence_id)
    st = seq["settings"]
    tpf = timebase.ticks_per_frame(st["fps_num"], st["fps_den"])
    out: dict = {"notes": []}
    if p.shots:
        r = clip_place_shots(ctx, PlaceShots(sequence_id=seq["id"], shots=p.shots, edge_margin=p.edge_margin,
                                             min_speed=p.min_speed, allow_cover=p.allow_cover))
        out["placed"] = len(r["placed"])
        out["dark_or_text"] = [f"{x['shot']}: {', '.join(x['notes'])}" for x in r["placed"] if x["notes"]]

    end = None
    a1 = seq["tracks"][seq["audio_tracks"][0]]["clips"] if seq["audio_tracks"] else {}
    if p.align_end and a1:
        end = max(c["end"] for c in a1.values()) + p.end_pad_frames * tpf
        _align_end(ctx, seq, end, out["notes"])
        out["end_frame"] = end // tpf

    cues = sp.cues_from_sequence(seq)
    plan = sp.make_plan(cues, reveal=tuple(p.reveal)) if cues else None
    effects = p.effects
    if effects is None and plan is not None:
        effects = [CaptionEffect(clip_id=c.clip_id, effect=plan.effects[c.i]) for c in plan.cues if c.clip_id]
    if effects:
        r = shorts_caption_effects(ctx, CaptionEffects(sequence_id=seq["id"], items=effects))
        out["effects"] = dict(sorted(Counter(x["effect"] for x in r["captions"]).items()))
        out["notes"] += [f"'{x['text']}': {x['note']}" for x in r["captions"] if x.get("note")]

    sounds = p.sounds
    if sounds is None and plan is not None:
        dirs = sfx_dirs(p.sfx_dirs)
        sounds, out["hits_to_choose"], missing = [], [], []
        for c in plan.cues:
            name = plan.sounds.get(c.i)
            if not name:
                continue
            if name == "hit?":
                out["hits_to_choose"].append({"at": f"{c.start // tpf}f", "caption": c.text})
                continue
            mid, path, _ = resolve_sound(ctx.doc, name, dirs)
            if mid is None and path is None:
                missing.append(name)
                continue
            sounds.append(Sound(at=c.start, sound=name))
        if missing:
            out["notes"].append(f"sounds not found, skipped: {', '.join(sorted(set(missing)))}")
    if sounds:
        r = shorts_place_sfx(ctx, PlaceSfx(sequence_id=seq["id"], sounds=sounds, end_at=end, sfx_dirs=p.sfx_dirs,
                                           auto_match=p.auto_match))
        out["sounds"] = [f"{x['at_frame']}f {x['sound']} ({x['track']})" for x in r["sounds"]]
        out["notes"] += r["warnings"]

    if p.mark_shots:
        from .. import shorts_fill

        if p.shots:
            marks = [MarkShot(at=s.at, end=s.end) for s in p.shots]
            shorts_mark_shots(ctx, MarkShots(sequence_id=seq["id"], shots=marks))
        elif not shorts_fill.shot_markers(seq) and plan is not None:
            shorts_mark_shots(ctx, MarkShots(sequence_id=seq["id"], reveal=list(p.reveal)))
            out["notes"].append("shot plan stored as markers S1.. (no shots given: fill them with shorts_gaps as sources arrive)")

    rep = shorts_check.check(ctx.doc, seq)
    out["check"] = shorts_check.render(rep)
    out["ok"] = rep["ok"]
    return out


# ---------------------------------------------------------------- TMI

TMI_FONT = "Pretendard-Medium"
TMI_SIZE = 48.0
TMI_Y = {"above": 680.0, "above_large": 670.0, "below": 960.0}   # box tops (docs/shorts-editing caption effect grammar)
TMI_ONE_LINE = 19
POLITE = ("니다", "요", "죠")


class Tmi(Params):
    caption_clip_id: str = Field(description="the caption cue the note hangs on (same start and end)")
    text: str = Field(min_length=1, description="the note; '\\n' for a 2-3 line memo")
    place: Literal["auto", "above", "below"] = Field(default="auto", description="auto: one short line above, longer "
                                                                                  "or multi-line memos below")


class AddTmi(Params):
    sequence_id: Optional[str] = None
    items: list[Tmi] = Field(min_length=1)
    track_id: Optional[str] = Field(default=None, description="default the video track named TMI, else V4")
    replace: bool = Field(default=False, description="overwrite TMI already on the track at those times")


def tmi_track(seq: dict) -> Optional[str]:
    for tid in seq["video_tracks"]:
        t = seq["tracks"][tid]
        if not is_caption_track(t) and (t.get("name") or "").strip().upper() == "TMI":
            return tid
    return seq["video_tracks"][3] if len(seq["video_tracks"]) > 3 and not is_caption_track(seq["tracks"][seq["video_tracks"][3]]) else None


@command("shorts.add_tmi", AddTmi)
def shorts_add_tmi(ctx: Context, p: AddTmi):
    """Put TMI notes on caption cues in one edit: a text graphic on the TMI track (V4) with each cue's start and
    end, Pretendard-Medium 48 with the caption track's stroke and shadow, above the caption (y 680; 670 over a
    120 caption) for one short line, below (y 960) for longer memos; several notes on one cue share one graphic;
    the caption track's effects are copied so the note moves with its caption. Refuses the whole list on a problem;
    warns on polite-register endings, long one-liners and glyphs the font cannot draw."""
    from .. import text_fit
    from ..tracks import active_track_effects
    from .motion import _text_layer, graphic_clip

    seq = seq_of(ctx.doc, p.sequence_id)
    tid = p.track_id or tmi_track(seq)
    if tid is None or tid not in seq["tracks"] or seq["tracks"][tid]["kind"] != "video" or is_caption_track(seq["tracks"][tid]):
        raise CommandError("NO_TMI_TRACK", "no TMI track: name a video track TMI or give track_id")
    problems, warnings = [], []
    groups: dict[str, list[tuple[Tmi, str, dict]]] = {}
    for it in p.items:
        try:
            ctid, cue = find_clip(seq, it.caption_clip_id)
        except CommandError:
            problems.append(f"{it.caption_clip_id}: no such clip")
            continue
        if cue.get("caption") is None:
            problems.append(f"{it.caption_clip_id}: not a caption cue")
            continue
        groups.setdefault(cue["id"], []).append((it, ctid, cue))
    for cid, notes in groups.items():
        cue = notes[0][2]
        if not p.replace and not is_free(seq["tracks"][tid], cue["start"], cue["end"]):
            problems.append(f"'{cue['caption']['text']}': the TMI track already has a clip there (pass replace)")
    if problems:
        raise CommandError("TMI_REFUSED", "; ".join(problems), problems=problems)

    from .timeline import clear_range

    out = []
    for cid, notes in groups.items():
        _, ctid, cue = notes[0]
        cstyle = seq["tracks"][ctid]["caption_style"]
        size = ((cue["caption"].get("style") or {}).get("font_size") or cstyle.get("font_size") or 100)
        x = (cstyle.get("position") or [135.0, 0])[0]
        box = list(cstyle.get("box") or [810.0, 576.0])
        layers, used = [], set()
        for it, _c, _q in notes:
            text = it.text.strip()
            place = it.place
            if place == "auto":
                place = "below" if "\n" in text or len(text) > TMI_ONE_LINE else "above"
            key = "above_large" if place == "above" and size >= 120 else place
            if place in used:
                warnings.append(f"'{cue['caption']['text']}': two notes {place} the caption overlap; put one below")
            used.add(place)
            layer = _text_layer(text, [x, TMI_Y[key]], font_family=TMI_FONT, font_size=TMI_SIZE, bold=False,
                                color=cstyle.get("color", "#ffffff"), stroke_color=cstyle.get("stroke_color"),
                                stroke_width=cstyle.get("stroke_width", 0.0), shadow=cstyle.get("shadow"),
                                align="center", box=box, vertical_align="center")
            layers.append(layer)
            core = text.rstrip(" .!?…~ㅋ")
            if core.endswith(POLITE):
                warnings.append(f"'{text}': TMI is never in the narration's polite register (니다/요/죠)")
            if "\n" not in text and len(text) > TMI_ONE_LINE and place == "above":
                warnings.append(f"'{text}': {len(text)} characters on one line above the caption (7-19 reads at tempo)")
            missing = text_fit.missing_glyphs(layer)
            if missing:
                warnings.append(f"'{text}': the font cannot draw {''.join(missing)} (write the Korean reading)")
        clear_range(ctx, seq, tid, cue["start"], cue["end"])
        clip = graphic_clip(f"TMI {notes[0][0].text.splitlines()[0][:30]}", cue["start"], cue["end"], layers)
        clip["effects"] = [dict(e) for e in active_track_effects(seq["tracks"][ctid])]
        write_clip(ctx, seq, tid, clip)
        out.append({"clip_id": clip["id"], "caption": cue["caption"]["text"], "notes": [n[0].text for n in notes],
                    "y": [l["position"][1] for l in layers]})
    return {"track_id": tid, "tmi": out, "warnings": warnings}


# ---------------------------------------------------------------- BGM level

BGM_BAND = (-27.0, -24.0)        # user, 2026-11-03: BGM anywhere between -27 and -24 LUFS (no fixed midpoint)


class SetBgm(Params):
    sequence_id: Optional[str] = None
    track_id: Optional[str] = Field(default=None, description="the BGM track; default A5")
    clip_ids: Optional[list[str]] = Field(default=None, description="default every clip on the BGM track")
    low_lufs: float = Field(default=BGM_BAND[0], ge=-60, le=0, description="quietest allowed (LUFS)")
    high_lufs: float = Field(default=BGM_BAND[1], ge=-60, le=0, description="loudest allowed (LUFS)")


@command("shorts.set_bgm", SetBgm)
def shorts_set_bgm(ctx: Context, p: SetBgm):
    """Keep the BGM inside -27 to -24 LUFS by Loudness Auto-Match, without forcing a midpoint: each BGM clip is
    tagged Music and measured as it plays with its current gain; inside the band it is left alone, louder than
    -24 it is matched down to -24, quieter than -27 matched up to -27. Only Audio Gain changes."""
    from .. import audio_analysis as aa
    from .audio import _source

    if p.low_lufs > p.high_lufs:
        raise CommandError("BAD_PARAMS", "low_lufs must not be above high_lufs")
    seq = seq_of(ctx.doc, p.sequence_id)
    at = seq["audio_tracks"]
    tid = p.track_id or (at[4] if len(at) > 4 else None)
    if tid is None or tid not in seq["tracks"] or seq["tracks"][tid]["kind"] != "audio":
        raise CommandError("NO_BGM_TRACK", "no BGM track (A5); give track_id")
    clips = [c for c in seq["tracks"][tid]["clips"].values() if not p.clip_ids or c["id"] in p.clip_ids]
    if not clips:
        raise CommandError("NO_BGM", "no clips on the BGM track")
    measure = getattr(ctx.services, "audio_measure", None)
    if measure is None:
        raise CommandError("NO_SERVICES", "loudness analysis needs media services")
    tag = REGISTRY.get("clip.set_audio_tag")
    tag.handler(ctx, tag.params.model_validate({"sequence_id": seq["id"], "clip_ids": [c["id"] for c in clips], "tag": "music"}))
    am = REGISTRY.get("audio.auto_match")
    out = {"track_id": tid, "band": [p.low_lufs, p.high_lufs], "clips": {}}
    for c in clips:
        src = _source(ctx, c)
        if src is None:
            raise CommandError("MEDIA_OFFLINE", f"'{c['name']}' has no audio on disk")
        path, s0, dur, channels, _m = src
        level, _metric = aa.level_of("music", measure(path, s0, dur, c["speed"], channels, []))
        if level is None:
            out["clips"][c["id"]] = {"note": "silent"}
            continue
        gain = float(find_clip(seq, c["id"])[1].get("gain_db") or 0.0)
        now = round(level + gain, 1)
        if p.low_lufs <= now <= p.high_lufs:
            out["clips"][c["id"]] = {"lufs": now, "action": "kept", "gain_db": round(gain, 1)}
            continue
        target = p.high_lufs if now > p.high_lufs else p.low_lufs
        r = am.handler(ctx, am.params.model_validate({"sequence_id": seq["id"], "clip_ids": [c["id"]],
                                                      "targets": {"music": target}, "music_bed_db": None}))
        x = r["clips"][c["id"]]
        row = {"lufs_before": now, "action": "lowered" if target == p.high_lufs else "raised", "target": target,
               "result": x.get("result"), "gain_db": x.get("gain_db"), "notes": x.get("notes")}
        if x.get("result") is not None and not p.low_lufs - 0.5 <= x["result"] <= p.high_lufs + 0.5:
            row["note"] = "could not reach the band (peak ceiling or boost limit)"
        out["clips"][c["id"]] = row
    return out


# ---------------------------------------------------------------- the shot plan as markers

class MarkShot(Params):
    at: TimeValue
    end: TimeValue
    look: str = Field(default="", description="what should be on screen (Korean picture description), for the source search")
    text: str = Field(default="", description="the narration the shot covers")


class MarkShots(Params):
    sequence_id: Optional[str] = None
    shots: Optional[list[MarkShot]] = Field(default=None, description="omit: the shorts_plan draft's shots")
    reveal: list[int] = Field(default_factory=list, description="for the draft: caption numbers that start a new shot")
    color: Literal["green", "red", "purple", "orange", "yellow", "white", "blue", "cyan"] = "cyan"


@command("shorts.mark_shots", MarkShots)
def shorts_mark_shots(ctx: Context, p: MarkShots):
    """Store the shot plan on the sequence as markers S1, S2 ... spanning each shot, with 'look | narration' as
    the comment, replacing earlier S-markers. The plan then survives the session, shows (and can be dragged) in the
    GUI, and shorts_gaps fills it as sources arrive."""
    from .. import shorts_fill, shorts_plan as sp
    from ..model import new_id

    seq = seq_of(ctx.doc, p.sequence_id)
    st = seq["settings"]
    if p.shots is None:
        cues = sp.cues_from_sequence(seq)
        if not cues:
            raise CommandError("NO_CAPTIONS", "no captions to plan from; give shots")
        plan = sp.make_plan(cues, reveal=tuple(p.reveal))
        rows = [(s.start, s.end, "", s.text) for s in plan.shots]
    else:
        rows = [(resolve_time(s.at, st), resolve_time(s.end, st), s.look, s.text) for s in p.shots]
    for a, b, *_ in rows:
        if b <= a:
            raise CommandError("BAD_SHOT", "a shot must end after it starts")
    for m in shorts_fill.shot_markers(seq):
        ctx.m.delete(["sequences", seq["id"], "markers", m["marker_id"]])
    for n, (a, b, look, text) in enumerate(sorted(rows), start=1):
        mid = new_id()
        ctx.m.set(["sequences", seq["id"], "markers", mid], {"id": mid, "start": a, "duration": b - a, "name": f"S{n}",
                                                             "comment": shorts_fill.comment(look, text), "color": p.color})
    return {"shots": len(rows)}
