"""Premiere .prproj import, against a minimal synthetic project that mirrors
the object-graph shapes seen in real Premiere 2025-26 files."""

import gzip

import pytest

from lumina.commands.timeline import check_sequence
from lumina.io.prproj import extract
from lumina.model import validate_project
from lumina.session import Session
from lumina.timebase import TICKS_PER_SECOND as SEC

from .test_session import FakeServices

FRAME30 = SEC // 30


def build_prproj(tmp_path, media_path: str) -> str:
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
  <VideoClip ObjectID="10" ClassID="x" Version="11"><Clip Version="18"><Source ObjectRef="11" /></Clip></VideoClip>
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
    <ClipItems Version="3"><TrackItems Version="1"><TrackItem Index="0" ObjectRef="61" /></TrackItems></ClipItems></ClipTrack></AudioClipTrack>

  <VideoClipTrackItem ObjectID="60" ClassID="x" Version="8"><ClipTrackItem Version="8">
    <TrackItem Version="4"><Start>{SEC}</Start><End>{5 * SEC}</End></TrackItem><SubClip ObjectRef="70" /></ClipTrackItem></VideoClipTrackItem>
  <SubClip ObjectID="70" ClassID="x" Version="6"><Clip ObjectRef="71" /><Name>interview cut</Name></SubClip>
  <VideoClip ObjectID="71" ClassID="x" Version="11"><Clip Version="18">
    <Node Version="1"><Properties Version="1"><asl.clip.label.name>BE.Prefs.LabelColors.7</asl.clip.label.name></Properties></Node>
    <Source ObjectRef="11" /><InPoint>{10 * SEC}</InPoint><OutPoint>{14 * SEC}</OutPoint><PlaybackSpeed>1</PlaybackSpeed></Clip></VideoClip>
  <AudioClipTrackItem ObjectID="61" ClassID="x" Version="11"><ClipTrackItem Version="8">
    <TrackItem Version="4"><Start>{SEC}</Start><End>{5 * SEC}</End></TrackItem><SubClip ObjectRef="72" /></ClipTrackItem></AudioClipTrackItem>
  <SubClip ObjectID="72" ClassID="x" Version="6"><Clip ObjectRef="73" /><Name>interview cut</Name></SubClip>
  <AudioClip ObjectID="73" ClassID="x" Version="11"><Clip Version="18"><Source ObjectRef="11" /><InPoint>{10 * SEC}</InPoint></Clip></AudioClip>
  <VideoClipTrackItem ObjectID="62" ClassID="x" Version="8"><ClipTrackItem Version="8">
    <TrackItem Version="4"><End>{2 * SEC}</End></TrackItem><SubClip ObjectRef="74" /></ClipTrackItem></VideoClipTrackItem>
  <SubClip ObjectID="74" ClassID="x" Version="6"><Clip ObjectRef="20" /><Name>Title</Name></SubClip>
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


def test_rejects_non_premiere_files(tmp_path):
    bad = tmp_path / "x.prproj"
    bad.write_bytes(gzip.compress(b"<NotPremiere/>"))
    s = Session(services=FakeServices())
    from lumina.commands.registry import CommandError
    with pytest.raises(CommandError) as e:
        s.execute("project.import_premiere", {"path": str(bad)})
    assert e.value.code == "BAD_PRPROJ"
