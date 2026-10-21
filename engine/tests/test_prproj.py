"""Premiere .prproj import, against a minimal synthetic project that mirrors
the object-graph shapes seen in real Premiere 2025-26 files."""

import base64
import gzip
import struct

import pytest

from lumina.commands.timeline import check_sequence
from lumina.io.prproj import extract
from lumina.model import validate_project
from lumina.session import Session
from lumina.timebase import TICKS_PER_SECOND as SEC

from .test_session import FakeServices

FRAME30 = SEC // 30


def _shape_blobs(fill: tuple[int, int, int], kind: int) -> tuple[str, str]:
    """Path + style blobs laid out like Premiere's (see lumina.io.prproj._shape)."""
    rect = [(-50.0, -20.0), (50.0, -20.0), (50.0, 20.0), (-50.0, 20.0)]
    path = struct.pack("<II", 2, 4) + b"".join(struct.pack("<I6f", 0, x, y, x, y, x, y) for x, y in rect) + b"\x01"
    # FlatBuffer: root uoffset | root vtable, table -> style vtable, table -> color vtable, table
    # (uoffsets point forward; each vtable sits just before its table)
    vt = [0] * 24
    vt[0], vt[23] = 4, 8
    root_vt = 4
    root_t = root_vt + 6
    style_vt = root_t + 8
    style_t = style_vt + 4 + 2 * len(vt)
    color_vt = style_t + 12
    color_t = color_vt + 10
    fb = struct.pack("<I", root_t)
    fb += struct.pack("<HHH", 6, 8, 4)
    fb += struct.pack("<iI", root_t - root_vt, style_t - (root_t + 4))
    fb += struct.pack("<HH", 4 + 2 * len(vt), 12) + struct.pack(f"<{len(vt)}H", *vt)
    fb += struct.pack("<iII", style_t - style_vt, color_t - (style_t + 4), kind)
    fb += struct.pack("<HHHHH", 10, 7, 4, 5, 6)
    fb += struct.pack("<i", color_t - color_vt) + bytes(fill) + b"\0"
    assert len(fb) == color_t + 8
    style = struct.pack("<II", len(fb), 0) + struct.pack("<I", 0x11223344) + bytes(fb)
    return base64.b64encode(path).decode(), base64.b64encode(style).decode()


def _shapes_xml(with_shapes: bool) -> str:
    if not with_shapes:
        return ""
    path, style = _shape_blobs((255, 0, 0), 1)
    def param(oid, pid, value):
        return f'<VideoComponentParam ObjectID="{oid}" ClassID="x" Version="1"><ParameterID>{pid}</ParameterID><StartKeyframe>-91445760000000000,{value},0,0,0,0,0,0</StartKeyframe></VideoComponentParam>'
    # two identical shapes: Premiere stores the second copy's blobs empty, deduplicated by BinaryHash
    comps = []
    for n, (oid, pos) in enumerate([(300, "0.5:0.25"), (400, "0.5:0.75")]):
        blob = lambda b: b if n == 0 else ""
        comps.append(f'''<VideoFilterComponent ObjectID="{oid}" ClassID="x" Version="9"><Component Version="7"><Params Version="1">
  <Param Index="0" ObjectRef="{oid + 1}" /><Param Index="1" ObjectRef="{oid + 2}" /><Param Index="2" ObjectRef="{oid + 4}" /></Params>{"<Bypass>true</Bypass>" if n else ""}</Component>
  <MatchName>AE.ADBE Shape</MatchName></VideoFilterComponent>
<ArbVideoComponentParam ObjectID="{oid + 1}" ClassID="x" Version="3"><ParameterID>1</ParameterID><StartKeyframeValue Encoding="base64" BinaryHash="h-path">{blob(path)}</StartKeyframeValue></ArbVideoComponentParam>
<ArbVideoComponentParam ObjectID="{oid + 2}" ClassID="x" Version="3"><ParameterID>2</ParameterID><StartKeyframeValue Encoding="base64" BinaryHash="h-style">{blob(style)}</StartKeyframeValue></ArbVideoComponentParam>
<PointComponentParam ObjectID="{oid + 4}" ClassID="x" Version="1"><ParameterID>4</ParameterID><StartKeyframe>-91445760000000000,{pos},0,0,0,0,0,0,5,4,0,0,0,0</StartKeyframe></PointComponentParam>''')
    return "\n".join(comps) + '''
<VideoComponentChain ObjectID="290" ClassID="x" Version="3"><ComponentChain Version="3"><Components Version="1">
  <Component Index="0" ObjectRef="300" /><Component Index="1" ObjectRef="400" /></Components></ComponentChain></VideoComponentChain>'''


def _levels_xml() -> str:
    """Clip Volume -3 dB, Audio Gain -6 dB and a -6 dB track fader, stored as amplitude like Premiere does."""
    def fader(oid, amp):
        return f'''<AudioComponentChain ObjectID="{oid}" ClassID="x" Version="4"><ComponentChain Version="3"><Components Version="1">
  <Component Index="0" ObjectRef="{oid + 1}" /></Components></ComponentChain></AudioComponentChain>
<AudioFader ObjectID="{oid + 1}" ClassID="x" Version="3"><AudioComponent Version="3"><Component Version="7"><Params Version="1">
  <Param Index="0" ObjectRef="{oid + 2}" /></Params><ID>1</ID></Component></AudioComponent></AudioFader>
<AudioComponentParam ObjectID="{oid + 2}" ClassID="x" Version="10"><StartKeyframe>-91445760000000000,{amp},0,0,0,0,0,0</StartKeyframe><Name>Volume</Name></AudioComponentParam>'''
    return fader(500, 0.707945764065) + "\n" + fader(510, 0.501187233627)


def build_prproj(tmp_path, media_path: str, with_shapes: bool = False, with_levels: bool = False, with_clip_markers: bool = False) -> str:
    shapes_xml = _shapes_xml(with_shapes) + (_levels_xml() if with_levels else "")
    # a clip marker on the master clip (Source monitor M), 3 s into the footage
    clip_marker_owner = '<MarkerOwner Version="1"><Markers ObjectRef="520" /></MarkerOwner>' if with_clip_markers else ""
    if with_clip_markers:
        shapes_xml += f'''
  <Markers ObjectID="520" ClassID="x" Version="4"><Markers Version="1">
    <Marker Version="1" Index="0"><First>cm1</First><Second ObjectRef="521" /></Marker></Markers></Markers>
  <Marker ObjectID="521" ClassID="x" Version="3"><DVAMarker>{{"DVAMarker":{{"mStartTime":{{"ticks":{3 * SEC}}},"mName":"고점","mType":"Comment","mColorIndex":1}}}}</DVAMarker></Marker>'''
    clip_fader = '<ComponentOwner Version="1"><Components ObjectRef="500" /></ComponentOwner>' if with_levels else ""
    track_fader = '<AudioTrack Version="12"><ComponentOwner Version="1"><Components ObjectRef="510" /></ComponentOwner></AudioTrack>' if with_levels else ""
    gain = "<Gain>0.501187233627</Gain>" if with_levels else ""
    owner = '<ComponentOwner Version="1"><Components ObjectRef="290" /></ComponentOwner>' if with_shapes else ""
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<PremiereData Version="3">
  <RootProjectItem ObjectUID="root" ClassID="x" Version="1">
    <ProjectItem Version="1"><Name>Root</Name></ProjectItem>
    <ProjectItemContainer Version="1"><Items Version="1">
      <Item Index="0" ObjectURef="bin1" />
    </Items></ProjectItemContainer>
  </RootProjectItem>
  <BinProjectItem ObjectUID="bin1" ClassID="x" Version="1">
    <ProjectItem Version="1"><Name>Footage</Name></ProjectItem>
    <ProjectItemContainer Version="1"><Items Version="1">
      <Item Index="0" ObjectURef="cpi-media" />
      <Item Index="1" ObjectURef="cpi-seq" />
      <Item Index="2" ObjectURef="cpi-graphic" />
    </Items></ProjectItemContainer>
  </BinProjectItem>
  <ClipProjectItem ObjectUID="cpi-media" ClassID="x" Version="1">
    <ProjectItem Version="1"><Node Version="1"><Properties Version="1">
      <Column.PropertyText.Label>BE.Prefs.LabelColors.5</Column.PropertyText.Label></Properties></Node>
      <Name>interview.mp4</Name></ProjectItem>
    <MasterClip ObjectURef="mc-media" />
  </ClipProjectItem>
  <MasterClip ObjectUID="mc-media" ClassID="x" Version="12"><Clips Version="1"><Clip Index="0" ObjectRef="10" /></Clips></MasterClip>
  <VideoClip ObjectID="10" ClassID="x" Version="11"><Clip Version="18">{clip_marker_owner}<Source ObjectRef="11" /></Clip></VideoClip>
  <VideoMediaSource ObjectID="11" ClassID="x" Version="2"><MediaSource Version="4"><Media ObjectURef="media1" /></MediaSource></VideoMediaSource>
  <Media ObjectUID="media1" ClassID="x" Version="30">
    <VideoStream ObjectRef="12" /><AudioStream ObjectRef="13" />
    <FilePath>{media_path}</FilePath><Title>interview.mp4</Title>
  </Media>
  <VideoStream ObjectID="12" ClassID="x" Version="23"><FrameRate>{FRAME30}</FrameRate><Duration>{20 * SEC}</Duration><FrameRect>0,0,1920,1080</FrameRect></VideoStream>
  <AudioStream ObjectID="13" ClassID="x" Version="1"><Duration>{20 * SEC}</Duration></AudioStream>

  <ClipProjectItem ObjectUID="cpi-graphic" ClassID="x" Version="1">
    <ProjectItem Version="1"><Name>Title</Name></ProjectItem><MasterClip ObjectURef="mc-graphic" />
  </ClipProjectItem>
  <MasterClip ObjectUID="mc-graphic" ClassID="x" Version="12"><Clips Version="1"><Clip Index="0" ObjectRef="20" /></Clips></MasterClip>
  <VideoClip ObjectID="20" ClassID="x" Version="11"><Clip Version="18"><Source ObjectRef="21" /></Clip></VideoClip>
  <VideoMediaSource ObjectID="21" ClassID="x" Version="2"><MediaSource Version="4"><Media ObjectURef="media-graphic" /></MediaSource></VideoMediaSource>
  <Media ObjectUID="media-graphic" ClassID="x" Version="30"><FilePath>Media/263229dc/Title.aegraphic</FilePath></Media>

  <ClipProjectItem ObjectUID="cpi-seq" ClassID="x" Version="1">
    <ProjectItem Version="1"><Name>Main Edit</Name></ProjectItem><MasterClip ObjectURef="mc-seq" />
  </ClipProjectItem>
  <MasterClip ObjectUID="mc-seq" ClassID="x" Version="12"><Clips Version="1"><Clip Index="0" ObjectRef="30" /></Clips></MasterClip>
  <VideoClip ObjectID="30" ClassID="x" Version="11"><Clip Version="18">
    <MarkerOwner Version="1"><Markers ObjectRef="31" /></MarkerOwner><Source ObjectRef="32" /></Clip></VideoClip>
  <Markers ObjectID="31" ClassID="x" Version="4"><Markers Version="1">
    <Marker Version="1" Index="0"><First>m1</First><Second ObjectRef="33" /></Marker></Markers></Markers>
  <Marker ObjectID="33" ClassID="x" Version="3"><DVAMarker>{{"DVAMarker":{{"mStartTime":{{"ticks":{2 * SEC}}},"mName":"Hook","mType":"Comment"}}}}</DVAMarker></Marker>
  <VideoSequenceSource ObjectID="32" ClassID="x" Version="3"><SequenceSource Version="4"><Sequence ObjectURef="seq1" /></SequenceSource></VideoSequenceSource>

  <Sequence ObjectUID="seq1" ClassID="x" Version="12">
    <Node Version="1"><Properties Version="1"><MZ.EditLine>{3 * SEC}</MZ.EditLine></Properties></Node>
    <PersistentGroupContainer Version="1"><LinkContainer Version="1"><Links Version="1"><Link Index="0" ObjectRef="40" /></Links></LinkContainer></PersistentGroupContainer>
    <TrackGroups Version="1">
      <TrackGroup Version="1" Index="0"><First>v</First><Second ObjectRef="50" /></TrackGroup>
      <TrackGroup Version="1" Index="1"><First>a</First><Second ObjectRef="51" /></TrackGroup>
    </TrackGroups>
    <Name>SEQ</Name>
  </Sequence>
  <Link ObjectID="40" ClassID="x" Version="1"><TrackItemGroup Version="1"><TrackItems Version="1">
    <TrackItem Index="0" ObjectRef="60" /><TrackItem Index="1" ObjectRef="61" /></TrackItems></TrackItemGroup></Link>
  <VideoTrackGroup ObjectID="50" ClassID="x" Version="13">
    <TrackGroup Version="1"><Tracks Version="1"><Track Index="0" ObjectURef="vt1" /><Track Index="1" ObjectURef="vt2" /></Tracks>
    <FrameRate>{FRAME30}</FrameRate></TrackGroup><FrameRect>0,0,1080,1920</FrameRect>
  </VideoTrackGroup>
  <AudioTrackGroup ObjectID="51" ClassID="x" Version="1">
    <TrackGroup Version="1"><Tracks Version="1"><Track Index="0" ObjectURef="at1" /></Tracks><FrameRate>5292000</FrameRate></TrackGroup>
  </AudioTrackGroup>
  <VideoClipTrack ObjectUID="vt1" ClassID="x" Version="1"><ClipTrack Version="2"><Track Version="4"><Index>0</Index></Track>
    <ClipItems Version="3"><TrackItems Version="1"><TrackItem Index="0" ObjectRef="60" /></TrackItems></ClipItems></ClipTrack></VideoClipTrack>
  <VideoClipTrack ObjectUID="vt2" ClassID="x" Version="1"><ClipTrack Version="2"><Track Version="4"><Index>1</Index></Track>
    <ClipItems Version="3"><TrackItems Version="1"><TrackItem Index="0" ObjectRef="62" /></TrackItems></ClipItems></ClipTrack></VideoClipTrack>
  <AudioClipTrack ObjectUID="at1" ClassID="x" Version="1"><ClipTrack Version="2"><Track Version="4"><IsMuted>true</IsMuted><Index>0</Index></Track>
    <ClipItems Version="3"><TrackItems Version="1"><TrackItem Index="0" ObjectRef="61" /></TrackItems></ClipItems></ClipTrack>{track_fader}</AudioClipTrack>

  <VideoClipTrackItem ObjectID="60" ClassID="x" Version="8"><ClipTrackItem Version="8">
    <TrackItem Version="4"><Start>{SEC}</Start><End>{5 * SEC}</End></TrackItem><SubClip ObjectRef="70" /></ClipTrackItem></VideoClipTrackItem>
  <SubClip ObjectID="70" ClassID="x" Version="6"><Clip ObjectRef="71" /><Name>interview cut</Name></SubClip>
  <VideoClip ObjectID="71" ClassID="x" Version="11"><Clip Version="18">
    <Node Version="1"><Properties Version="1"><asl.clip.label.name>BE.Prefs.LabelColors.7</asl.clip.label.name></Properties></Node>
    <Source ObjectRef="11" /><InPoint>{10 * SEC}</InPoint><OutPoint>{14 * SEC}</OutPoint><PlaybackSpeed>1</PlaybackSpeed></Clip></VideoClip>
  <AudioClipTrackItem ObjectID="61" ClassID="x" Version="11"><ClipTrackItem Version="8">{clip_fader}
    <TrackItem Version="4"><Start>{SEC}</Start><End>{5 * SEC}</End></TrackItem><SubClip ObjectRef="72" /></ClipTrackItem></AudioClipTrackItem>
  <SubClip ObjectID="72" ClassID="x" Version="6"><Clip ObjectRef="73" /><Name>interview cut</Name></SubClip>
  <AudioClip ObjectID="73" ClassID="x" Version="11"><Clip Version="18"><Source ObjectRef="11" /><InPoint>{10 * SEC}</InPoint></Clip>{gain}</AudioClip>
  <VideoClipTrackItem ObjectID="62" ClassID="x" Version="8"><ClipTrackItem Version="8">
    <TrackItem Version="4"><End>{2 * SEC}</End></TrackItem>{owner}<SubClip ObjectRef="74" /></ClipTrackItem></VideoClipTrackItem>
  <SubClip ObjectID="74" ClassID="x" Version="6"><Clip ObjectRef="20" /><Name>Title</Name></SubClip>
{shapes_xml}
</PremiereData>"""
    path = tmp_path / "demo.prproj"
    path.write_bytes(gzip.compress(xml.encode("utf-8")))
    return str(path)


def test_extract_maps_the_object_graph(tmp_path):
    ex = extract(build_prproj(tmp_path, "C:\\media\\interview.mp4"))
    assert [b["name"] for b in ex.bins] == ["Footage"]
    assert len(ex.media) == 1 and next(iter(ex.media.values()))["label"] == "forest"
    (seq,) = ex.sequences
    assert seq["name"] == "Main Edit"
    assert seq["settings"] == {"width": 1080, "height": 1920, "fps_num": 30, "fps_den": 1, "sample_rate": 48000}
    v = seq["video_tracks"][0]["items"][0]
    assert (v["start"], v["end"], v["source_in"], v["label"]) == (SEC, 5 * SEC, 10 * SEC, "mango")
    assert v["link"] is not None and v["link"] == seq["audio_tracks"][0]["items"][0]["link"]
    assert seq["audio_tracks"][0]["muted"] is True
    assert seq["markers"] == [{"start": 2 * SEC, "duration": 0, "name": "Hook", "comment": "", "color": "green"}]
    assert seq["playhead"] == 3 * SEC
    assert any("graphics" in w for w in ex.warnings)


def test_import_command_is_valid_and_one_undo_step(tmp_path):
    media = tmp_path / "interview.mp4"
    media.write_bytes(b"")
    s = Session(services=FakeServices())
    out = s.execute("project.import_premiere", {"path": build_prproj(tmp_path, str(media))})["result"]
    doc = s.doc
    assert validate_project(doc).model_dump(mode="json") == doc
    seq = doc["sequences"][out["sequences"][0]]
    check_sequence(doc, seq)
    clips = [c for t in seq["tracks"].values() for c in t["clips"].values()]
    assert len(clips) == 2 and clips[0]["link_id"] == clips[1]["link_id"] is not None
    assert doc["bins"][out["bin_id"]]["name"] == "demo"
    s.undo()
    assert doc["media"] == {} and doc["sequences"] == {}


def test_audio_levels_come_across(tmp_path):
    media = tmp_path / "interview.mp4"
    media.write_bytes(b"")
    path = build_prproj(tmp_path, str(media), with_levels=True)
    a = extract(path).sequences[0]["audio_tracks"][0]
    assert (a["items"][0]["volume_db"], a["items"][0]["gain_db"], a["volume_db"]) == (-3.0, -6.0, -6.0)
    s = Session(services=FakeServices())
    out = s.execute("project.import_premiere", {"path": path})["result"]
    doc = s.doc
    assert validate_project(doc).model_dump(mode="json") == doc
    seq = doc["sequences"][out["sequences"][0]]
    track = seq["tracks"][seq["audio_tracks"][0]]
    (clip,) = track["clips"].values()
    assert (clip["volume_db"], clip["gain_db"], track["volume_db"]) == (-3.0, -6.0, -6.0)
    # untouched clips stay at 0 dB
    plain = extract(build_prproj(tmp_path, str(media))).sequences[0]["audio_tracks"][0]
    assert (plain["items"][0]["volume_db"], plain["items"][0]["gain_db"], plain["volume_db"]) == (0.0, 0.0, 0.0)


def test_clip_markers_of_the_footage_come_across(tmp_path):
    media = tmp_path / "interview.mp4"
    media.write_bytes(b"")
    path = build_prproj(tmp_path, str(media), with_clip_markers=True)
    (m,) = extract(path).media.values()
    assert m["markers"] == [{"start": 3 * SEC, "duration": 0, "name": "고점", "comment": "", "color": "red"}]
    s = Session(services=FakeServices())
    s.execute("project.import_premiere", {"path": path})
    doc = s.doc
    assert validate_project(doc).model_dump(mode="json") == doc
    (item,) = doc["media"].values()
    assert [(k["start"], k["name"]) for k in item["markers"].values()] == [(3 * SEC, "고점")]


def test_rejects_non_premiere_files(tmp_path):
    bad = tmp_path / "x.prproj"
    bad.write_bytes(gzip.compress(b"<NotPremiere/>"))
    s = Session(services=FakeServices())
    from lumina.commands.registry import CommandError
    with pytest.raises(CommandError) as e:
        s.execute("project.import_premiere", {"path": str(bad)})
    assert e.value.code == "BAD_PRPROJ"


def test_shape_layers_import_with_deduplicated_blobs(tmp_path):
    ex = extract(build_prproj(tmp_path, "C:\\media\\interview.mp4", with_shapes=True))
    (g,) = [c["graphic"] for c in ex.sequences[0]["video_tracks"][1]["items"]]
    assert [l["type"] for l in g["layers"]] == ["shape", "shape"]
    top, bottom = g["layers"][1], g["layers"][0]
    # Premiere lists components top-most first; Lumina stacks bottom first
    assert top["position"] == [540, 480] and bottom["position"] == [540, 1440]
    for l in g["layers"]:
        assert l["shape"] == "rectangle" and l["fill"] == "#ff0000" and l["closed"] is True
        assert l["path"][0][:2] == [-50, -20] and l["path"][2][:2] == [50, 20]
    assert not any("shape" in w for w in ex.warnings)
    # the second component (bottom of the stack) has its eye off: <Bypass>true</Bypass>
    assert top["visible"] is True and bottom["visible"] is False
