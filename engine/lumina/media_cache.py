"""Media cache (Premiere's Preferences > Media Cache).

Generated files - thumbnails, filmstrips, waveforms, proxies - live in one
folder per media item under the cache root (``$LUMINA_CACHE``, else the
``cache_dir`` setting, else ``~/.lumina/cache``). Settings are kept in
``~/.lumina/settings.json`` (``$LUMINA_SETTINGS``). Cleaning removes whole
media folders: unused ones (no open project has that media), all of them, or
ones untouched for N days; ``auto_clean_days`` does the last at engine start.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Callable

DEFAULTS = {"cache_dir": None, "auto_clean_days": 0}


def settings_path() -> Path:
    if os.environ.get("LUMINA_SETTINGS"):
        return Path(os.environ["LUMINA_SETTINGS"])
    return Path(os.environ.get("LUMINA_HOME") or Path.home() / ".lumina") / "settings.json"


def _read_all() -> dict:
    try:
        return json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def settings() -> dict:
    return {**DEFAULTS, **(_read_all().get("media_cache") or {})}


def default_root() -> Path:
    return Path.home() / ".lumina" / "cache"


def root() -> Path:
    if os.environ.get("LUMINA_CACHE"):
        return Path(os.environ["LUMINA_CACHE"])
    return Path(settings()["cache_dir"] or default_root())


def update_settings(p: dict) -> dict:
    """Change cache settings (cache_dir: a folder, or null for the default; auto_clean_days: 0 = never)."""
    cur = settings()
    if "cache_dir" in p:
        d = p["cache_dir"] or None
        if d:
            Path(d).mkdir(parents=True, exist_ok=True)
        cur["cache_dir"] = d
    if "auto_clean_days" in p:
        cur["auto_clean_days"] = max(0, int(p["auto_clean_days"] or 0))
    data = _read_all()
    data["media_cache"] = cur
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    return {**cur, "root": str(root())}


def _entries() -> list[tuple[str, Path, int, float]]:
    """(media id, folder, bytes, last modified) for every media folder in the cache."""
    r = root()
    if not r.is_dir():
        return []
    out = []
    for d in r.iterdir():
        if not d.is_dir():
            continue
        size, mtime = 0, d.stat().st_mtime
        for f in d.rglob("*"):
            try:
                st = f.stat()
            except OSError:
                continue
            if f.is_file():
                size += st.st_size
                mtime = max(mtime, st.st_mtime)
        out.append((d.name, d, size, mtime))
    return out


def info(in_use: set[str]) -> dict:
    ents = _entries()
    used = [e for e in ents if e[0] in in_use]
    return {
        **settings(), "root": str(root()), "default_root": str(default_root()),
        "env_override": bool(os.environ.get("LUMINA_CACHE")),
        "items": len(ents), "bytes": sum(e[2] for e in ents),
        "used_items": len(used), "used_bytes": sum(e[2] for e in used),
    }


def clean(mode: str, in_use: set[str], days: float | None = None) -> dict:
    """mode: 'unused' (media no open project has), 'all', or 'older' (untouched for ``days``)."""
    if mode not in ("unused", "all", "older"):
        raise ValueError(f"unknown cache clean mode: {mode}")
    if mode == "older" and not days:
        raise ValueError("'older' needs days")
    cutoff = time.time() - float(days or 0) * 86400
    removed, freed, failed = [], 0, []
    for mid, d, size, mtime in _entries():
        if mode == "unused" and mid in in_use:
            continue
        if mode == "older" and mtime >= cutoff:
            continue
        try:
            shutil.rmtree(d)
        except OSError as exc:
            failed.append(f"{mid}: {exc}")
            continue
        removed.append(mid)
        freed += size
    return {"removed": len(removed), "removed_media": removed, "freed_bytes": freed, "failed": failed}


def auto_clean(in_use: Callable[[], set[str]]) -> dict | None:
    days = settings()["auto_clean_days"]
    if not days:
        return None
    try:
        return clean("older", in_use(), days)
    except OSError:
        return None
