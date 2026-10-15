"""Read Premiere Pro effect presets (``Effect Presets and Custom Items.prfpset``
in the Premiere profile folder, or a single exported ``.prfpset``).

The file is the same XML object graph as a .prproj: a bin tree
(``BinTreeItem`` / ``TreeItem``) whose items hold ``FilterPreset``\\ s, each
with one effect component (params and keyframes exactly like in a project),
the preset type and the in/out points of the clip it was saved from.
Points are kept normalized (0-1 of the clip frame) so a preset applies to
clips of any size; keyframe times stay Premiere source ticks relative to the
saved anchors. Effects Lumina does not render are listed in ``skipped``.
The file is only read.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from pathlib import Path

from .prproj import EFFECT_PARAMS, Graph, PrprojError, _effect, _int, _text, load

# FilterPreset/Type: how keyframes are fitted to the target clip
PRESET_TYPES = {"0": "scale", "1": "anchor_in", "2": "anchor_out"}


def default_paths() -> list[Path]:
    """Premiere profile preset files, newest version first."""
    homes = [Path.home() / "Documents", Path.home() / "OneDrive" / "문서", Path.home() / "OneDrive" / "Documents"]
    if os.environ.get("USERPROFILE"):
        homes.append(Path(os.environ["USERPROFILE"]) / "Documents")
    found = []
    for h in homes:
        base = h / "Adobe" / "Premiere Pro"
        if base.is_dir():
            found += base.glob("*/Profile-*/Effect Presets and Custom Items.prfpset")

    def version(p: Path) -> tuple:
        try:
            return tuple(int(x) for x in p.parent.parent.name.split("."))
        except ValueError:
            return (0,)
    return sorted(set(found), key=version, reverse=True)


def read_presets(path: str) -> dict:
    """-> {"presets": [{id, name, folder, type, anchor_in, anchor_out, speed, effects}], "skipped": [...]}"""
    g = load(path)
    presets: list[dict] = []
    skipped: list[str] = []

    def preset_of(node: ET.Element, name: str, folder: list[str]) -> None:
        data = g.at(node, "TreeItemBase/Data")
        if data is None or data.tag != "FilterPresetItem":
            return
        uid = _text(node, "TreeItemBase/Node/Properties/MZ.EffectPresets.PresetUID") or node.get("ObjectID")
        effects, kind, a_in, a_out, speed = [], "anchor_in", 0, 0, 1.0
        for ref in data.findall("FilterPresets/FilterPreset"):
            fp = g.ref(ref)
            comp = g.at(fp, "Component")
            match = _text(fp, "FilterMatchName")
            if comp is None or match not in EFFECT_PARAMS:
                skipped.append(f"{'/'.join(folder + [name])}: {_text(comp, 'Component/DisplayName') if comp is not None else match}")
                continue
            kind = PRESET_TYPES.get(_text(fp, "Type") or "1", "anchor_in")
            a_in, a_out = _int(fp, "AnchorInPoint"), _int(fp, "AnchorOutPoint")
            try:
                speed = float(_text(fp, "Speed") or 1.0) or 1.0
            except ValueError:
                speed = 1.0
            fx = _effect(g, comp, EFFECT_PARAMS[match], 1.0, 1.0)  # points stay normalized
            fx["enabled"] = True
            effects.append(fx)
        if effects:
            presets.append({"id": uid, "name": name, "folder": folder, "type": kind, "anchor_in": a_in,
                            "anchor_out": a_out, "speed": speed, "effects": effects})

    def walk(bin_el: ET.Element, folder: list[str]) -> None:
        for ref in bin_el.findall("Items/Item"):
            node = g.ref(ref)
            if node is None:
                continue
            name = _text(node, "TreeItemBase/Name") or "Preset"
            if node.tag == "BinTreeItem":
                walk(node, folder + [name])
            else:
                preset_of(node, name, folder)

    tree = g.ref(g.root.find("Tree"))
    root = g.at(tree, "RootBin") if tree is not None else None
    if root is None:
        raise PrprojError("not a Premiere preset file (no bin tree)")
    walk(root, [])
    # the top "Presets" bin is Premiere's own folder; Lumina shows presets under its Presets folder
    for p in presets:
        if p["folder"] and _is_presets_bin(g, root, p["folder"][0]):
            p["folder"] = p["folder"][1:]
    return {"presets": presets, "skipped": skipped}


def _is_presets_bin(g: Graph, root: ET.Element, name: str) -> bool:
    for ref in root.findall("Items/Item"):
        node = g.ref(ref)
        if node is not None and _text(node, "TreeItemBase/Name") == name:
            return _text(node, "TreeItemBase/Node/Properties/HandlerEffects.EffectItemTree.PresetsBin") == "1"
    return False
