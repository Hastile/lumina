"""Installed-font lookup by family, full or PostScript name.

Premiere stores PostScript names ("S-CoreDream-9Black"); the GUI resolves them
with CSS ``local()``. The renderer needs a file, so we index the ``name``
table of every installed font once (reading only the headers) and cache it.
"""

from __future__ import annotations

import json
import os
import struct
import threading
from pathlib import Path

from ..media.jobs import cache_root

FONT_EXTS = {".ttf", ".otf", ".ttc", ".otc"}
FALLBACKS = ["Pretendard", "Malgun Gothic", "Noto Sans KR", "Arial", "DejaVu Sans"]
_lock = threading.Lock()
_index: dict[str, list[dict]] | None = None


def font_dirs() -> list[Path]:
    dirs = []
    if os.name == "nt":
        dirs.append(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts")
        if os.environ.get("LOCALAPPDATA"):
            dirs.append(Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "Windows" / "Fonts")
    else:
        dirs += [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path.home() / ".fonts",
                 Path("/Library/Fonts"), Path("/System/Library/Fonts"), Path.home() / "Library" / "Fonts"]
    extra = os.environ.get("LUMINA_FONT_DIRS")
    if extra:
        dirs += [Path(p) for p in extra.split(os.pathsep) if p]
    return [d for d in dirs if d.is_dir()]


def norm(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _read_names(f, offset: int) -> dict[int, str]:
    f.seek(offset + 4)
    (num_tables,) = struct.unpack(">H", f.read(2))
    f.seek(offset + 12)
    records = f.read(16 * num_tables)
    name_off = None
    for i in range(num_tables):
        tag, _, off, _ = struct.unpack(">4sIII", records[16 * i:16 * i + 16])
        if tag == b"name":
            name_off = off
            break
    if name_off is None:
        return {}
    f.seek(name_off)
    _, count, string_off = struct.unpack(">HHH", f.read(6))
    recs = f.read(12 * count)
    out: dict[int, tuple[int, str]] = {}
    for i in range(count):
        pid, eid, lang, nid, length, off = struct.unpack(">HHHHHH", recs[12 * i:12 * i + 12])
        if nid not in (1, 2, 4, 6, 16, 17):
            continue
        f.seek(name_off + string_off + off)
        raw = f.read(length)
        try:
            text = raw.decode("utf-16-be") if pid in (0, 3) else raw.decode("latin-1")
        except UnicodeDecodeError:
            continue
        # prefer English (Windows 0x409) strings, but keep localized ones as aliases
        rank = 0 if (pid == 3 and lang == 0x409) else 1 if pid == 1 else 2
        if nid not in out or rank < out[nid][0]:
            out[nid] = (rank, text)
        out.setdefault(1000 + nid * 10 + min(rank, 9), (rank, text))
    return {k: v for k, (_, v) in out.items()}


def read_faces(path: Path) -> list[dict]:
    faces = []
    with open(path, "rb") as f:
        head = f.read(12)
        if head[:4] in (b"ttcf",):
            (n,) = struct.unpack(">I", head[8:12])
            offsets = struct.unpack(f">{n}I", f.read(4 * n))
        else:
            offsets = (0,)
        for index, off in enumerate(offsets):
            names = _read_names(f, off)
            if not names:
                continue
            aliases = sorted({v for k, v in names.items() if k >= 1000})
            faces.append({
                "path": str(path), "index": index,
                "family": names.get(16) or names.get(1, ""), "style": names.get(17) or names.get(2, ""),
                "full": names.get(4, ""), "ps": names.get(6, ""), "aliases": aliases,
            })
    return faces


def _build() -> dict[str, list[dict]]:
    files = sorted(p for d in font_dirs() for p in d.rglob("*") if p.suffix.lower() in FONT_EXTS)
    stamp = [[str(p), p.stat().st_size] for p in files]
    cache = cache_root() / "fonts.json"
    faces: list[dict] | None = None
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
        if data.get("stamp") == stamp:
            faces = data["faces"]
    except (OSError, ValueError, KeyError):
        pass
    if faces is None:
        faces = []
        for p in files:
            try:
                faces += read_faces(p)
            except (OSError, struct.error):
                continue
        try:
            cache.write_text(json.dumps({"stamp": stamp, "faces": faces}), encoding="utf-8")
        except OSError:
            pass
    index: dict[str, list[dict]] = {}
    for face in faces:
        keys = {face["ps"], face["full"], f"{face['family']} {face['style']}", face["family"], *face["aliases"]}
        for k in keys:
            if k:
                index.setdefault(norm(k), []).append(face)
    return index


def index() -> dict[str, list[dict]]:
    global _index
    with _lock:
        if _index is None:
            _index = _build()
        return _index


def _is_regular(face: dict) -> bool:
    return norm(face["style"]) in ("regular", "normal", "book", "roman", "")


def _is_bold(face: dict) -> bool:
    s = norm(face["style"])
    return "bold" in s and "semi" not in s and "extra" not in s and "italic" not in s


def find(name: str, bold: bool = False) -> tuple[dict | None, bool]:
    """Best face for a CSS-ish font request -> (face, exact). ``exact`` is
    False when a fallback font was used."""
    idx = index()
    for candidate, exact in [(name, True), *((f, False) for f in FALLBACKS)]:
        faces = idx.get(norm(candidate))
        if not faces:
            continue
        if bold:
            pick = next((f for f in faces if _is_bold(f)), None) or idx.get(norm(candidate + "bold"), [None])[0]
            if pick:
                return pick, exact
        pick = next((f for f in faces if norm(f["ps"]) == norm(candidate) or norm(f["full"]) == norm(candidate)), None)
        pick = pick or next((f for f in faces if _is_regular(f)), None) or faces[0]
        return pick, exact
    return None, False
