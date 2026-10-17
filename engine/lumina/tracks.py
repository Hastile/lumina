"""Track-level features shared by the commands and the renderers.

* Caption tracks: a video track with a ``caption_style``. Its clips carry
  ``caption = {text, style}`` and are drawn as a one-layer text graphic built from
  the track style, so cues stay plain text (no conversion to graphics) and a
  style change on the track restyles every cue.
* Track effects: ``track.effects`` apply to every clip of a video track, after
  the clip's own effects and Motion, in sequence pixels. Their keyframe times
  are relative to each clip (see ``model.Effect``); ``timing_clip`` turns that
  into the clip-like timing the keyframe evaluator expects.

The GUI has a TypeScript twin (app/src/renderer/src/util/tracks.ts).
"""

from __future__ import annotations

CAPTION_LAYER_ID = "caption"
STYLE_FIELDS = (
    "font_family", "font_size", "bold", "italic", "color", "stroke_color", "stroke_width", "background", "align",
    "position", "box", "vertical_align", "tracking", "shadow", "scale", "rotation", "opacity",
)


def default_caption_style(settings: dict) -> dict:
    """Premiere-like default: white text with a black edge, bottom centre."""
    w, h = settings["width"], settings["height"]
    short = min(w, h)
    return {
        "font_family": "Pretendard", "font_size": float(round(short * 0.055)), "bold": True, "italic": False,
        "color": "#ffffff", "stroke_color": "#000000", "stroke_width": max(2.0, round(short * 0.004, 1)), "background": None,
        "align": "center", "position": [w / 2, h * (0.82 if h > w else 0.88)], "box": None, "vertical_align": "center",
        "tracking": 0.0, "shadow": None, "scale": 100.0, "rotation": 0.0, "opacity": 100.0,
    }


def caption_layer(style: dict, caption: dict) -> dict:
    s = {k: style.get(k) for k in STYLE_FIELDS if k in style}
    s.update({k: v for k, v in (caption.get("style") or {}).items() if k in STYLE_FIELDS})
    return {**s, "id": CAPTION_LAYER_ID, "type": "text", "text": caption.get("text", ""), "runs": None, "visible": True,
            "approximate": False}


def clip_graphic(clip: dict, track: dict | None) -> dict | None:
    """What a clip draws as a graphic: its own graphic, or a caption cue in its track's style."""
    if clip.get("graphic") is not None:
        return clip["graphic"]
    cap = clip.get("caption")
    if cap is not None:
        style = (track or {}).get("caption_style") or {}
        return {"layers": [caption_layer(style, cap)], "source": "caption"}
    return None


def is_caption_track(track: dict) -> bool:
    return track.get("caption_style") is not None


def timing_clip(effect: dict, clip: dict) -> dict:
    """Clip-like timing whose source time is the track effect's keyframe time:
    source_time(timing, t) = t - In (anchor in), t - Out (anchor out), or the
    position in 0..ref_duration (stretch)."""
    anchor = effect.get("anchor") or "in"
    if anchor == "out":
        return {"start": clip["end"], "end": clip["end"] + 1, "source_in": 0, "speed": 1.0}
    if anchor == "stretch":
        length = max(1, clip["end"] - clip["start"])
        ref = effect.get("ref_duration") or length
        return {"start": clip["start"], "end": clip["end"], "source_in": 0, "speed": ref / length}
    return {"start": clip["start"], "end": clip["end"], "source_in": 0, "speed": 1.0}


def active_track_effects(track: dict | None) -> list[dict]:
    return [e for e in (track or {}).get("effects") or [] if e.get("enabled", True)]


REVERSE_EPSILON = 254_016_000  # 1 ms: well below a frame, above ffmpeg's microsecond seek resolution


def media_time(clip: dict, source_t: int) -> int:
    """The media time a clip shows at a source time: Frame Hold pins it; a reversed
    clip (which covers [source_at(end), source_in)) shows the frame just before."""
    if clip.get("freeze") is not None:
        return clip["freeze"]
    return max(0, source_t - REVERSE_EPSILON) if clip.get("speed", 1.0) < 0 else source_t
