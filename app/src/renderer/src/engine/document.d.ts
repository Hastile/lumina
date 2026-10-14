/* Generated from shared/schema/document.schema.json by scripts/gen-types.mjs. Do not edit. */

export type SchemaVersion = number;
export type Id = string;
export type Name = string;
export type RootBinId = string;
export type Id1 = string;
export type Name1 = string;
export type ParentId = string | null;
export type Id2 = string;
export type Name2 = string;
export type Path = string;
export type BinId = string;
export type Kind = "video" | "audio" | "image";
/**
 * ticks; 0 for stills
 */
export type Duration = number;
export type Width = number;
export type Height = number;
export type FpsNum = number;
export type FpsDen = number;
export type Codec = string;
export type PixFmt = string;
export type Channels = number;
export type SampleRate = number;
export type Codec1 = string;
export type Label =
  | "violet"
  | "iris"
  | "caribbean"
  | "lavender"
  | "cerulean"
  | "forest"
  | "rose"
  | "mango"
  | "purple"
  | "blue"
  | "teal"
  | "magenta"
  | "tan"
  | "green"
  | "brown"
  | "yellow";
export type ProxyPath = string | null;
export type ThumbnailPath = string | null;
export type WaveformPath = string | null;
/**
 * source monitor In mark, ticks
 */
export type InPoint = number | null;
/**
 * source monitor Out mark, ticks
 */
export type OutPoint = number | null;
export type Id3 = string;
export type Name3 = string;
export type BinId1 = string;
export type Width1 = number;
export type Height1 = number;
export type FpsNum1 = number;
export type FpsDen1 = number;
export type SampleRate1 = number;
export type Id4 = string;
export type Kind1 = "video" | "audio";
export type Name4 = string;
export type Locked = boolean;
export type SyncLock = boolean;
/**
 * video: eye toggle off
 */
export type Hidden = boolean;
export type Muted = boolean;
export type Solo = boolean;
export type Targeted = boolean;
export type Id5 = string;
/**
 * null for graphics
 */
export type MediaId = string | null;
export type Name5 = string;
/**
 * timeline start, ticks (inclusive)
 */
export type Start = number;
/**
 * timeline end, ticks (exclusive)
 */
export type End = number;
/**
 * source time at `start`, ticks
 */
export type SourceIn = number;
export type Speed = number;
export type Enabled = boolean;
/**
 * clips sharing a link_id move/trim together
 */
export type LinkId = string | null;
export type Label1 =
  | "violet"
  | "iris"
  | "caribbean"
  | "lavender"
  | "cerulean"
  | "forest"
  | "rose"
  | "mango"
  | "purple"
  | "blue"
  | "teal"
  | "magenta"
  | "tan"
  | "green"
  | "brown"
  | "yellow";
export type Opacity = number;
export type VolumeDb = number;
/**
 * sequence pixels [x, y]; null = frame center
 */
export type Position = number[] | null;
export type Scale = number;
/**
 * used when uniform is false
 */
export type ScaleWidth = number;
export type Uniform = boolean;
/**
 * degrees, clockwise
 */
export type Rotation = number;
/**
 * source pixels [x, y]; null = source center
 */
export type Anchor = number[] | null;
export type CropLeft = number;
export type CropTop = number;
export type CropRight = number;
export type CropBottom = number;
/**
 * source time in ticks (Premiere convention: keyframes follow the content when trimmed)
 */
export type T = number;
export type V = number | number[];
export type Interp = "linear" | "ease" | "hold";
export type Id6 = string;
export type Type = "text";
export type Text = string;
export type FontFamily = string;
export type FontSize = number;
export type Bold = boolean;
export type Italic = boolean;
export type Color = string;
export type StrokeColor = string | null;
export type StrokeWidth = number;
export type Background = string | null;
export type Align = "left" | "center" | "right";
/**
 * sequence pixels; the text block's alignment point (vertical center)
 */
export type Position1 = number[];
export type Scale1 = number;
export type Rotation1 = number;
export type Opacity1 = number;
/**
 * imported with best-effort styling (e.g. from a MOGRT)
 */
export type Approximate = boolean;
export type Layers = TextLayer[];
/**
 * origin, e.g. 'mogrt:Title' for imported Premiere graphics
 */
export type Source = string | null;
/**
 * bottom (V1) to top
 */
export type VideoTracks = string[];
/**
 * top (A1) to bottom
 */
export type AudioTracks = string[];
export type Id7 = string;
export type Start1 = number;
export type Duration1 = number;
export type Name6 = string;
export type Comment = string;
export type Color1 = "green" | "red" | "purple" | "orange" | "yellow" | "white" | "blue" | "cyan";
export type Playhead = number;
export type InPoint1 = number | null;
export type OutPoint1 = number | null;
export type ActiveSequenceId = string | null;

export interface Project {
  schema_version: SchemaVersion;
  id: Id;
  name: Name;
  root_bin_id: RootBinId;
  bins: Bins;
  media: Media;
  sequences: Sequences;
  active_sequence_id: ActiveSequenceId;
}
export interface Bins {
  [k: string]: Bin;
}
/**
 * This interface was referenced by `Project`'s JSON-Schema
 * via the `definition` "Bin".
 */
export interface Bin {
  id: Id1;
  name: Name1;
  parent_id: ParentId;
}
export interface Media {
  [k: string]: MediaItem;
}
/**
 * This interface was referenced by `Project`'s JSON-Schema
 * via the `definition` "MediaItem".
 */
export interface MediaItem {
  id: Id2;
  name: Name2;
  path: Path;
  bin_id: BinId;
  kind: Kind;
  duration: Duration;
  video: VideoStream | null;
  audio: AudioStream | null;
  label: Label;
  proxy_path: ProxyPath;
  thumbnail_path: ThumbnailPath;
  waveform_path: WaveformPath;
  in_point: InPoint;
  out_point: OutPoint;
}
/**
 * This interface was referenced by `Project`'s JSON-Schema
 * via the `definition` "VideoStream".
 */
export interface VideoStream {
  width: Width;
  height: Height;
  fps_num: FpsNum;
  fps_den: FpsDen;
  codec: Codec;
  pix_fmt: PixFmt;
}
/**
 * This interface was referenced by `Project`'s JSON-Schema
 * via the `definition` "AudioStream".
 */
export interface AudioStream {
  channels: Channels;
  sample_rate: SampleRate;
  codec: Codec1;
}
export interface Sequences {
  [k: string]: Sequence;
}
/**
 * This interface was referenced by `Project`'s JSON-Schema
 * via the `definition` "Sequence".
 */
export interface Sequence {
  id: Id3;
  name: Name3;
  bin_id: BinId1;
  settings: SequenceSettings;
  tracks: Tracks;
  video_tracks: VideoTracks;
  audio_tracks: AudioTracks;
  markers: Markers;
  playhead: Playhead;
  in_point: InPoint1;
  out_point: OutPoint1;
}
/**
 * This interface was referenced by `Project`'s JSON-Schema
 * via the `definition` "SequenceSettings".
 */
export interface SequenceSettings {
  width: Width1;
  height: Height1;
  fps_num: FpsNum1;
  fps_den: FpsDen1;
  sample_rate: SampleRate1;
}
export interface Tracks {
  [k: string]: Track;
}
/**
 * This interface was referenced by `Project`'s JSON-Schema
 * via the `definition` "Track".
 */
export interface Track {
  id: Id4;
  kind: Kind1;
  name: Name4;
  locked: Locked;
  sync_lock: SyncLock;
  hidden: Hidden;
  muted: Muted;
  solo: Solo;
  targeted: Targeted;
  clips: Clips;
}
export interface Clips {
  [k: string]: Clip;
}
/**
 * This interface was referenced by `Project`'s JSON-Schema
 * via the `definition` "Clip".
 */
export interface Clip {
  id: Id5;
  media_id: MediaId;
  name: Name5;
  start: Start;
  end: End;
  source_in: SourceIn;
  speed: Speed;
  enabled: Enabled;
  link_id: LinkId;
  label: Label1;
  opacity: Opacity;
  volume_db: VolumeDb;
  motion: Motion;
  keyframes: Keyframes;
  graphic: Graphic | null;
}
/**
 * Premiere's intrinsic Motion effect.
 *
 * This interface was referenced by `Project`'s JSON-Schema
 * via the `definition` "Motion".
 */
export interface Motion {
  position: Position;
  scale: Scale;
  scale_width: ScaleWidth;
  uniform: Uniform;
  rotation: Rotation;
  anchor: Anchor;
  crop_left: CropLeft;
  crop_top: CropTop;
  crop_right: CropRight;
  crop_bottom: CropBottom;
}
export interface Keyframes {
  [k: string]: Keyframe[];
}
/**
 * This interface was referenced by `Project`'s JSON-Schema
 * via the `definition` "Keyframe".
 */
export interface Keyframe {
  t: T;
  v: V;
  interp: Interp;
}
/**
 * This interface was referenced by `Project`'s JSON-Schema
 * via the `definition` "Graphic".
 */
export interface Graphic {
  layers: Layers;
  source: Source;
}
/**
 * This interface was referenced by `Project`'s JSON-Schema
 * via the `definition` "TextLayer".
 */
export interface TextLayer {
  id: Id6;
  type: Type;
  text: Text;
  font_family: FontFamily;
  font_size: FontSize;
  bold: Bold;
  italic: Italic;
  color: Color;
  stroke_color: StrokeColor;
  stroke_width: StrokeWidth;
  background: Background;
  align: Align;
  position: Position1;
  scale: Scale1;
  rotation: Rotation1;
  opacity: Opacity1;
  approximate: Approximate;
}
export interface Markers {
  [k: string]: Marker;
}
/**
 * This interface was referenced by `Project`'s JSON-Schema
 * via the `definition` "Marker".
 */
export interface Marker {
  id: Id7;
  start: Start1;
  duration: Duration1;
  name: Name6;
  comment: Comment;
  color: Color1;
}
