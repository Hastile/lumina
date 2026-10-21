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
 * timeline thumbnails: JSON {image, interval, count, width, height}
 */
export type FilmstripPath = string | null;
/**
 * source monitor In mark, ticks
 */
export type InPoint = number | null;
/**
 * source monitor Out mark, ticks
 */
export type OutPoint = number | null;
/**
 * Make Offline: shown as missing media even if the file exists
 */
export type Offline = boolean;
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
/**
 * audio: Audio Track Mixer fader
 */
export type VolumeDb = number;
/**
 * audio: track pan / balance, -100 left .. 100 right
 */
export type Pan = number;
export type Id5 = string;
/**
 * null for graphics and nested sequences
 */
export type MediaId = string | null;
/**
 * nested sequence (Premiere's Nest): the clip shows that sequence, and source_in is a time in it
 */
export type SequenceId = string | null;
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
export type VolumeDb1 = number;
/**
 * Audio Gain (Premiere's G dialog), applied before Volume
 */
export type GainDb = number;
/**
 * Essential Sound audio type
 */
export type AudioTag = ("dialogue" | "music" | "sfx" | "ambience") | null;
/**
 * Essential Sound Mute: the clip is silent (it stays enabled)
 */
export type Muted1 = boolean;
/**
 * LUFS it was matched to
 */
export type Target = number;
/**
 * loudness measured before the match
 */
export type Lufs = number;
/**
 * the Audio Gain Auto-Match set
 */
export type GainDb1 = number;
/**
 * the Audio Gain before it (Reset restores it)
 */
export type PreviousGainDb = number;
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
export type Interp = "linear" | "ease" | "hold" | "bezier" | "auto_bezier" | "continuous_bezier";
/**
 * [speed, influence] of the incoming Bezier handle
 */
export type EaseIn = number[] | null;
/**
 * [speed, influence] of the outgoing Bezier handle
 */
export type EaseOut = number[] | null;
/**
 * spatial in-tangent for point values
 */
export type TangentIn = number[] | null;
/**
 * spatial out-tangent for point values
 */
export type TangentOut = number[] | null;
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
 * sequence pixels: point text -> the block's alignment point at its vertical center; area text -> the box's top-left
 */
export type Position1 = number[];
/**
 * area text box [w, h]; null = point text
 */
export type Box = number[] | null;
/**
 * area text only
 */
export type VerticalAlign = "top" | "center" | "bottom";
/**
 * 1/1000 em added after each character
 */
export type Tracking = number;
export type Color1 = string;
/**
 * percent
 */
export type Opacity1 = number;
/**
 * degrees clockwise from up; 135 = down-right
 */
export type Angle = number;
export type Distance = number;
/**
 * CSS text-shadow blur radius (Gaussian sigma = blur / 2)
 */
export type Blur = number;
/**
 * styled spans; their texts concatenate to `text`
 */
export type Runs = TextRun[] | null;
export type Text1 = string;
export type FontFamily1 = string | null;
export type FontSize1 = number | null;
export type Bold1 = boolean | null;
export type Italic1 = boolean | null;
export type Color2 = string | null;
export type StrokeColor1 = string | null;
export type StrokeWidth1 = number | null;
export type Tracking1 = number | null;
export type Scale1 = number;
export type Rotation1 = number;
export type Opacity2 = number;
/**
 * the layer's eye toggle
 */
export type Visible = boolean;
/**
 * imported with best-effort styling (e.g. from a MOGRT)
 */
export type Approximate = boolean;
export type Id7 = string;
export type Type1 = "shape";
export type Shape = "rectangle" | "ellipse" | "path";
/**
 * vertices [x, y, in_x, in_y, out_x, out_y]; tangent handles are absolute points
 */
export type Path1 = number[][];
export type Closed = boolean;
/**
 * null = no fill
 */
export type Fill = string | null;
export type StrokeColor2 = string | null;
export type StrokeWidth2 = number;
/**
 * sequence pixels where the anchor lands
 */
export type Position2 = number[];
/**
 * layer pixels
 */
export type Anchor1 = number[];
export type Scale2 = number;
/**
 * used when uniform is false
 */
export type ScaleWidth1 = number;
export type Uniform1 = boolean;
export type Rotation2 = number;
export type Opacity3 = number;
/**
 * the layer's eye toggle
 */
export type Visible1 = boolean;
/**
 * blended with everything below in the sequence
 */
export type Blend = "normal" | "multiply";
export type Id8 = string;
export type Type2 = "image";
/**
 * image file (png, jpg, webp, ...)
 */
export type Path2 = string;
/**
 * the image's pixel size [w, h]
 */
export type Size = number[];
/**
 * sequence pixels where the anchor lands
 */
export type Position3 = number[];
/**
 * image pixels; null = centre
 */
export type Anchor2 = number[] | null;
export type Scale3 = number;
/**
 * used when uniform is false
 */
export type ScaleWidth2 = number;
export type Uniform2 = boolean;
export type Rotation3 = number;
export type Opacity4 = number;
/**
 * percent of the image hidden from the left (the picture does not move)
 */
export type CropLeft1 = number;
export type CropTop1 = number;
export type CropRight1 = number;
export type CropBottom1 = number;
/**
 * the layer's eye toggle
 */
export type Visible2 = boolean;
/**
 * blended with everything below in the sequence
 */
export type Blend1 = "normal" | "multiply";
/**
 * bottom to top
 */
export type Layers = (TextLayer | ShapeLayer | ImageLayer)[];
/**
 * origin, e.g. 'mogrt:Title' for imported Premiere graphics
 */
export type Source = string | null;
export type Text2 = string;
/**
 * per-cue overrides of CaptionStyle fields
 */
export type Style = {
  [k: string]: string | number | boolean | number[] | null;
} | null;
export type Id9 = string;
export type Type3 = "transform" | "gaussian_blur" | "lumetri" | "parametric_eq" | "compressor" | "hard_limiter";
export type Enabled1 = boolean;
/**
 * track effects: what keyframe times count from
 */
export type Anchor3 = ("in" | "out" | "stretch") | null;
/**
 * track effects, anchor stretch: the length keyframe times span
 */
export type RefDuration = number | null;
export type Effects = Effect[];
/**
 * Frame Hold: the source time shown for the whole clip (ticks)
 */
export type Freeze = number | null;
/**
 * Adjustment Layer: its effects apply to everything below it (Opacity mixes the result in; Motion and crop are not used)
 */
export type Adjustment = boolean;
export type Id10 = string;
export type Type4 = "cross_dissolve" | "dip_to_black" | "dip_to_white" | "constant_power";
/**
 * outgoing clip (ends at the cut)
 */
export type ClipA = string | null;
/**
 * incoming clip (starts at the cut)
 */
export type ClipB = string | null;
/**
 * ticks
 */
export type Duration1 = number;
/**
 * center at cut / start at cut (over clip_b) / end at cut (over clip_a)
 */
export type Alignment = "center" | "start" | "end";
/**
 * video: track effects, applied to every clip of the track
 */
export type Effects1 = Effect[];
export type FontFamily2 = string;
export type FontSize2 = number;
export type Bold2 = boolean;
export type Italic2 = boolean;
export type Color3 = string;
export type StrokeColor3 = string | null;
export type StrokeWidth3 = number;
export type Background1 = string | null;
export type Align1 = "left" | "center" | "right";
/**
 * sequence pixels; point text: the block's alignment point at its vertical center
 */
export type Position4 = number[];
/**
 * area text box [w, h]; null = point text
 */
export type Box1 = number[] | null;
export type VerticalAlign1 = "top" | "center" | "bottom";
export type Tracking2 = number;
export type Scale4 = number;
export type Rotation4 = number;
export type Opacity5 = number;
/**
 * bottom (V1) to top
 */
export type VideoTracks = string[];
/**
 * top (A1) to bottom
 */
export type AudioTracks = string[];
export type Id11 = string;
export type Start1 = number;
export type Duration2 = number;
export type Name6 = string;
export type Comment = string;
export type Color4 = "green" | "red" | "purple" | "orange" | "yellow" | "white" | "blue" | "cyan";
export type Playhead = number;
export type InPoint1 = number | null;
export type OutPoint1 = number | null;
/**
 * Audio Track Mixer's Mix fader
 */
export type MasterVolumeDb = number;
/**
 * source patching: the track source video goes to (Insert/Overwrite); null = the first targeted video track; '' = none
 */
export type SourceVideoTrack = string | null;
/**
 * source patching for audio; null = first targeted audio track; '' = none
 */
export type SourceAudioTrack = string | null;
export type Id12 = string;
export type Name7 = string;
export type BinId2 = string;
export type Kind2 = "captions" | "template" | "adjustment";
/**
 * template: the .mogrt / .aegraphic file; captions: the source .srt, if any
 */
export type Path3 = string | null;
/**
 * captions: the cues
 */
export type Cues = CaptionCue[] | null;
/**
 * ticks from the start of the captions
 */
export type Start2 = number;
export type End1 = number;
export type Text3 = string;
export type Label2 =
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
export type ActiveSequenceId = string | null;

export interface Project {
  schema_version: SchemaVersion;
  id: Id;
  name: Name;
  root_bin_id: RootBinId;
  bins: Bins;
  media: Media;
  sequences: Sequences;
  assets: Assets;
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
  filmstrip_path: FilmstripPath;
  in_point: InPoint;
  out_point: OutPoint;
  offline: Offline;
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
  master_volume_db: MasterVolumeDb;
  source_video_track: SourceVideoTrack;
  source_audio_track: SourceAudioTrack;
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
  volume_db: VolumeDb;
  pan: Pan;
  clips: Clips;
  transitions: Transitions;
  effects: Effects1;
  /**
   * set on a caption track (its cues are Clip.caption)
   */
  caption_style: CaptionStyle | null;
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
  sequence_id: SequenceId;
  name: Name5;
  start: Start;
  end: End;
  source_in: SourceIn;
  speed: Speed;
  enabled: Enabled;
  link_id: LinkId;
  label: Label1;
  opacity: Opacity;
  volume_db: VolumeDb1;
  gain_db: GainDb;
  audio_tag: AudioTag;
  muted: Muted1;
  /**
   * the last Loudness Auto-Match (for its status and Reset)
   */
  auto_match: AutoMatchState | null;
  motion: Motion;
  keyframes: Keyframes;
  graphic: Graphic | null;
  /**
   * a cue on a caption track
   */
  caption: Caption | null;
  effects: Effects;
  freeze: Freeze;
  adjustment: Adjustment;
}
/**
 * This interface was referenced by `Project`'s JSON-Schema
 * via the `definition` "AutoMatchState".
 */
export interface AutoMatchState {
  target: Target;
  lufs: Lufs;
  gain_db: GainDb1;
  previous_gain_db: PreviousGainDb;
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
 * Temporal interpolation follows After Effects/Premiere: each side of a
 * keyframe is linear, hold, or a Bezier handle given as [speed, influence]
 * (value units per second of source time, fraction 0-1 of the segment).
 * "ease" is a Bezier with Premiere's Easy Ease handles (speed 0, 33 %).
 * "auto_bezier" computes its handles from the neighbours (a smooth curve
 * through the keyframe); "continuous_bezier" keeps both sides at one speed.
 * Point values may also carry spatial tangents (value units, relative to
 * the keyframe) shaping the motion path.
 *
 * This interface was referenced by `Project`'s JSON-Schema
 * via the `definition` "Keyframe".
 */
export interface Keyframe {
  t: T;
  v: V;
  interp: Interp;
  ease_in: EaseIn;
  ease_out: EaseOut;
  tangent_in: TangentIn;
  tangent_out: TangentOut;
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
  box: Box;
  vertical_align: VerticalAlign;
  tracking: Tracking;
  shadow: TextShadow | null;
  runs: Runs;
  scale: Scale1;
  rotation: Rotation1;
  opacity: Opacity2;
  visible: Visible;
  approximate: Approximate;
}
/**
 * This interface was referenced by `Project`'s JSON-Schema
 * via the `definition` "TextShadow".
 */
export interface TextShadow {
  color: Color1;
  opacity: Opacity1;
  angle: Angle;
  distance: Distance;
  blur: Blur;
}
/**
 * A styled span of a text layer; unset fields inherit the layer's.
 *
 * This interface was referenced by `Project`'s JSON-Schema
 * via the `definition` "TextRun".
 */
export interface TextRun {
  text: Text1;
  font_family: FontFamily1;
  font_size: FontSize1;
  bold: Bold1;
  italic: Italic1;
  color: Color2;
  stroke_color: StrokeColor1;
  stroke_width: StrokeWidth1;
  tracking: Tracking1;
}
/**
 * A vector shape. Its path lives in layer pixels; ``anchor`` (layer pixels)
 * lands on ``position`` (sequence pixels), then scale and rotation apply
 * around it, like Premiere's per-layer transform.
 *
 * This interface was referenced by `Project`'s JSON-Schema
 * via the `definition` "ShapeLayer".
 */
export interface ShapeLayer {
  id: Id7;
  type: Type1;
  shape: Shape;
  path: Path1;
  closed: Closed;
  fill: Fill;
  stroke_color: StrokeColor2;
  stroke_width: StrokeWidth2;
  position: Position2;
  anchor: Anchor1;
  scale: Scale2;
  scale_width: ScaleWidth1;
  uniform: Uniform1;
  rotation: Rotation2;
  opacity: Opacity3;
  visible: Visible1;
  blend: Blend;
}
/**
 * A picture placed in a graphic (Premiere: New Layer > From File). ``anchor`` (image pixels,
 * null = the image's centre) lands on ``position`` (sequence pixels), then scale and rotation
 * apply around it. Unlike Premiere, each image layer has its own crop.
 *
 * This interface was referenced by `Project`'s JSON-Schema
 * via the `definition` "ImageLayer".
 */
export interface ImageLayer {
  id: Id8;
  type: Type2;
  path: Path2;
  size: Size;
  position: Position3;
  anchor: Anchor2;
  scale: Scale3;
  scale_width: ScaleWidth2;
  uniform: Uniform2;
  rotation: Rotation3;
  opacity: Opacity4;
  crop_left: CropLeft1;
  crop_top: CropTop1;
  crop_right: CropRight1;
  crop_bottom: CropBottom1;
  visible: Visible2;
  blend: Blend1;
}
/**
 * A caption cue: its text, drawn with the track's style (overrides apply to this cue only).
 *
 * This interface was referenced by `Project`'s JSON-Schema
 * via the `definition` "Caption".
 */
export interface Caption {
  text: Text2;
  style: Style;
}
/**
 * A standard effect, applied in order before the intrinsic Motion/Opacity
 * (Premiere's render order). Params and keyframes are keyed by the names in
 * ``lumina.effects.EFFECTS``; keyframe times are source ticks, like Motion's.
 *
 * On a track (Track.effects) an effect applies to every clip of the track, after
 * the clip's own effects and Motion, in sequence pixels; its keyframe times are
 * then relative to each clip: from its In point (anchor "in"), from its Out point
 * (anchor "out", negative), or 0..ref_duration stretched over the clip ("stretch").
 *
 * This interface was referenced by `Project`'s JSON-Schema
 * via the `definition` "Effect".
 */
export interface Effect {
  id: Id9;
  type: Type3;
  enabled: Enabled1;
  params: Params;
  keyframes: Keyframes1;
  anchor: Anchor3;
  ref_duration: RefDuration;
}
export interface Params {
  [k: string]: number | boolean | number[];
}
export interface Keyframes1 {
  [k: string]: Keyframe[];
}
export interface Transitions {
  [k: string]: Transition;
}
/**
 * A transition at an edit point. Its position follows the clips: the cut is
 * ``clip_a``'s end (or ``clip_b``'s start for a single-sided one).
 *
 * This interface was referenced by `Project`'s JSON-Schema
 * via the `definition` "Transition".
 */
export interface Transition {
  id: Id10;
  type: Type4;
  clip_a: ClipA;
  clip_b: ClipB;
  duration: Duration1;
  alignment: Alignment;
}
/**
 * A caption track's style (Premiere's track style): every cue on the track is
 * drawn as one text layer with these settings and the cue's text.
 *
 * This interface was referenced by `Project`'s JSON-Schema
 * via the `definition` "CaptionStyle".
 */
export interface CaptionStyle {
  font_family: FontFamily2;
  font_size: FontSize2;
  bold: Bold2;
  italic: Italic2;
  color: Color3;
  stroke_color: StrokeColor3;
  stroke_width: StrokeWidth3;
  background: Background1;
  align: Align1;
  position: Position4;
  box: Box1;
  vertical_align: VerticalAlign1;
  tracking: Tracking2;
  shadow: TextShadow | null;
  scale: Scale4;
  rotation: Rotation4;
  opacity: Opacity5;
}
export interface Markers {
  [k: string]: Marker;
}
/**
 * This interface was referenced by `Project`'s JSON-Schema
 * via the `definition` "Marker".
 */
export interface Marker {
  id: Id11;
  start: Start1;
  duration: Duration2;
  name: Name6;
  comment: Comment;
  color: Color4;
}
/**
 * captions, templates and adjustment layers
 */
export interface Assets {
  [k: string]: Asset;
}
/**
 * A project item that is not a media file: a captions file (its cues), a Motion
 * Graphics Template, or an Adjustment Layer. Placed on the timeline with asset.place.
 *
 * This interface was referenced by `Project`'s JSON-Schema
 * via the `definition` "Asset".
 */
export interface Asset {
  id: Id12;
  name: Name7;
  bin_id: BinId2;
  kind: Kind2;
  path: Path3;
  cues: Cues;
  label: Label2;
}
/**
 * This interface was referenced by `Project`'s JSON-Schema
 * via the `definition` "CaptionCue".
 */
export interface CaptionCue {
  start: Start2;
  end: End1;
  text: Text3;
}
