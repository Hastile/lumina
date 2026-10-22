"""Names of works and characters, made the same however they are written.

A work arrives as a folder name (블루 아카이브), a file title (『PUKEY GODDESS
SHOT TRICK』), Claude's reading (葬送のフリーレン, Frieren) or a tagger tag
(sousou_no_frieren). The shared entity catalog knows 44k IPs and their
characters with Korean names and 20k aliases; resolving every spelling
through it is what makes them one entity, keyed by catalog_ref.
"""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from . import db

CATALOG_PATH = Path(os.environ.get("LUMINA_ENTITY_CATALOG", "D:/Dev/shared/entity_catalog.db"))


def key(name: str) -> str:
    """Letters and digits only, case-folded: 'Frieren: Beyond Journey's End'
    and 'frieren beyond journeys end' are one key."""
    return "".join(ch for ch in (name or "").casefold() if ch.isalnum())


@dataclass
class Match:
    type: str                 # 'work' | 'character'
    catalog_id: int
    ref: str                  # entity.catalog_ref
    ko: Optional[str]         # None when the catalog has no Korean name yet
    names: list[str] = field(default_factory=list)  # native, English, tag
    ip_id: Optional[int] = None


class Catalog:
    def __init__(self, path: Path = CATALOG_PATH):
        self.ok = path.exists()
        self._by_key: dict[tuple[str, str], list[int]] = {}
        self._rows: dict[tuple[str, int], Match] = {}
        self._refs: dict[str, Match] = {}
        if not self.ok:
            return
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            for r in con.execute("SELECT ip_id, canonical_name_ko, title_native, title_english, danbooru_tag, needs_label "
                                 "FROM ip_entity"):
                m = Match("work", r[0], f"danbooru:{r[4]}" if r[4] else f"catalog:ip:{r[0]}",
                          None if r[5] else r[1], [n for n in (r[2], r[3], r[4], r[1]) if n])
                self._add(m)
            for r in con.execute("SELECT character_id, ip_id, canonical_name_ko, name_native, name_english, danbooru_tag, "
                                 "needs_label FROM character_entity WHERE is_active = 1"):
                m = Match("character", r[0], f"danbooru:{r[5]}" if r[5] else f"catalog:character:{r[0]}",
                          None if r[6] else r[2], [n for n in (r[3], r[4], r[5], r[2]) if n], ip_id=r[1])
                self._add(m)
            for etype, eid, alias in con.execute(
                    "SELECT entity_type, entity_id, alias FROM entity_alias WHERE is_active = 1 AND confidence >= 0.8"):
                t = "work" if etype == "ip" else "character"
                if (t, eid) in self._rows:
                    self._by_key.setdefault((t, key(alias)), []).append(eid)
        finally:
            con.close()

    def _add(self, m: Match) -> None:
        self._rows[(m.type, m.catalog_id)] = m
        self._refs[m.ref] = m
        for n in m.names:
            self._by_key.setdefault((m.type, key(n)), []).append(m.catalog_id)
            if m.type == "character" and "_(" in n:  # fern_(sousou_no_frieren) -> fern
                self._by_key.setdefault((m.type, key(n.split("_(")[0])), []).append(m.catalog_id)

    def resolve(self, type_: str, names: Iterable[str], ip_id: Optional[int] = None) -> Optional[Match]:
        """The catalog entry any of these names spells. For a character an
        ambiguous name ('Fern') is settled by the work, and refused if it
        cannot be."""
        if not self.ok:
            return None
        for n in names:
            k = key(n)
            if len(k) < 2:
                continue
            ids = list(dict.fromkeys(self._by_key.get((type_, k), [])))
            if type_ == "character" and ip_id is not None:
                ids = [i for i in ids if self._rows[(type_, i)].ip_id == ip_id] or ([] if len(ids) > 1 else ids)
            if len(ids) == 1:
                return self._rows[(type_, ids[0])]
        return None

    def by_ref(self, ref: str) -> Optional[Match]:
        return self._refs.get(ref)


_CATALOG: Optional[Catalog] = None


def catalog() -> Catalog:
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = Catalog()
    return _CATALOG


def _has_hangul(name: str) -> bool:
    return any("가" <= ch <= "힣" for ch in name)


def clean_names(names) -> list[str]:
    """Split what a model writes as one string ('フリーレン (Frieren)',
    'A / B') into names, and drop scraps too short to identify anything."""
    out: list[str] = []
    for n in names:
        for part in re.split(r"\s*[/|]\s*|\s*\(|\)", n or ""):
            part = part.strip(" )")
            k = key(part)
            if len(k) >= (3 if k.isascii() else 2):
                out.append(part)
    return list(dict.fromkeys(out))


def resolve_entity(con, type_: str, names: list[str], *, ip_ref: Optional[str] = None) -> Optional[int]:
    """The source-DB entity these names mean, created if new.

    Order: the catalog (so every spelling meets at one catalog_ref), then a
    source entity whose name or alias has the same key, then a new one. The
    name kept is the channel's (already in the source DB), else the catalog's
    Korean name, else a Korean name given; an entity known only by a Latin
    tag takes the first Korean name that arrives.
    """
    names = clean_names(names)
    if not names:
        return None
    ip_id = None
    if ip_ref:
        ip = catalog().by_ref(ip_ref)
        ip_id = ip.catalog_id if ip else None
    m = catalog().resolve(type_, names, ip_id)
    extra = clean_names(m.names) if m else []
    hangul = [n for n in names if _has_hangul(n)]
    found = None
    if m is not None:
        row = con.execute("SELECT entity_id FROM entity WHERE type = ? AND catalog_ref = ?", (type_, m.ref)).fetchone()
        found = row[0] if row else None
    if found is None:
        keys = {key(n) for n in names + extra}
        for r in con.execute("SELECT entity_id, name, aliases FROM entity WHERE type = ?", (type_,)):
            if keys & {key(n) for n in (r["name"], *r["aliases"].split("\n")) if n}:
                found = r["entity_id"]
                break
    if found is None:
        name = (m.ko if m and m.ko else None) or (hangul[0] if hangul else names[0])
        return db.entity(con, type_, name, aliases=names + extra, catalog_ref=m.ref if m else None)
    current = con.execute("SELECT name FROM entity WHERE entity_id = ?", (found,)).fetchone()[0]
    if not _has_hangul(current) and hangul:
        db.rename_entity(con, found, hangul[0])
        current = hangul[0]
    db.entity(con, type_, current, aliases=names + extra, catalog_ref=m.ref if m else None)
    return found


def find_entity(con, type_: str, names: list[str], *, ip_ref: Optional[str] = None) -> Optional[int]:
    """Like resolve_entity, but never invents a new name: only an entity the
    source DB already has or the catalog knows. For weak evidence such as a
    folder name, which is as often a character, a director or a topic
    (고죠 사토루, 신카이 마코토, 수익창출 항소) as a work."""
    names = clean_names(names)
    if not names:
        return None
    ip = catalog().by_ref(ip_ref) if ip_ref else None
    if catalog().resolve(type_, names, ip.catalog_id if ip else None) is not None:
        return resolve_entity(con, type_, names, ip_ref=ip_ref)
    keys = {key(n) for n in names}
    for r in con.execute("SELECT entity_id, name, aliases FROM entity WHERE type = ?", (type_,)):
        if keys & {key(n) for n in (r["name"], *r["aliases"].split("\n")) if n}:
            return r["entity_id"]
    return None


def characters_of(ip_ref: str, limit: int = 60) -> list[tuple[str, Optional[str]]]:
    """(Korean name, native name) of a work's characters, best known first:
    the cast Claude chooses from when it names who is on screen."""
    ip = catalog().by_ref(ip_ref)
    if ip is None or not CATALOG_PATH.exists():
        return []
    con = sqlite3.connect(f"file:{CATALOG_PATH.as_posix()}?mode=ro", uri=True)
    try:
        return [(r[0], r[1]) for r in con.execute(
            "SELECT canonical_name_ko, name_native FROM character_entity WHERE ip_id = ? AND is_active = 1 "
            "AND needs_label = 0 ORDER BY COALESCE(popularity, 0) DESC, COALESCE(danbooru_post_count, 0) DESC "
            "LIMIT ?", (ip.catalog_id, limit))]
    finally:
        con.close()
