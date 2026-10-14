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
from typing import Literal

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
    in_point: int | None = Field(default=None, description="source monitor In mark, ticks")
    out_point: int | None = Field(default=None, description="source monitor Out mark, ticks")


class Bin(_Model):
    id: str
    name: str
    parent_id: str | None


class Clip(_Model):
    id: str
    media_id: str
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
    clips: dict[str, Clip] = Field(default_factory=dict)


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


class Project(_Model):
    schema_version: int = SCHEMA_VERSION
    id: str
    name: str
    root_bin_id: str
    bins: dict[str, Bin] = Field(default_factory=dict)
    media: dict[str, MediaItem] = Field(default_factory=dict)
    sequences: dict[str, Sequence] = Field(default_factory=dict)
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
