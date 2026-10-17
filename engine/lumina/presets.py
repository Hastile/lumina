"""Effect preset library (the Effects panel's Presets folder).

Presets live outside projects, in ``~/.lumina/presets.json`` (or
``$LUMINA_PRESETS``), like Premiere's profile file they are imported from.
A preset holds standard effects with normalized points and keyframes timed
relative to the clip it was saved from; :func:`fit` places them on a target
clip the way Premiere does:

* ``anchor_in``  - keep each keyframe's distance from the clip's start;
* ``anchor_out`` - keep each keyframe's distance from the clip's end;
* ``scale``      - stretch the saved in-out span over the target clip.

Offsets are timeline time, so a clip's speed is taken into account, and
Bezier handle speeds are rescaled with the time stretch.
"""

from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path

from .io.prproj import _point_keyframes
from .model import new_id

_lock = threading.Lock()


def library_path() -> Path:
    if os.environ.get("LUMINA_PRESETS"):
        return Path(os.environ["LUMINA_PRESETS"])
    return Path.home() / ".lumina" / "presets.json"


def load() -> list[dict]:
    p = library_path()
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("presets", [])
    except (OSError, ValueError):
        return []


def save(presets: list[dict]) -> None:
    p = library_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": 1, "presets": presets}, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def get(preset_id: str) -> dict | None:
    return next((p for p in load() if p["id"] == preset_id), None)


def import_premiere(path: str | None = None) -> dict:
    """Merge a Premiere preset file into the library (same preset UID -> replaced)."""
    from .io.prfpset import default_paths, read_presets

    if path is None:
        found = default_paths()
        if not found:
            raise FileNotFoundError("no Premiere Pro preset file found (Documents/Adobe/Premiere Pro/<version>/Profile-*/)")
        path = str(found[0])
    got = read_presets(path)
    with _lock:
        lib = {p["id"]: p for p in load()}
        for p in got["presets"]:
            p["source"] = "premiere"
            lib[p["id"]] = p
        save(list(lib.values()))
    return {"path": path, "imported": len(got["presets"]), "skipped": got["skipped"],
            "names": ["/".join(p["folder"] + [p["name"]]) for p in got["presets"]]}


def remove(preset_id: str) -> bool:
    with _lock:
        lib = load()
        keep = [p for p in lib if p["id"] != preset_id]
        save(keep)
        return len(keep) != len(lib)


def fit(preset: dict, clip: dict, frame_w: float, frame_h: float) -> list[dict]:
    """The preset's effects ready to append to ``clip``: new ids, points in the
    clip's pixels, keyframes moved to the clip's source time."""
    a_in, a_out = preset.get("anchor_in", 0), preset.get("anchor_out", 0)
    src_speed = float(preset.get("speed") or 1.0) or 1.0
    in_src = clip["source_in"]
    out_src = clip["source_in"] + round((clip["end"] - clip["start"]) * clip["speed"])
    kind = preset.get("type", "anchor_in")
    if kind == "scale" and a_out > a_in:
        k = (out_src - in_src) / (a_out - a_in)

        def when(t: int) -> int:
            return in_src + round((t - a_in) * k)
    else:
        k = clip["speed"] / src_speed  # same timeline offset at the target clip's speed
        if kind == "anchor_out":
            def when(t: int) -> int:
                return out_src - round((a_out - t) * k)
        else:
            def when(t: int) -> int:
                return in_src + round((t - a_in) * k)

    def pt(v):
        return [float(v[0]) * frame_w, float(v[1]) * frame_h] if isinstance(v, list) else v

    out = []
    for fx in preset.get("effects", []):
        fx = copy.deepcopy(fx)
        fx["id"] = new_id()
        fx["params"] = {n: pt(v) for n, v in fx.get("params", {}).items()}
        kfs = {}
        for name, frames in fx.get("keyframes", {}).items():
            if frames and isinstance(frames[0]["v"], list):
                frames = _point_keyframes(frames, frame_w, frame_h)  # values, tangents, path speeds -> pixels
            moved = []
            for kf in frames:
                kf = {**kf, "t": when(kf["t"])}
                for side in ("ease_in", "ease_out"):
                    if kf.get(side) and k:
                        kf[side] = [kf[side][0] / k, kf[side][1]]  # speeds follow the time stretch
                moved.append(kf)
            kfs[name] = sorted(moved, key=lambda f: f["t"])
        fx["keyframes"] = kfs
        out.append(fx)
    return out


def fit_track(preset: dict, frame_w: float, frame_h: float) -> list[dict]:
    """The preset's effects as track effects: keyframe times relative to each clip
    (from its In, from its Out, or stretched over it, as the preset was saved)."""
    a_in, a_out = preset.get("anchor_in", 0), preset.get("anchor_out", 0)
    kind = preset.get("type", "anchor_in")
    if kind == "scale" and a_out > a_in:
        ref = {"source_in": 0, "start": 0, "end": a_out - a_in, "speed": 1.0}
        anchor, ref_duration = "stretch", a_out - a_in
    else:
        # a zero-length clip at 0: when(t) becomes the sequence offset from the anchor
        ref = {"source_in": 0, "start": 0, "end": 0, "speed": 1.0}
        anchor, ref_duration = ("out" if kind == "anchor_out" else "in"), None
    out = fit(preset, ref, frame_w, frame_h)
    for fx in out:
        fx["anchor"], fx["ref_duration"] = anchor, ref_duration
    return out
