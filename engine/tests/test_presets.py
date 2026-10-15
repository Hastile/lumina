"""Premiere effect presets: reading a .prfpset and fitting presets to clips."""

import pytest

from lumina import presets
from lumina.io.prfpset import read_presets
from lumina.session import Session
from lumina.timebase import TICKS_PER_SECOND as SEC

from .test_session import FakeServices

BASE = 3600 * SEC  # Premiere graphics start their source time at one hour


def _preset_xml(oid: int, name: str, uid: str, ptype: int, a_in: int, a_out: int, keyframes: str) -> str:
    return f"""
  <TreeItem ObjectID="{oid}" ClassID="x" Version="3"><TreeItemBase Version="4"><Name>{name}</Name>
    <Node Version="1"><Properties Version="1"><MZ.EffectPresets.PresetUID>{uid}</MZ.EffectPresets.PresetUID></Properties></Node>
    <Data ObjectRef="{oid + 1}"/></TreeItemBase></TreeItem>
  <FilterPresetItem ObjectID="{oid + 1}" ClassID="x" Version="2"><FilterPresets Version="1"><FilterPreset Index="0" ObjectRef="{oid + 2}"/></FilterPresets></FilterPresetItem>
  <FilterPreset ObjectID="{oid + 2}" ClassID="x" Version="3"><Component ObjectRef="{oid + 3}"/><Type>{ptype}</Type>
    <FilterMatchName>AE.ADBE Geometry2</FilterMatchName><AnchorInPoint>{a_in}</AnchorInPoint><AnchorOutPoint>{a_out}</AnchorOutPoint><Speed>1.</Speed></FilterPreset>
  <VideoFilterComponent ObjectID="{oid + 3}" ClassID="x" Version="9"><Component Version="7"><DisplayName>Transform</DisplayName><Params Version="1">
    <Param Index="0" ObjectRef="{oid + 4}"/><Param Index="1" ObjectRef="{oid + 5}"/></Params></Component></VideoFilterComponent>
  <PointComponentParam ObjectID="{oid + 4}" ClassID="x" Version="1"><ParameterID>2</ParameterID>
    <StartKeyframe>-91445760000000000,0.5:0.75,0,0,0,0,0,0,5,4,0,0,0,0</StartKeyframe></PointComponentParam>
  <VideoComponentParam ObjectID="{oid + 5}" ClassID="x" Version="1"><ParameterID>3</ParameterID>
    <StartKeyframe>-91445760000000000,100.,0,0,0,0,0,0</StartKeyframe><Keyframes>{keyframes}</Keyframes></VideoComponentParam>"""


@pytest.fixture()
def preset_file(tmp_path):
    pop = f"{BASE},70.,5,0,0,0.25,60.,0.5;{BASE + SEC // 5},100.,5,0,-40.,1.,0,0.33;"
    grow = f"{BASE},100.,0,0,0,0.16,0,0.16;{BASE + 4 * SEC},120.,0,0,0,0.16,0,0.16;"
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<PremiereData Version="3">
  <Tree ObjectRef="1"/>
  <Tree ObjectID="1" ClassID="x" Version="1"><RootBin ObjectRef="2"/></Tree>
  <BinTreeItem ObjectID="2" ClassID="x" Version="4"><TreeItemBase Version="4"><Name>Root</Name></TreeItemBase>
    <Items Version="1"><Item Index="0" ObjectRef="3"/></Items></BinTreeItem>
  <BinTreeItem ObjectID="3" ClassID="x" Version="4"><TreeItemBase Version="4"><Name>사전 설정</Name>
    <Node Version="1"><Properties Version="1"><HandlerEffects.EffectItemTree.PresetsBin>1</HandlerEffects.EffectItemTree.PresetsBin></Properties></Node></TreeItemBase>
    <Items Version="1"><Item Index="0" ObjectRef="4"/><Item Index="1" ObjectRef="20"/></Items></BinTreeItem>
  <BinTreeItem ObjectID="4" ClassID="x" Version="4"><TreeItemBase Version="4"><Name>쇼츠</Name></TreeItemBase>
    <Items Version="1"><Item Index="0" ObjectRef="10"/></Items></BinTreeItem>
  {_preset_xml(10, "팝인", "uid-pop", 1, BASE, BASE + SEC, pop)}
  {_preset_xml(20, "스케일 업", "uid-grow", 0, BASE, BASE + 4 * SEC, grow)}
</PremiereData>"""
    p = tmp_path / "Effect Presets and Custom Items.prfpset"
    p.write_text(xml, encoding="utf-8")
    return str(p)


def test_reads_the_preset_tree(preset_file):
    got = read_presets(preset_file)
    assert got["skipped"] == []
    pop, grow = got["presets"]
    assert (pop["name"], pop["folder"], pop["type"]) == ("팝인", ["쇼츠"], "anchor_in"), "Premiere's own Presets bin is dropped"
    assert (grow["folder"], grow["type"]) == ([], "scale")
    fx = pop["effects"][0]
    assert fx["type"] == "transform" and fx["params"]["position"] == [0.5, 0.75], "points stay normalized"
    k0, k1 = fx["keyframes"]["scale"]
    assert k0["interp"] == "bezier" and k0["ease_out"] == [60.0, 0.5] and k1["ease_in"] == [-40.0, 1.0]


def test_fit_anchors_and_scales_keyframes(preset_file):
    pop, grow = read_presets(preset_file)["presets"]
    clip = {"source_in": 10 * SEC, "start": 0, "end": 8 * SEC, "speed": 2.0}
    (fx,) = presets.fit(pop, clip, 1080, 1920)
    assert fx["params"]["position"] == [540, 1440]
    k0, k1 = fx["keyframes"]["scale"]
    # anchored to the in point; at 2x speed the same timeline offset is twice the source time
    assert (k0["t"], k1["t"]) == (10 * SEC, 10 * SEC + 2 * SEC // 5)
    assert k0["ease_out"] == [30.0, 0.5], "handle speeds follow the time stretch"
    (fx,) = presets.fit(grow, clip, 1080, 1920)
    g0, g1 = fx["keyframes"]["scale"]
    assert (g0["t"], g1["t"]) == (10 * SEC, 10 * SEC + 16 * SEC), "scaled over the whole clip (8 s at 2x)"
    (fx,) = presets.fit({**pop, "type": "anchor_out"}, clip, 1080, 1920)
    out_src = 10 * SEC + 16 * SEC
    assert fx["keyframes"]["scale"][1]["t"] == out_src - 2 * (SEC - SEC // 5)


def test_import_and_apply_preset_command(preset_file, tmp_path, monkeypatch):
    monkeypatch.setenv("LUMINA_PRESETS", str(tmp_path / "presets.json"))
    r = presets.import_premiere(preset_file)
    assert r["imported"] == 2 and sorted(r["names"]) == sorted(["스케일 업", "쇼츠/팝인"])
    presets.import_premiere(preset_file)
    assert len(presets.load()) == 2, "re-import replaces by preset UID"
    s = Session(services=FakeServices())
    sid = s.execute("sequence.create", {"name": "s", "settings": {"width": 1080, "height": 1920, "fps_num": 30, "fps_den": 1}})["result"]["sequence_id"]
    g = s.execute("graphic.create_text", {"sequence_id": sid, "text": "자막", "at": "1s"})["result"]
    out = s.execute("effect.apply_preset", {"sequence_id": sid, "clip_ids": [g["clip_id"]], "preset_id": "uid-pop"})["result"]
    clip = s.doc["sequences"][sid]["tracks"][g["track_id"]]["clips"][g["clip_id"]]
    (fx,) = clip["effects"]
    assert fx["id"] == out["effect_ids"][0] and fx["params"]["position"] == [540, 1440]
    assert fx["params"]["skew"] == 0, "unsaved params take the effect's defaults"
    assert fx["keyframes"]["scale"][0]["t"] == clip["source_in"]
    s.undo()
    assert s.doc["sequences"][sid]["tracks"][g["track_id"]]["clips"][g["clip_id"]]["effects"] == []
