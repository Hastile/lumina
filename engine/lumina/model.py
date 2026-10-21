"""Document schema.

The live document is a plain JSON-compatible dict (so it can be patched,
diffed, logged and sent to the UI cheaply). These pydantic models are the
contract for that dict: they validate projects on load/save and generate
``shared/schema/document.schema.json`` from which the UI's TypeScript types
are produced.

Entities are keyed by stable UUIDs; nothing is addressed by index, so ripples
and reorders never invalidate a reference.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1

LabelColor = Literal[
    "violet", "iris", "caribbean", "lavender", "cerulean", "forest", "rose",
    "mango", "purple", "blue", "teal", "magenta", "tan", "green", "brown", "yellow",
]


def new_id() -> str:
    return uuid.uuid4().hex


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)


class VideoStream(_Model):
    width: int
    height: int
    fps_num: int
    fps_den: int
    codec: str = ""
    pix_fmt: str = ""


class AudioStream(_Model):
    channels: int
    sample_rate: int
    codec: str = ""


class MediaItem(_Model):
    id: str
    name: str
    path: str
    bin_id: str
    kind: Literal["video", "audio", "image"]
    duration: int = Field(description="ticks; 0 for stills")
    video: VideoStream | None = None
    audio: AudioStream | None = None
    label: LabelColor = "iris"
    proxy_path: str | None = None
    thumbnail_path: str | None = None
    waveform_path: str | None = None
    filmstrip_path: str | None = Field(default=None, description="timeline thumbnails: JSON {image, interval, count, width, height}")
    in_point: int | None = Field(default=None, description="source monitor In mark, ticks")
    out_point: int | None = Field(default=None, description="source monitor Out mark, ticks")
    offline: bool = Field(default=False, description="Make Offline: shown as missing media even if the file exists")


class Bin(_Model):
    id: str
    name: str
    parent_id: str | None


KeyframeProp = Literal[
    "position", "scale", "scale_width", "rotation", "anchor",
    "crop_left", "crop_top", "crop_right", "crop_bottom", "opacity", "volume_db",
]


class Keyframe(_Model):
    """Temporal interpolation follows After Effects/Premiere: each side of a
    keyframe is linear, hold, or a Bezier handle given as [speed, influence]
    (value units per second of source time, fraction 0-1 of the segment).
    "ease" is a Bezier with Premiere's Easy Ease handles (speed 0, 33 %).
    "auto_bezier" computes its handles from the neighbours (a smooth curve
    through the keyframe); "continuous_bezier" keeps both sides at one speed.
    Point values may also carry spatial tangents (value units, relative to
    the keyframe) shaping the motion path."""
    t: int = Field(description="source time in ticks (Premiere convention: keyframes follow the content when trimmed)")
    v: float | list[float]
    interp: Literal["linear", "ease", "hold", "bezier", "auto_bezier", "continuous_bezier"] = "linear"
    ease_in: list[float] | None = Field(default=None, description="[speed, influence] of the incoming Bezier handle")
    ease_out: list[float] | None = Field(default=None, description="[speed, influence] of the outgoing Bezier handle")
    tangent_in: list[float] | None = Field(default=None, description="spatial in-tangent for point values")
    tangent_out: list[float] | None = Field(default=None, description="spatial out-tangent for point values")


class Motion(_Model):
    """Premiere's intrinsic Motion effect."""
    position: list[float] | None = Field(default=None, description="sequence pixels [x, y]; null = frame center")
    scale: float = 100.0
    scale_width: float = Field(default=100.0, description="used when uniform is false")
    uniform: bool = True
    rotation: float = Field(default=0.0, description="degrees, clockwise")
    anchor: list[float] | None = Field(default=None, description="source pixels [x, y]; null = source center")
    crop_left: float = 0.0
    crop_top: float = 0.0
    crop_right: float = 0.0
    crop_bottom: float = 0.0


class TextRun(_Model):
    """A styled span of a text layer; unset fields inherit the layer's."""
    text: str
    font_family: str | None = None
    font_size: float | None = None
    bold: bool | None = None
    italic: bool | None = None
    color: str | None = None
    stroke_color: str | None = None
    stroke_width: float | None = None
    tracking: float | None = None


class TextShadow(_Model):
    color: str = "#000000"
    opacity: float = Field(default=75.0, description="percent")
    angle: float = Field(default=135.0, description="degrees clockwise from up; 135 = down-right")
    distance: float = 10.0
    blur: float = Field(default=0.0, description="CSS text-shadow blur radius (Gaussian sigma = blur / 2)")


class TextLayer(_Model):
    id: str
    type: Literal["text"] = "text"
    text: str
    font_family: str = "Pretendard"
    font_size: float = 100.0
    bold: bool = False
    italic: bool = False
    color: str = "#ffffff"
    stroke_color: str | None = None
    stroke_width: float = 0.0
    background: str | None = None
    align: Literal["left", "center", "right"] = "center"
    position: list[float] = Field(description="sequence pixels: point text -> the block's alignment point at its vertical "
                                              "center; area text -> the box's top-left")
    box: list[float] | None = Field(default=None, description="area text box [w, h]; null = point text")
    vertical_align: Literal["top", "center", "bottom"] = Field(default="top", description="area text only")
    tracking: float = Field(default=0.0, description="1/1000 em added after each character")
    shadow: TextShadow | None = None
    runs: list[TextRun] | None = Field(default=None, description="styled spans; their texts concatenate to `text`")
    scale: float = 100.0
    rotation: float = 0.0
    opacity: float = 100.0
    visible: bool = Field(default=True, description="the layer's eye toggle")
    approximate: bool = Field(default=False, description="imported with best-effort styling (e.g. from a MOGRT)")


class ShapeLayer(_Model):
    """A vector shape. Its path lives in layer pixels; ``anchor`` (layer pixels)
    lands on ``position`` (sequence pixels), then scale and rotation apply
    around it, like Premiere's per-layer transform."""
    id: str
    type: Literal["shape"] = "shape"
    shape: Literal["rectangle", "ellipse", "path"] = "rectangle"
    path: list[list[float]] = Field(description="vertices [x, y, in_x, in_y, out_x, out_y]; tangent handles are absolute points")
    closed: bool = True
    fill: str | None = Field(default="#ffffff", description="null = no fill")
    stroke_color: str | None = None
    stroke_width: float = 0.0
    position: list[float] = Field(description="sequence pixels where the anchor lands")
    anchor: list[float] = Field(default_factory=lambda: [0.0, 0.0], description="layer pixels")
    scale: float = 100.0
    scale_width: float = Field(default=100.0, description="used when uniform is false")
    uniform: bool = True
    rotation: float = 0.0
    opacity: float = 100.0
    visible: bool = Field(default=True, description="the layer's eye toggle")
    blend: Literal["normal", "multiply"] = Field(default="normal", description="blended with everything below in the sequence")


class ImageLayer(_Model):
    """A picture placed in a graphic (Premiere: New Layer > From File). ``anchor`` (image pixels,
    null = the image's centre) lands on ``position`` (sequence pixels), then scale and rotation
    apply around it."""
    id: str
    type: Literal["image"] = "image"
    path: str = Field(description="image file (png, jpg, webp, ...)")
    size: list[float] = Field(description="the image's pixel size [w, h]")
    position: list[float] = Field(description="sequence pixels where the anchor lands")
    anchor: list[float] | None = Field(default=None, description="image pixels; null = centre")
    scale: float = 100.0
    scale_width: float = Field(default=100.0, description="used when uniform is false")
    uniform: bool = True
    rotation: float = 0.0
    opacity: float = 100.0
    visible: bool = Field(default=True, description="the layer's eye toggle")
    blend: Literal["normal", "multiply"] = Field(default="normal", description="blended with everything below in the sequence")


GraphicLayer = Annotated[TextLayer | ShapeLayer | ImageLayer, Field(discriminator="type")]


class CaptionStyle(_Model):
    """A caption track's style (Premiere's track style): every cue on the track is
    drawn as one text layer with these settings and the cue's text."""
    font_family: str = "Pretendard"
    font_size: float = 60.0
    bold: bool = False
    italic: bool = False
    color: str = "#ffffff"
    stroke_color: str | None = "#000000"
    stroke_width: float = 4.0
    background: str | None = None
    align: Literal["left", "center", "right"] = "center"
    position: list[float] = Field(default_factory=lambda: [960.0, 950.0],
                                  description="sequence pixels; point text: the block's alignment point at its vertical center")
    box: list[float] | None = Field(default=None, description="area text box [w, h]; null = point text")
    vertical_align: Literal["top", "center", "bottom"] = "center"
    tracking: float = 0.0
    shadow: TextShadow | None = None
    scale: float = 100.0
    rotation: float = 0.0
    opacity: float = 100.0


class Caption(_Model):
    """A caption cue: its text, drawn with the track's style (overrides apply to this cue only)."""
    text: str
    style: dict[str, str | float | bool | list[float] | None] | None = Field(
        default=None, description="per-cue overrides of CaptionStyle fields")


class Graphic(_Model):
    layers: list[GraphicLayer] = Field(default_factory=list, description="bottom to top")
    source: str | None = Field(default=None, description="origin, e.g. 'mogrt:Title' for imported Premiere graphics")


EffectType = Literal["transform", "gaussian_blur", "lumetri", "parametric_eq", "compressor", "hard_limiter"]
TransitionType = Literal["cross_dissolve", "dip_to_black", "dip_to_white", "constant_power"]


class Effect(_Model):
    """A standard effect, applied in order before the intrinsic Motion/Opacity
    (Premiere's render order). Params and keyframes are keyed by the names in
    ``lumina.effects.EFFECTS``; keyframe times are source ticks, like Motion's.

    On a track (Track.effects) an effect applies to every clip of the track, after
    the clip's own effects and Motion, in sequence pixels; its keyframe times are
    then relative to each clip: from its In point (anchor "in"), from its Out point
    (anchor "out", negative), or 0..ref_duration stretched over the clip ("stretch")."""
    id: str
    type: EffectType
    enabled: bool = True
    params: dict[str, float | bool | list[float]] = Field(default_factory=dict)
    keyframes: dict[str, list[Keyframe]] = Field(default_factory=dict)
    anchor: Literal["in", "out", "stretch"] | None = Field(default=None, description="track effects: what keyframe times count from")
    ref_duration: int | None = Field(default=None, description="track effects, anchor stretch: the length keyframe times span")


class Transition(_Model):
    """A transition at an edit point. Its position follows the clips: the cut is
    ``clip_a``'s end (or ``clip_b``'s start for a single-sided one)."""
    id: str
    type: TransitionType = "cross_dissolve"
    clip_a: str | None = Field(default=None, description="outgoing clip (ends at the cut)")
    clip_b: str | None = Field(default=None, description="incoming clip (starts at the cut)")
    duration: int = Field(description="ticks")
    alignment: Literal["center", "start", "end"] = Field(
        default="center", description="center at cut / start at cut (over clip_b) / end at cut (over clip_a)")


class AutoMatchState(_Model):
    target: float = Field(description="LUFS it was matched to")
    lufs: float = Field(description="loudness measured before the match")
    gain_db: float = Field(description="the Audio Gain Auto-Match set")
    previous_gain_db: float = Field(description="the Audio Gain before it (Reset restores it)")


class Clip(_Model):
    id: str
    media_id: str | None = Field(description="null for graphics and nested sequences")
    sequence_id: str | None = Field(default=None, description="nested sequence (Premiere's Nest): the clip shows that "
                                    "sequence, and source_in is a time in it")
    name: str
    start: int = Field(description="timeline start, ticks (inclusive)")
    end: int = Field(description="timeline end, ticks (exclusive)")
    source_in: int = Field(description="source time at `start`, ticks")
    speed: float = 1.0
    enabled: bool = True
    link_id: str | None = Field(default=None, description="clips sharing a link_id move/trim together")
    label: LabelColor = "iris"
    opacity: float = 100.0
    volume_db: float = 0.0
    gain_db: float = Field(default=0.0, description="Audio Gain (Premiere's G dialog), applied before Volume")
    audio_tag: Literal["dialogue", "music", "sfx", "ambience"] | None = Field(default=None, description="Essential Sound audio type")
    muted: bool = Field(default=False, description="Essential Sound Mute: the clip is silent (it stays enabled)")
    auto_match: AutoMatchState | None = Field(default=None, description="the last Loudness Auto-Match (for its status and Reset)")
    motion: Motion = Field(default_factory=Motion)
    keyframes: dict[KeyframeProp, list[Keyframe]] = Field(default_factory=dict)
    graphic: Graphic | None = None
    caption: Caption | None = Field(default=None, description="a cue on a caption track")
    effects: list[Effect] = Field(default_factory=list)
    freeze: int | None = Field(default=None, description="Frame Hold: the source time shown for the whole clip (ticks)")
    adjustment: bool = Field(default=False, description="Adjustment Layer: its effects apply to everything below it "
                                                        "(Opacity mixes the result in; Motion and crop are not used)")


class Track(_Model):
    id: str
    kind: Literal["video", "audio"]
    name: str
    locked: bool = False
    sync_lock: bool = True
    hidden: bool = Field(default=False, description="video: eye toggle off")
    muted: bool = False
    solo: bool = False
    targeted: bool = True
    volume_db: float = Field(default=0.0, description="audio: Audio Track Mixer fader")
    pan: float = Field(default=0.0, ge=-100, le=100, description="audio: track pan / balance, -100 left .. 100 right")
    clips: dict[str, Clip] = Field(default_factory=dict)
    transitions: dict[str, Transition] = Field(default_factory=dict)
    effects: list[Effect] = Field(default_factory=list, description="video: track effects, applied to every clip of the track")
    caption_style: CaptionStyle | None = Field(default=None, description="set on a caption track (its cues are Clip.caption)")


class Marker(_Model):
    id: str
    start: int
    duration: int = 0
    name: str = ""
    comment: str = ""
    color: Literal["green", "red", "purple", "orange", "yellow", "white", "blue", "cyan"] = "green"


class SequenceSettings(_Model):
    width: int = 1920
    height: int = 1080
    fps_num: int = 30
    fps_den: int = 1
    sample_rate: int = 48000


class Sequence(_Model):
    id: str
    name: str
    bin_id: str
    settings: SequenceSettings = Field(default_factory=SequenceSettings)
    tracks: dict[str, Track] = Field(default_factory=dict)
    video_tracks: list[str] = Field(default_factory=list, description="bottom (V1) to top")
    audio_tracks: list[str] = Field(default_factory=list, description="top (A1) to bottom")
    markers: dict[str, Marker] = Field(default_factory=dict)
    playhead: int = 0
    in_point: int | None = None
    out_point: int | None = None
    master_volume_db: float = Field(default=0.0, description="Audio Track Mixer's Mix fader")
    source_video_track: str | None = Field(default=None, description="source patching: the track source video goes to "
                                           "(Insert/Overwrite); null = the first targeted video track; '' = none")
    source_audio_track: str | None = Field(default=None, description="source patching for audio; null = first targeted audio track; '' = none")


class CaptionCue(_Model):
    start: int = Field(description="ticks from the start of the captions")
    end: int
    text: str


class Asset(_Model):
    """A project item that is not a media file: a captions file (its cues), a Motion
    Graphics Template, or an Adjustment Layer. Placed on the timeline with asset.place."""
    id: str
    name: str
    bin_id: str
    kind: Literal["captions", "template", "adjustment"]
    path: str | None = Field(default=None, description="template: the .mogrt / .aegraphic file; captions: the source .srt, if any")
    cues: list[CaptionCue] | None = Field(default=None, description="captions: the cues")
    label: LabelColor = "rose"


class Project(_Model):
    schema_version: int = SCHEMA_VERSION
    id: str
    name: str
    root_bin_id: str
    bins: dict[str, Bin] = Field(default_factory=dict)
    media: dict[str, MediaItem] = Field(default_factory=dict)
    sequences: dict[str, Sequence] = Field(default_factory=dict)
    assets: dict[str, Asset] = Field(default_factory=dict, description="captions, templates and adjustment layers")
    active_sequence_id: str | None = None


def new_project(name: str = "Untitled") -> dict:
    root = new_id()
    project = Project(
        id=new_id(),
        name=name,
        root_bin_id=root,
        bins={root: Bin(id=root, name=name, parent_id=None)},
    )
    return project.model_dump(mode="json")


def validate_project(doc: dict) -> Project:
    return Project.model_validate(doc)
