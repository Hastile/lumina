"""FCP7 (xmeml v4) sequence generator - the route around Premiere's missing setters.

Why this exists: Premiere Pro 26.3 exposes no public API for several ordinary
edit operations. There is no speed setter (`createSetSpeedAction` reports
false), no reverse, no track add/delete, and no sequence-settings write. But
Premiere *imports* Final Cut Pro 7 XML, and an imported sequence can carry
all of them. This was verified live on 26.3: a generated XML produced a
6-video-track 60 fps sequence whose clips read back through Premiere's own
`getSpeed` as `speed: 2` and `speed: -1 / speedReversed: 1`, on a build whose
`createSetSpeedAction` was false.

Scope and honesty about it:

- Importing an XML **creates a new sequence**. It never edits an existing one
  in place. The workflow is read-existing -> generate -> import-as-new.
- A hand-built XML cannot round-trip a finished Premiere sequence. MOGRTs,
  Essential Graphics, Lumetri and other Premiere-native components have no
  FCP7 representation and are lost. Use this for programmatic assembly
  (rough cuts, B-roll layout, speed ramps), not for rewriting a graded,
  graphics-heavy timeline.
- Everything here is pure: spec in, XML string out. No file writes, no
  Premiere calls, no subprocess. pr-mcp.py owns all I/O, matching
  edit_plan.py's and premiere_edit_planning.py's convention.

Audio level calibration: the FCP7 `audiolevels` value is a **multiplier on
the import default**, not an absolute amplitude. Measured on 26.3 with five
data points, including a control clip carrying no audiolevels filter at all:

    xml value      -> Volume/Level raw
    (no filter)    -> 0.17782794      <- the import default
    1.0            -> 0.17782794      <- identical to the control
    0.5            -> 0.08891400
    0.4216965      -> 0.07498940
    0.17782794     -> 0.03162280

Every point fits `raw = xml * 0.17782794` exactly. An earlier reading of just
the last row suggested `raw = xml ** 2`; that was a coincidence - the test
value happened to equal the unity constant, so the square and the multiply
gave the same answer. The end-to-end import test is what caught it, which is
why `audio_level_xml_value` is pinned by a test with more than one point.

Motion calibration: the FCP7 `basic` and `opacity` filters survive import as
`AE.ADBE Motion` and `AE.ADBE Opacity`, keyframes included. Measured on 26.3
against a four-clip probe with a no-filter control:

  * `scale` maps 1:1 (sent 50 -> read 50).
  * `opacity` maps 1:1 (sent 50 -> read 50).
  * Keyframe `<when>` is in **frames, clip-relative** - the same domain
    `list_property_keyframes` reports. Sent when=0/59 at 30fps, read back at
    0.0s and 1.9667s exactly.
  * `<center>` is **not** in frame units. Its unit is the source's dimensions:
    `horiz=0.25` moved a 480-wide source by 120px and a 960-wide source by
    240px in the same 1920-wide sequence, and `vert=0.125` moved a 270-tall
    source by 33.75px. Hence `motion_center_value` below, and hence
    `sourceWidth`/`sourceHeight` being required whenever a pixel offset is
    requested - without the source size the offset is not computable.

    Sharper than that, and the thing that actually bit: the dimensions
    Premiere scales by are the ones **declared in this document's `<file>`
    `<samplecharacteristics>`**, not the real pixel size of the file on disk.
    They are the same number only if the declaration is honest. This
    generator used to declare the *sequence* size for every file, so a
    480-wide still asking for a -500px offset was scaled by 1920 instead and
    landed at x=-1040 - entirely off-screen, with correct-looking keyframes
    and a correct-looking readback. `sourceWidth`/`sourceHeight` now drive
    both the declaration and the conversion, so the two cannot drift apart.

The spec deliberately takes pixel offsets from frame centre rather than FCP7
units, because that is what Premiere's own Effect Controls displays and the
only thing an editor can sanity-check by eye. The conversion stays in here.

Two import behaviours worth knowing before trusting a readback:

  * Every imported still reads back `Scale Width = 109.4020004272461`,
    including a control clip with no filter at all and regardless of source
    size. It is an import baseline like `FCP7_IMPORT_UNITY_RAW`, it scales
    proportionally with `scale`, and it causes no visible distortion.
  * A still is **not** scaled to frame size on import - a 960x540 image
    occupies 960x540 of a 1920x1080 frame. Callers that want a target
    on-screen size must compute `scale` from the source dimensions.
"""

from __future__ import annotations

import math
import urllib.parse
from typing import Any, Optional
from xml.sax.saxutils import escape

SCHEMA_VERSION = "1.0"
XMEML_VERSION = "4"

MEDIA_TYPES = {"video", "audio"}

# Premiere accepts a wide range but these are the ones this generator has
# actually been exercised against; anything else is rejected rather than
# silently emitted and left to fail inside Premiere's importer.
SUPPORTED_TIMEBASES = {23, 24, 25, 29, 30, 50, 59, 60}


class Fcp7XmlError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _fail(code: str, message: str):
    raise Fcp7XmlError(code, message)


def _require_number(value, name, *, minimum=None, allow_none=False):
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail("INVALID_SPEC", f"{name} must be a number.")
    number = float(value)
    if not math.isfinite(number):
        _fail("INVALID_SPEC", f"{name} must be finite.")
    if minimum is not None and number < minimum:
        _fail("INVALID_SPEC", f"{name} must be >= {minimum}.")
    return number


def _require_int(value, name, *, minimum=None):
    if isinstance(value, bool) or not isinstance(value, int):
        _fail("INVALID_SPEC", f"{name} must be an integer.")
    if minimum is not None and value < minimum:
        _fail("INVALID_SPEC", f"{name} must be >= {minimum}.")
    return value


def path_to_pathurl(file_path: str) -> str:
    """Absolute Windows/POSIX path -> the file:// URL Premiere's importer wants.

    Non-ASCII paths (this project's media lives under Korean directories) are
    percent-encoded; leaving them raw makes Premiere silently fail to relink.
    """
    if not isinstance(file_path, str) or not file_path.strip():
        _fail("INVALID_SPEC", "filePath must be a non-empty string.")
    normalized = file_path.replace("\\", "/")
    return "file://localhost/" + urllib.parse.quote(normalized, safe="/:")


# Volume/Level raw value that an imported clip carrying no audiolevels filter
# ends up with, measured against a no-filter control clip on 26.3. The FCP7
# level multiplies this rather than replacing it.
FCP7_IMPORT_UNITY_RAW = 0.17782794

# Scale Width every imported still reads back at, control clip included. Not a
# filter artifact and not a distortion - recorded so a readback check can tell
# "the import did this" apart from "our filter did this".
FCP7_IMPORT_STILL_SCALE_WIDTH = 109.4020004272461


def motion_center_value(offset_pixels: float, source_dimension: float) -> float:
    """Pixel offset from frame centre -> the FCP7 <center> value that produces it.

    The unit is the source's own dimension, so the same pixel offset needs a
    different value for a 480-wide and a 960-wide image. Measured both ways;
    see the module docstring.
    """
    if not source_dimension or source_dimension <= 0:
        _fail("INVALID_SPEC", "sourceWidth/sourceHeight must be positive to convert a pixel offset.")
    return offset_pixels / float(source_dimension)


def audio_level_xml_value(level_db: float) -> float:
    """Target dB -> the FCP7 audiolevels value that produces it.

    dB follows the same convention as set_audio_clip_volume elsewhere in this
    project: `dB = 20 * log10(raw)`. Because the FCP7 value is a multiplier on
    the import default rather than an absolute amplitude, this works out to
    `10 ** ((level_db + 15) / 20)` - a level of 1.0 reproduces exactly what an
    unfiltered import gets.

    Caveat worth knowing: an imported clip with no filter reads back at the
    raw value this project's dB convention calls -15 dB. Whether Premiere's
    own UI labels that clip -15 dB or 0 dB was not verified - only the
    readback was. Values here are therefore consistent with
    set_audio_clip_volume/get_track_item_property, which is what matters for
    comparing before and after, but do not assume they match the fader
    number on screen until someone checks.
    """
    db = _require_number(level_db, "levelDb")
    return (10.0 ** (db / 20.0)) / FCP7_IMPORT_UNITY_RAW


def audio_level_expected_raw(level_db: float) -> float:
    """The Volume/Level raw value the import should produce for this dB."""
    return audio_level_xml_value(level_db) * FCP7_IMPORT_UNITY_RAW


def seconds_to_frames(seconds: float, timebase: int) -> int:
    """Round to the nearest whole frame - FCP7 XML has no sub-frame positions."""
    return int(round(float(seconds) * int(timebase)))


def timeline_frames_for_source(source_frames: int, speed_percent: float) -> int:
    """How many timeline frames `source_frames` of source occupy at a speed.

    Reverse is a direction, not a duration change, so the magnitude is used.
    """
    magnitude = abs(float(speed_percent))
    if magnitude <= 0:
        _fail("INVALID_SPEC", "speedPercent must be non-zero; use a still or frame hold for 0.")
    return max(1, int(round(source_frames * 100.0 / magnitude)))


def _normalize_keyframable(raw, name, timebase, *, minimum=None):
    """A bare number, or [{seconds, value}, ...] -> frame-stamped points.

    Both spellings collapse to the same shape so the emitter has one code
    path; a single point is written as a plain <value> rather than a
    one-keyframe animation, which is what a static parameter looks like in
    Premiere.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return [{"frames": 0, "value": _require_number(raw, name, minimum=minimum)}]
    if not isinstance(raw, list) or not raw:
        _fail("INVALID_SPEC", f"{name} must be a number or a non-empty list of keyframes.")

    points = []
    for position, entry in enumerate(raw):
        if not isinstance(entry, dict):
            _fail("INVALID_SPEC", f"{name}[{position}] must be an object with seconds/value.")
        seconds = _require_number(entry.get("seconds", 0), f"{name}[{position}].seconds", minimum=0)
        value = _require_number(entry.get("value"), f"{name}[{position}].value", minimum=minimum)
        points.append({"frames": seconds_to_frames(seconds, timebase), "value": value})

    points.sort(key=lambda p: p["frames"])
    if len({p["frames"] for p in points}) != len(points):
        _fail("INVALID_SPEC", f"{name} has two keyframes on the same frame.")
    return points


def _normalize_center(raw, name, timebase, source_width, source_height):
    """{x, y} or [{seconds, x, y}, ...] pixel offsets -> FCP7 center points.

    Offsets are pixels from the frame centre, positive right/down. The source
    dimensions are what makes them convertible at all - see the docstring.
    """
    if raw is None:
        return None
    entries = raw if isinstance(raw, list) else [raw]
    if not entries:
        _fail("INVALID_SPEC", f"{name} must not be an empty list.")
    if source_width is None or source_height is None:
        _fail("INVALID_SPEC", f"{name} requires sourceWidth and sourceHeight on the same clip.")

    points = []
    for position, entry in enumerate(entries):
        if not isinstance(entry, dict):
            _fail("INVALID_SPEC", f"{name}[{position}] must be an object with x/y.")
        seconds = _require_number(entry.get("seconds", 0), f"{name}[{position}].seconds", minimum=0)
        x = _require_number(entry.get("x", 0), f"{name}[{position}].x")
        y = _require_number(entry.get("y", 0), f"{name}[{position}].y")
        points.append({
            "frames": seconds_to_frames(seconds, timebase),
            "horiz": motion_center_value(x, source_width),
            "vert": motion_center_value(y, source_height),
        })

    points.sort(key=lambda p: p["frames"])
    if len({p["frames"] for p in points}) != len(points):
        _fail("INVALID_SPEC", f"{name} has two keyframes on the same frame.")
    return points


def _normalize_motion(raw, index, timebase):
    """clips[i].motion -> normalized scale/rotation/center, or None."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        _fail("INVALID_SPEC", f"clips[{index}].motion must be an object.")

    label = f"clips[{index}].motion"
    source_width = raw.get("sourceWidth")
    source_height = raw.get("sourceHeight")
    for field, value in (("sourceWidth", source_width), ("sourceHeight", source_height)):
        if value is not None:
            _require_int(value, f"{label}.{field}", minimum=1)

    motion = {
        "scale": _normalize_keyframable(raw.get("scale"), f"{label}.scale", timebase, minimum=0),
        "rotation": _normalize_keyframable(raw.get("rotation"), f"{label}.rotation", timebase),
        "center": _normalize_center(
            raw.get("positionOffsetPixels"), f"{label}.positionOffsetPixels",
            timebase, source_width, source_height,
        ),
        "sourceWidth": source_width,
        "sourceHeight": source_height,
    }
    if not any(motion[key] for key in ("scale", "rotation", "center")):
        _fail("INVALID_SPEC", f"{label} sets none of scale/rotation/positionOffsetPixels.")
    return motion


def _normalize_clip(raw, index, timebase):
    if not isinstance(raw, dict):
        _fail("INVALID_SPEC", f"clips[{index}] must be an object.")
    media_type = raw.get("mediaType", "video")
    if media_type not in MEDIA_TYPES:
        _fail("INVALID_SPEC", f"clips[{index}].mediaType must be one of {sorted(MEDIA_TYPES)}.")
    track_index = _require_int(raw.get("trackIndex", 0), f"clips[{index}].trackIndex", minimum=0)
    file_path = raw.get("filePath")
    if not isinstance(file_path, str) or not file_path.strip():
        _fail("INVALID_SPEC", f"clips[{index}].filePath is required.")

    start_seconds = _require_number(raw.get("startSeconds", 0), f"clips[{index}].startSeconds", minimum=0)
    source_in = _require_number(raw.get("sourceInSeconds", 0), f"clips[{index}].sourceInSeconds", minimum=0)
    source_out = _require_number(raw.get("sourceOutSeconds"), f"clips[{index}].sourceOutSeconds", minimum=0)
    if source_out <= source_in:
        _fail("INVALID_SPEC", f"clips[{index}].sourceOutSeconds must be greater than sourceInSeconds.")

    speed_percent = _require_number(raw.get("speedPercent", 100), f"clips[{index}].speedPercent")
    reverse = raw.get("reverse", False)
    if type(reverse) is not bool:
        _fail("INVALID_SPEC", f"clips[{index}].reverse must be bool.")
    # A negative speedPercent and reverse=true mean the same thing; accept
    # either spelling but emit one consistent representation.
    if speed_percent < 0:
        reverse = True
    speed_magnitude = abs(speed_percent)
    if speed_magnitude == 0:
        _fail("INVALID_SPEC", f"clips[{index}].speedPercent must be non-zero.")

    level_db = raw.get("levelDb")
    if level_db is not None:
        if media_type != "audio":
            _fail("INVALID_SPEC", f"clips[{index}].levelDb applies to audio clips only.")
        _require_number(level_db, f"clips[{index}].levelDb")

    # Motion and opacity are video-only in FCP7 and in Premiere both. Failing
    # closed here beats emitting a filter the importer will silently drop.
    for field in ("motion", "opacity"):
        if raw.get(field) is not None and media_type != "video":
            _fail("INVALID_SPEC", f"clips[{index}].{field} applies to video clips only.")
    motion = _normalize_motion(raw.get("motion"), index, timebase)
    opacity = _normalize_keyframable(raw.get("opacity"), f"clips[{index}].opacity", timebase, minimum=0)

    # Hoisted so the <file> declaration and the <center> conversion use one
    # number. Declaring the sequence size here while converting against the
    # real size puts the clip off-screen with no error anywhere - see the
    # module docstring.
    source_width = raw.get("sourceWidth") or (motion or {}).get("sourceWidth")
    source_height = raw.get("sourceHeight") or (motion or {}).get("sourceHeight")
    for field, value in (("sourceWidth", source_width), ("sourceHeight", source_height)):
        if value is not None:
            _require_int(value, f"clips[{index}].{field}", minimum=1)
    if (source_width is None) != (source_height is None):
        _fail("INVALID_SPEC", f"clips[{index}] needs both sourceWidth and sourceHeight, or neither.")

    source_in_frames = seconds_to_frames(source_in, timebase)
    source_out_frames = seconds_to_frames(source_out, timebase)
    source_frames = source_out_frames - source_in_frames
    if source_frames <= 0:
        _fail("INVALID_SPEC", f"clips[{index}] source range is shorter than one frame at {timebase} fps.")
    start_frames = seconds_to_frames(start_seconds, timebase)
    timeline_frames = timeline_frames_for_source(source_frames, speed_magnitude)

    name = raw.get("name")
    if name is not None and not isinstance(name, str):
        _fail("INVALID_SPEC", f"clips[{index}].name must be a string.")

    return {
        "index": index,
        "name": name or f"clip_{index}",
        "mediaType": media_type,
        "trackIndex": track_index,
        "filePath": file_path,
        "startFrames": start_frames,
        "endFrames": start_frames + timeline_frames,
        "sourceInFrames": source_in_frames,
        "sourceOutFrames": source_out_frames,
        "timelineFrames": timeline_frames,
        "speedPercent": speed_magnitude,
        "reverse": reverse,
        "levelDb": float(level_db) if level_db is not None else None,
        "motion": motion,
        "opacity": opacity,
        "sourceWidth": source_width,
        "sourceHeight": source_height,
    }


def _detect_overlaps(clips):
    """Two clips on one track cannot occupy the same frame range."""
    conflicts = []
    by_track: dict[tuple[str, int], list] = {}
    for clip in clips:
        by_track.setdefault((clip["mediaType"], clip["trackIndex"]), []).append(clip)
    for (media_type, track_index), group in sorted(by_track.items()):
        ordered = sorted(group, key=lambda c: c["startFrames"])
        for left, right in zip(ordered, ordered[1:]):
            if right["startFrames"] < left["endFrames"]:
                conflicts.append({
                    "mediaType": media_type, "trackIndex": track_index,
                    "left": left["name"], "right": right["name"],
                    "leftEndFrames": left["endFrames"], "rightStartFrames": right["startFrames"],
                })
    return conflicts


def normalize_sequence_spec(spec: Any) -> dict[str, Any]:
    """Validate and expand a sequence spec. Raises Fcp7XmlError on bad input."""
    if not isinstance(spec, dict):
        _fail("INVALID_SPEC", "spec must be an object.")

    name = spec.get("name")
    if not isinstance(name, str) or not name.strip():
        _fail("INVALID_SPEC", "name is required.")

    timebase = _require_int(spec.get("frameRate", 30), "frameRate", minimum=1)
    if timebase not in SUPPORTED_TIMEBASES:
        _fail("INVALID_SPEC", f"frameRate {timebase} is outside the verified set {sorted(SUPPORTED_TIMEBASES)}.")
    ntsc = spec.get("ntsc", False)
    if type(ntsc) is not bool:
        _fail("INVALID_SPEC", "ntsc must be bool.")

    width = _require_int(spec.get("width", 1920), "width", minimum=16)
    height = _require_int(spec.get("height", 1080), "height", minimum=16)

    raw_clips = spec.get("clips")
    if not isinstance(raw_clips, list) or not raw_clips:
        _fail("INVALID_SPEC", "clips must be a non-empty list.")
    clips = [_normalize_clip(raw, index, timebase) for index, raw in enumerate(raw_clips)]

    conflicts = _detect_overlaps(clips)
    if conflicts:
        _fail("CLIP_OVERLAP", f"{len(conflicts)} clip pair(s) overlap on the same track: {conflicts}")

    implied_video = max((c["trackIndex"] for c in clips if c["mediaType"] == "video"), default=-1) + 1
    implied_audio = max((c["trackIndex"] for c in clips if c["mediaType"] == "audio"), default=-1) + 1
    video_tracks = _require_int(spec.get("videoTrackCount", implied_video), "videoTrackCount", minimum=0)
    audio_tracks = _require_int(spec.get("audioTrackCount", implied_audio), "audioTrackCount", minimum=0)
    if video_tracks < implied_video:
        _fail("INVALID_SPEC", f"videoTrackCount {video_tracks} is fewer than the {implied_video} track(s) the clips use.")
    if audio_tracks < implied_audio:
        _fail("INVALID_SPEC", f"audioTrackCount {audio_tracks} is fewer than the {implied_audio} track(s) the clips use.")

    markers = []
    for index, raw in enumerate(spec.get("markers") or []):
        if not isinstance(raw, dict):
            _fail("INVALID_SPEC", f"markers[{index}] must be an object.")
        marker_name = raw.get("name")
        if not isinstance(marker_name, str) or not marker_name.strip():
            _fail("INVALID_SPEC", f"markers[{index}].name is required.")
        seconds = _require_number(raw.get("seconds", 0), f"markers[{index}].seconds", minimum=0)
        comment = raw.get("comment") or ""
        if not isinstance(comment, str):
            _fail("INVALID_SPEC", f"markers[{index}].comment must be a string.")
        markers.append({"name": marker_name, "comment": comment, "frames": seconds_to_frames(seconds, timebase)})

    duration_frames = max((c["endFrames"] for c in clips), default=0)

    return {
        "schemaVersion": SCHEMA_VERSION,
        "name": name, "frameRate": timebase, "ntsc": ntsc,
        "width": width, "height": height,
        "videoTrackCount": video_tracks, "audioTrackCount": audio_tracks,
        "clips": clips, "markers": markers, "durationFrames": duration_frames,
    }


def _rate(timebase, ntsc):
    return f"<rate><timebase>{timebase}</timebase><ntsc>{'TRUE' if ntsc else 'FALSE'}</ntsc></rate>"


def _file_element(clip, file_id, first_use, timebase, ntsc, width, height):
    # FCP7 declares a <file> once and references it by id afterwards. Emitting
    # the full block twice makes Premiere import the same media as two
    # separate project items.
    if not first_use:
        return f'<file id="{file_id}"/>'
    # The sequence size is only a fallback. When the caller knows the real
    # source size, declaring it is what makes <center> mean what the pixel
    # offsets asked for.
    declared_width = clip.get("sourceWidth") or width
    declared_height = clip.get("sourceHeight") or height
    media = (
        f"<video><samplecharacteristics><width>{declared_width}</width>"
        f"<height>{declared_height}</height></samplecharacteristics></video>"
        if clip["mediaType"] == "video" else "<audio><channelcount>2</channelcount></audio>"
    )
    return (
        f'<file id="{file_id}">'
        f"<name>{escape(clip['name'])}</name>"
        f"<pathurl>{escape(path_to_pathurl(clip['filePath']))}</pathurl>"
        f"{_rate(timebase, ntsc)}"
        f"<media>{media}</media>"
        f"</file>"
    )


def _timeremap_filter(clip):
    # Premiere reads speed/reverse from this filter; omitting it leaves the
    # clip at 100% no matter what the in/out-to-start/end ratio implies.
    return (
        "<filter><effect>"
        "<name>Time Remap</name><effectid>timeremap</effectid>"
        "<effectcategory>motion</effectcategory><effecttype>motion</effecttype>"
        "<mediatype>video</mediatype>"
        "<parameter><parameterid>variablespeed</parameterid><name>variablespeed</name>"
        "<valuemin>0</valuemin><valuemax>1</valuemax><value>0</value></parameter>"
        "<parameter><parameterid>speed</parameterid><name>speed</name>"
        "<valuemin>-10000</valuemin><valuemax>10000</valuemax>"
        f"<value>{-clip['speedPercent'] if clip['reverse'] else clip['speedPercent']:g}</value></parameter>"
        "<parameter><parameterid>reverse</parameterid><name>reverse</name>"
        f"<value>{'TRUE' if clip['reverse'] else 'FALSE'}</value></parameter>"
        "</effect></filter>"
    )


def _scalar_parameter(parameter_id, name, points, *, vmin, vmax):
    """One <parameter>: a lone point becomes <value>, several become keyframes."""
    head = (
        f"<parameterid>{parameter_id}</parameterid><name>{name}</name>"
        f"<valuemin>{vmin}</valuemin><valuemax>{vmax}</valuemax>"
    )
    if len(points) == 1:
        return f"<parameter>{head}<value>{points[0]['value']:g}</value></parameter>"
    body = "".join(
        f"<keyframe><when>{p['frames']}</when><value>{p['value']:g}</value></keyframe>"
        for p in points
    )
    return f"<parameter>{head}{body}</parameter>"


def _center_parameter(points):
    head = "<parameterid>center</parameterid><name>Center</name>"
    if len(points) == 1:
        point = points[0]
        return (
            f"<parameter>{head}"
            f"<value><horiz>{point['horiz']:g}</horiz><vert>{point['vert']:g}</vert></value>"
            "</parameter>"
        )
    body = "".join(
        f"<keyframe><when>{p['frames']}</when>"
        f"<value><horiz>{p['horiz']:g}</horiz><vert>{p['vert']:g}</vert></value></keyframe>"
        for p in points
    )
    return f"<parameter>{head}{body}</parameter>"


def _basic_motion_filter(motion):
    """Position/scale/rotation. Lands on Premiere's built-in Motion component."""
    params = []
    if motion["scale"] is not None:
        params.append(_scalar_parameter("scale", "Scale", motion["scale"], vmin=0, vmax=1000))
    if motion["rotation"] is not None:
        params.append(_scalar_parameter("rotation", "Rotation", motion["rotation"], vmin=-8640, vmax=8640))
    if motion["center"] is not None:
        params.append(_center_parameter(motion["center"]))
    return (
        "<filter><effect>"
        "<name>Basic Motion</name><effectid>basic</effectid>"
        "<effectcategory>motion</effectcategory><effecttype>motion</effecttype>"
        "<mediatype>video</mediatype>"
        f"{''.join(params)}"
        "</effect></filter>"
    )


def _opacity_filter(points):
    # Opacity is its own FCP7 effect, not a parameter of basic motion, and it
    # arrives on a separate Premiere component (AE.ADBE Opacity).
    return (
        "<filter><effect>"
        "<name>Opacity</name><effectid>opacity</effectid>"
        "<effectcategory>motion</effectcategory><effecttype>motion</effecttype>"
        "<mediatype>video</mediatype>"
        f"{_scalar_parameter('opacity', 'opacity', points, vmin=0, vmax=100)}"
        "</effect></filter>"
    )


def _audiolevels_filter(level_db):
    value = audio_level_xml_value(level_db)
    return (
        "<filter><effect>"
        "<name>Audio Levels</name><effectid>audiolevels</effectid>"
        "<effectcategory>audiolevels</effectcategory><effecttype>audiolevels</effecttype>"
        "<mediatype>audio</mediatype>"
        "<parameter><parameterid>level</parameterid><name>Level</name>"
        f"<valuemin>0</valuemin><valuemax>3.98109</valuemax><value>{value:.8f}</value></parameter>"
        "</effect></filter>"
    )


def _clipitem(clip, file_id, first_use, normalized):
    timebase, ntsc = normalized["frameRate"], normalized["ntsc"]
    parts = [
        f'<clipitem id="clip{clip["index"]}">',
        f"<name>{escape(clip['name'])}</name>",
        "<enabled>TRUE</enabled>",
        f"<duration>{clip['sourceOutFrames']}</duration>",
        _rate(timebase, ntsc),
        f"<start>{clip['startFrames']}</start><end>{clip['endFrames']}</end>",
        f"<in>{clip['sourceInFrames']}</in><out>{clip['sourceOutFrames']}</out>",
        _file_element(clip, file_id, first_use, timebase, ntsc, normalized["width"], normalized["height"]),
    ]
    if clip["mediaType"] == "video":
        parts.append(_timeremap_filter(clip))
        if clip.get("motion") is not None:
            parts.append(_basic_motion_filter(clip["motion"]))
        if clip.get("opacity") is not None:
            parts.append(_opacity_filter(clip["opacity"]))
    else:
        parts.append("<sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack>")
        if clip["levelDb"] is not None:
            parts.append(_audiolevels_filter(clip["levelDb"]))
    parts.append("</clipitem>")
    return "".join(parts)


def build_fcp7_xml(spec: Any) -> str:
    """Render a normalized-or-raw spec into an importable xmeml document."""
    normalized = spec if spec.get("schemaVersion") == SCHEMA_VERSION else normalize_sequence_spec(spec)
    timebase, ntsc = normalized["frameRate"], normalized["ntsc"]

    # One <file> declaration per distinct media path, referenced thereafter.
    file_ids: dict[str, str] = {}
    declared: set[str] = set()
    for clip in normalized["clips"]:
        file_ids.setdefault(clip["filePath"], f"file{len(file_ids)}")

    def track_xml(media_type, track_index):
        items = [c for c in normalized["clips"] if c["mediaType"] == media_type and c["trackIndex"] == track_index]
        body = []
        for clip in sorted(items, key=lambda c: c["startFrames"]):
            file_id = file_ids[clip["filePath"]]
            first_use = file_id not in declared
            declared.add(file_id)
            body.append(_clipitem(clip, file_id, first_use, normalized))
        extra = "<outputchannelindex>1</outputchannelindex>" if media_type == "audio" else ""
        return f"<track>{''.join(body)}<enabled>TRUE</enabled><locked>FALSE</locked>{extra}</track>"

    video_tracks = "".join(track_xml("video", i) for i in range(normalized["videoTrackCount"]))
    audio_tracks = "".join(track_xml("audio", i) for i in range(normalized["audioTrackCount"]))
    markers = "".join(
        f"<marker><name>{escape(m['name'])}</name><comment>{escape(m['comment'])}</comment>"
        f"<in>{m['frames']}</in><out>-1</out></marker>"
        for m in normalized["markers"]
    )

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<!DOCTYPE xmeml>\n"
        f'<xmeml version="{XMEML_VERSION}">'
        '<sequence id="generated">'
        f"<name>{escape(normalized['name'])}</name>"
        f"<duration>{normalized['durationFrames']}</duration>"
        f"{_rate(timebase, ntsc)}"
        "<media>"
        "<video><format><samplecharacteristics>"
        f"{_rate(timebase, ntsc)}"
        f"<width>{normalized['width']}</width><height>{normalized['height']}</height>"
        "</samplecharacteristics></format>"
        f"{video_tracks}</video>"
        f"<audio>{audio_tracks}</audio>"
        "</media>"
        f"{markers}"
        "</sequence></xmeml>\n"
    )


def assign_track_ordinals(items: list[dict[str, Any]], *, start_key: str) -> None:
    """Number each item by its start-time position within its own
    (mediaType, trackIndex) track, in place.

    Track index alone does not identify a clip once a track holds more than
    one - and a timeline built by programmatic assembly holds many. The
    ordinal is what lets expected and actual clips be paired one-to-one
    instead of collapsing to whichever clip happens to be last on the track.
    Items missing a usable start sort last, keeping their relative order, so
    a partial readback still pairs the clips it can rather than raising.
    """
    by_track: dict[tuple[str, int], list] = {}
    for item in items:
        by_track.setdefault((item.get("mediaType"), item.get("trackIndex")), []).append(item)
    for group in by_track.values():
        ordered = sorted(
            enumerate(group),
            key=lambda pair: (pair[1].get(start_key) is None, pair[1].get(start_key) or 0, pair[0]),
        )
        for ordinal, (_, item) in enumerate(ordered):
            item["ordinal"] = ordinal


def expected_import_result(normalized: dict[str, Any]) -> dict[str, Any]:
    """What the imported sequence should look like, for post-import verification.

    Speeds are expressed the way Premiere's own getSpeed reports them - a
    ratio, not a percentage, and negative when reversed - so a caller can
    compare without re-deriving the convention.
    """
    items = []
    for clip in normalized["clips"]:
        ratio = clip["speedPercent"] / 100.0
        items.append({
            "name": clip["name"],
            "mediaType": clip["mediaType"],
            "trackIndex": clip["trackIndex"],
            "startSeconds": clip["startFrames"] / normalized["frameRate"],
            "endSeconds": clip["endFrames"] / normalized["frameRate"],
            "speed": -ratio if clip["reverse"] else ratio,
            "speedReversed": clip["reverse"],
        })
    assign_track_ordinals(items, start_key="startSeconds")
    return {
        "name": normalized["name"],
        "frameRate": normalized["frameRate"],
        "videoTrackCount": normalized["videoTrackCount"],
        "audioTrackCount": normalized["audioTrackCount"],
        "markerCount": len(normalized["markers"]),
        "items": items,
    }


def compare_imported_sequence(expected: dict[str, Any], actual: dict[str, Any], *, tolerance_seconds: float = 0.05):
    """Diff expected-vs-live. Returns a list of mismatch dicts (empty = match)."""
    mismatches = []

    def note(field, want, got):
        mismatches.append({"field": field, "expected": want, "actual": got})

    for field in ("frameRate", "videoTrackCount", "audioTrackCount", "markerCount"):
        if field in actual and actual[field] != expected[field]:
            note(field, expected[field], actual[field])

    # Keyed by ordinal as well as track: a track holding N clips would
    # otherwise collapse to its last clip, and every other expected clip on
    # that track would be diffed against it - producing N-1 fabricated
    # mismatches on any sequence with more than one clip per track.
    #
    # Ordinals are (re)derived here rather than trusted from the caller, so
    # a live summary assembled by any other reader still pairs correctly.
    # Shallow copies keep that renumbering out of the caller's dicts.
    live_items = [dict(item) for item in actual.get("items") or []]
    assign_track_ordinals(live_items, start_key="startSeconds")
    actual_items = {
        (i.get("mediaType"), i.get("trackIndex"), i.get("ordinal")): i
        for i in live_items
    }
    for want in expected["items"]:
        key = (want["mediaType"], want["trackIndex"], want.get("ordinal"))
        got = actual_items.get(key)
        if got is None:
            note(f"items[{key}]", want, None)
            continue
        for field in ("startSeconds", "endSeconds"):
            if field in got and got[field] is not None and abs(float(got[field]) - want[field]) > tolerance_seconds:
                note(f"items[{key}].{field}", want[field], got[field])
        if got.get("speed") is not None and abs(float(got["speed"]) - want["speed"]) > 1e-6:
            note(f"items[{key}].speed", want["speed"], got["speed"])
    return mismatches
