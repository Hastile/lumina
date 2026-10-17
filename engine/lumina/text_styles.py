"""Text style library (Premiere's Text Styles / caption track styles).

Styles live outside projects, in ``~/.lumina/text_styles.json`` (or
``$LUMINA_TEXT_STYLES``), like Premiere keeps them in
``Documents/Adobe/Common/Assets/Text Styles``, where they are imported from
(.prtextstyle files: a one-item Premiere project whose StyleProjectItem holds
an ``AE.ADBE Text`` component). A style is text formatting only - font, size,
fill, stroke, shadow, background, tracking, alignment - never position or
transform, so applying one to a caption track restyles every cue in place.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
from pathlib import Path

from .model import new_id

_lock = threading.Lock()
FORMAT_FIELDS = ("font_family", "font_size", "bold", "italic", "color", "stroke_color", "stroke_width", "background", "align",
                 "tracking", "shadow")


def library_path() -> Path:
    if os.environ.get("LUMINA_TEXT_STYLES"):
        return Path(os.environ["LUMINA_TEXT_STYLES"])
    return Path.home() / ".lumina" / "text_styles.json"


def load() -> list[dict]:
    try:
        return json.loads(library_path().read_text(encoding="utf-8")).get("styles", [])
    except (OSError, ValueError):
        return []


def save(styles: list[dict]) -> None:
    p = library_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": 1, "styles": styles}, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def get(style_id: str) -> dict | None:
    return next((s for s in load() if s["id"] == style_id), None)


def default_paths() -> list[Path]:
    """Premiere's Text Styles folders (Documents, OneDrive Documents)."""
    home = Path.home()
    cands = [home / "Documents" / "Adobe" / "Common" / "Assets" / "Text Styles"]
    for od in (os.environ.get("OneDrive"), os.environ.get("OneDriveConsumer")):
        if od:
            cands += [Path(od) / d / "Adobe" / "Common" / "Assets" / "Text Styles" for d in ("Documents", "문서")]
    cands += [home / "OneDrive" / d / "Adobe" / "Common" / "Assets" / "Text Styles" for d in ("Documents", "문서")]
    seen, out = set(), []
    for c in cands:
        if c.is_dir() and str(c).lower() not in seen:
            seen.add(str(c).lower())
            out.append(c)
    return out


def _style_from_component(g, comp) -> dict | None:
    from .io import prproj as P

    params = P._params(g, comp)
    for pe in params.values():
        raw = g.blob(pe.find("StartKeyframeValue"))
        if not raw or len(raw) < 100:
            continue
        try:
            doc = P._text_doc(base64.b64decode(raw))
        except ValueError:
            continue
        if doc:
            r = doc["runs"][0]
            return {
                "font_family": r["font_family"] or "Pretendard", "font_size": float(r["font_size"]), "bold": False, "italic": False,
                "color": r["color"], "stroke_color": r["stroke_color"], "stroke_width": float(r["stroke_width"] or 0.0),
                "background": None, "align": doc["align"], "tracking": float(r["tracking"] or 0.0), "shadow": doc["shadow"],
            }
    return None


def read_styles(path: str) -> list[dict]:
    """The text styles a Premiere file holds: a .prtextstyle, or a project's StyleProjectItems."""
    from .io import prproj as P

    g = P.load(path)
    out = []
    for item in (e for e in g.root if e.tag == "StyleProjectItem"):
        comp = g.at(item, "Component")
        if comp is None or P._text(comp, "MatchName") != "AE.ADBE Text":
            continue
        style = _style_from_component(g, comp)
        if style is not None:
            out.append({"name": P._text(item, "ProjectItem/Name") or Path(path).stem, "style": style, "source": str(path)})
    return out


def _key(s: dict) -> str:
    return hashlib.sha1(json.dumps([s["name"], s["style"]], sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def import_premiere(path: str | None = None) -> dict:
    """Merge Premiere text styles into the library: a .prtextstyle file, a folder of them, or a
    project; default: Premiere's Text Styles folder. A style with the same name and look is kept once."""
    targets: list[Path]
    if path is None:
        dirs = default_paths()
        if not dirs:
            raise FileNotFoundError("no Premiere Text Styles folder found (Documents/Adobe/Common/Assets/Text Styles)")
        targets = [f for d in dirs for f in sorted(d.glob("*.prtextstyle"))]
    elif Path(path).is_dir():
        targets = sorted(Path(path).glob("*.prtextstyle"))
    else:
        targets = [Path(path)]
    found, skipped = [], []
    for f in targets:
        try:
            got = read_styles(str(f))
        except Exception as exc:  # noqa: BLE001 - reported per file
            skipped.append(f"{f.name}: {exc}")
            continue
        if not got:
            skipped.append(f"{f.name}: no text style")
        found += got
    with _lock:
        lib = load()
        keys = {_key(s) for s in lib}
        added = []
        for s in found:
            if _key(s) in keys:
                continue
            keys.add(_key(s))
            rec = {"id": new_id(), "name": s["name"], "style": s["style"], "source": s["source"]}
            lib.append(rec)
            added.append(rec["name"])
        save(lib)
    return {"imported": len(added), "names": added, "skipped": skipped, "total": len(found)}


def remove(style_id: str) -> bool:
    with _lock:
        lib = load()
        keep = [s for s in lib if s["id"] != style_id]
        save(keep)
        return len(keep) != len(lib)


def save_style(name: str, style: dict) -> dict:
    """Save a caption track's (or text layer's) formatting as a new style."""
    rec = {"id": new_id(), "name": name, "style": {k: style.get(k) for k in FORMAT_FIELDS if k in style}, "source": None}
    with _lock:
        lib = load()
        lib.append(rec)
        save(lib)
    return rec
