"""Connection + CRUD helpers for the source metadata RDBMS (database/source.db).

Connection-per-call: callers open/close (or hold one connection across a
batch) and pass it in; nothing here caches a connection globally. Mirrors
pr-mcp.py's convention of keeping I/O boundaries explicit. Every insert
that could collide with a UNIQUE constraint is get-or-create / upsert, so
repeated ingestion runs never crash on duplicate sources, media, or tags.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any, Optional

DEFAULT_DB_PATH = Path.home() / ".lumina" / "source.db"


def connect(db_path: "str | Path" = DEFAULT_DB_PATH) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA foreign_keys = ON")
    con.row_factory = sqlite3.Row
    return con


def _now_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# source
# ---------------------------------------------------------------------------

def get_or_create_source(
    con: sqlite3.Connection,
    *,
    source_category: str,
    is_official: bool,
    work_title: Optional[str] = None,
    origin_ref: Optional[str] = None,
    subtitle: Optional[str] = None,
    episode: Optional[int] = None,
) -> int:
    """출처 행을 찾거나 만듭니다.

    같은 출처인지를 (작품, 편, 화수) 로 봅니다 (migration_009). 예전에는
    (작품, origin_ref) 였는데, 그러면 원피스 1089화와 1090화가 같은 행으로
    합쳐집니다 - 둘 다 origin_ref 가 비어 있으니까요.

    셋 다 NULL 인 것은 정상입니다: 스톡 영상이나 극장판에는 화수가 없습니다.
    그런 경우 작품명이 같으면 같은 출처로 봅니다.
    """
    row = con.execute(
        "SELECT source_id FROM source "
        "WHERE work_title IS ? AND subtitle IS ? AND episode IS ?",
        (work_title, subtitle, episode),
    ).fetchone()
    if row is not None:
        _assign_catalog_ip(con, row['source_id'], work_title)
        return row["source_id"]
    cur = con.execute(
        "INSERT INTO source (source_category, is_official, work_title, origin_ref, "
        "subtitle, episode, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (source_category, int(bool(is_official)), work_title, origin_ref,
         subtitle, episode, _now_ms()),
    )
    _assign_catalog_ip(con, cur.lastrowid, work_title)
    return cur.lastrowid


def _assign_catalog_ip(con: sqlite3.Connection, source_id: int, title: Optional[str]) -> None:
    if not title or 'ip_entity_id' not in {r[1] for r in con.execute('PRAGMA table_info(source)')}:
        return
    ent = connect_entity_catalog()
    if ent is None:
        return
    try:
        resolved = resolve_ip_entity(ent, title)
        if resolved:
            con.execute('UPDATE source SET ip_entity_id=? WHERE source_id=? AND ip_entity_id IS NULL', (resolved['ip_id'],source_id))
    finally:
        ent.close()


# ---------------------------------------------------------------------------
# entity catalog (정본 작품/캐릭터)
# ---------------------------------------------------------------------------
#
# 카탈로그는 별도 파일입니다. adb-mcp 와 character_trend 가 함께 읽어야 해서
# 둘 다 바깥에 뒀고, 그래서 ip_entity_id 는 진짜 FOREIGN KEY 가 아니라
# 약한 참조입니다 (migration_008 참고). 없는 환경에서도 인제스트는 그대로
# 돌아가야 하므로, 여기 있는 함수는 전부 "카탈로그가 없으면 None" 입니다.

def _default_entity_catalog_path() -> Path:
    """카탈로그 파일을 찾습니다. 소스 실행과 exe 실행에서 답이 다릅니다.

    소스로 돌 때는 저장소 바깥의 형제 폴더입니다 (`D:\\Dev\\shared`).
    그런데 **frozen 상태에서는 `__file__` 이 PyInstaller 임시 추출
    폴더**라, 같은 계산을 하면 존재하지도 않는 경로가 나옵니다. 그러면
    connect_entity_catalog 가 조용히 None 을 돌려주고 드롭다운이 빈 채로
    뜹니다 - 오류도 로그도 없이 기능만 사라지는, 가장 알아채기 어려운
    실패입니다.

    그래서 frozen 일 때는 exe 가 놓인 자리를 기준으로 몇 군데를 훑고,
    실제로 있는 것을 씁니다. 하나도 없으면 첫 후보를 돌려주는데, 그건
    "없다" 는 답을 내되 오류 메시지에 그럴듯한 경로가 찍히게 하려는
    것입니다.
    """
    override = os.environ.get("ENTITY_CATALOG_PATH")
    if override:
        return Path(override)

    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
        # exe 옆 → dist/ 위 → 저장소 위. 지금 배치는 세 번째가 답입니다
        # (dist\프로젝트폴더관리.exe 에서 ..\..\shared).
        roots = [base, base.parent, base.parent.parent]
    else:
        repo = Path(__file__).resolve().parent.parent
        roots = [repo.parent, repo]

    candidates = [root / "shared" / "entity_catalog.db" for root in roots]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


ENTITY_CATALOG_PATH = _default_entity_catalog_path()


def entity_catalog_available(path: "str | Path | None" = None) -> bool:
    return Path(path or ENTITY_CATALOG_PATH).exists()


def connect_entity_catalog(path: "str | Path | None" = None) -> Optional[sqlite3.Connection]:
    """읽기 전용 연결. 카탈로그가 없으면 None 을 돌려주고 예외를 내지
    않습니다 - 카탈로그는 편차를 막는 장치지, 인제스트의 전제 조건이
    아닙니다."""
    resolved = Path(path or ENTITY_CATALOG_PATH)
    if not resolved.exists():
        return None
    con = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def entity_key(value: str) -> str:
    """entity_catalog 의 alias_key 와 같은 정규화.

    trend.db 가 쓰던 규칙(NFKC → casefold → 구두점·공백 제거)을 그대로
    가져온 것입니다. 세 DB 의 키가 서로 교환 가능해야 하므로 여기서 규칙을
    바꾸면 안 됩니다.
    """
    text = unicodedata.normalize("NFKC", value or "").casefold()
    return "".join(ch for ch in text if unicodedata.category(ch)[0] not in "PZC")


def public_entity_table(con: sqlite3.Connection, kind: str) -> str:
    """Both apps consume the same published identities when quality migration exists."""
    table = 'character_entity' if kind == 'character' else 'ip_entity'
    view = 'public_' + table
    return view if con.execute("SELECT 1 FROM sqlite_master WHERE type='view' AND name=?", (view,)).fetchone() else table


def recent_ip_sources(ip_id: int, *, limit: int = 8, collection_path=None) -> list[dict[str, Any]]:
    """Reuse public references collected by the trend app; never search the network."""
    from contextlib import closing
    path = Path(collection_path or os.environ.get('SHARED_COLLECTION_PATH') or ENTITY_CATALOG_PATH.parent / 'collection.db')
    if not path.is_file():
        return []
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=3)) as con:
            con.row_factory = sqlite3.Row
            return [dict(r) for r in con.execute('''SELECT DISTINCT r.url,r.title,r.source,r.author,r.published_at
                FROM resource r JOIN resource_entity e USING(resource_key)
                WHERE e.entity_type='work' AND e.entity_id=?
                ORDER BY r.published_at DESC LIMIT ?''', (int(ip_id),max(1,min(int(limit),30))))]
    except sqlite3.Error:
        return []


def resolve_ip_entity(
    ent_con: sqlite3.Connection, title: str
) -> Optional[dict[str, Any]]:
    """제목/별칭 문자열 하나를 정본 작품 행으로 해소합니다.

    별칭까지 보는 이유는 입력이 어디서 오는지 모르기 때문입니다 - 사람이
    친 한글, danbooru 태그, AniList 영문 제목이 전부 같은 작품이어야 합니다.
    """
    key = entity_key(title)
    if not key:
        return None
    table = public_entity_table(ent_con, 'ip')
    rows = ent_con.execute(
        f"""SELECT DISTINCT w.ip_id, w.canonical_name_ko, w.danbooru_tag, w.media_type,
                  w.provenance, w.needs_label
             FROM {table} w LEFT JOIN entity_alias a ON w.ip_id = a.entity_id
               AND a.entity_type='ip' AND a.is_active=1
            WHERE w.is_active=1 AND (w.name_key=? OR a.alias_key=?) LIMIT 2""",
        (key,key),
    ).fetchall()
    return dict(rows[0]) if len(rows) == 1 else None


def resolve_character_entity(
    ent_con: sqlite3.Connection, name: str, *, ip_entity_id: Optional[int] = None
) -> list[dict[str, Any]]:
    """이름 하나로 캐릭터 후보들을 찾습니다. 하나로 좁히지 않고 목록을
    돌려주는 게 의도입니다 - '사사키' 는 실제로 두 작품에 있고, 어느
    쪽인지는 프레임이 정합니다 (CLAUDE.md: 확인하지 않은 이름은 쓰지 않는다).

    ip_entity_id 를 주면 그 작품 안으로만 좁힙니다. 작품이 이미 확정된
    컷에서는 이게 사실상 유일해집니다.
    """
    key = entity_key(name)
    if not key:
        return []

    # 정본명과 별칭 양쪽을 봅니다. 별칭만 보면 라벨이 나중에 고쳐진 행을
    # 놓칩니다 - 실제로 그랬습니다: source.db 의 한글명을 카탈로그로
    # 되먹인 뒤 canonical_name_ko 는 '타카나시 호시노' 가 됐는데 별칭
    # 인덱스에는 영문만 남아 있어 조회가 전부 빗나갔습니다. 정본명은
    # 별칭이 하나도 없어도 반드시 찾혀야 하는 값입니다.
    sql = """SELECT c.character_id, c.ip_id, c.canonical_name_ko, c.danbooru_tag,
                    c.needs_label, c.is_active, w.canonical_name_ko AS ip_name
               FROM character_entity c
               LEFT JOIN ip_entity w ON w.ip_id = c.ip_id
              WHERE (c.name_key = :key
                     OR EXISTS (SELECT 1 FROM entity_alias a
                                 WHERE a.entity_type = 'character'
                                   AND a.entity_id = c.character_id
                                   AND a.alias_key = :key AND a.is_active = 1))"""
    params: dict[str, Any] = {"key": key}
    if public_entity_table(ent_con, 'character') == 'public_character_entity':
        sql += ' AND c.character_id IN (SELECT character_id FROM public_character_entity)'
    if ip_entity_id is not None:
        # 소속은 character_ip 로 봅니다 (N:M, migration_005). 한 인물이 같은
        # 작품군의 여러 작품에 나오므로, 대표 소속(c.ip_id)만 보면 `페이트`
        # 로 좁혔을 때 대표가 Fate/Grand Order 인 서번트를 놓칩니다.
        sql += (" AND EXISTS (SELECT 1 FROM character_ip m"
                " WHERE m.character_id = c.character_id AND m.ip_id = :ip_id)")
        params["ip_id"] = ip_entity_id
    # 의상 변형(is_active=0)은 뒤로. 같은 인물이라 라벨은 같지만, 고를
    # 대상은 대표 행이어야 합니다.
    return [dict(r) for r in ent_con.execute(sql + " ORDER BY c.is_active DESC", params)]


def list_character_ips(
    ent_con: sqlite3.Connection, character_id: int
) -> list[dict[str, Any]]:
    """한 인물이 속한 작품들. 대표가 앞에 옵니다."""
    return [dict(r) for r in ent_con.execute(
        """SELECT m.ip_id, m.is_primary, m.source, w.canonical_name_ko AS ip_name
             FROM character_ip m
             LEFT JOIN ip_entity w ON w.ip_id = m.ip_id
            WHERE m.character_id = ?
            ORDER BY m.is_primary DESC, w.canonical_name_ko""",
        (character_id,),
    )]


def link_character_to_ip(
    ent_con: sqlite3.Connection, *, character_id: int, ip_id: int,
    make_primary: bool = False, source: str = "user",
) -> None:
    """인물을 작품에 연결합니다 (N:M, migration_005).

    `character_entity.ip_id` 는 대표 소속으로 남아 있고, 규칙은 "ip_id 는
    언제나 character_ip 에 is_primary=1 로 같은 행이 있어야 한다" 입니다.
    그 둘을 따로 쓰면 반드시 어긋나므로 여기서 함께 씁니다 - 대표를 바꾸면
    이전 대표를 0 으로 내리고, 행 자체를 지우지는 않습니다(소속은 남습니다).
    """
    # 접힌 중복 행에 소속을 붙이면 드롭다운에 뜨지 않습니다 - 그 행은
    # is_active=0 이라 목록 쿼리에서 걸러지고, 연결은 있는데 아무 데도
    # 보이지 않는 상태가 됩니다. 실제로 그랬습니다: 이름으로 찾은 `렘` 이
    # 병합된 옛 행이어서 콰르텟에 붙였는데 목록에 나오지 않았습니다.
    # 그래서 항상 대표 행으로 옮겨 답니다.
    canonical = resolve_canonical_character(ent_con, character_id)
    if canonical is not None:
        character_id = canonical["character_id"]

    if make_primary:
        ent_con.execute(
            "UPDATE character_ip SET is_primary = 0 WHERE character_id = ?", (character_id,))
    ent_con.execute(
        "INSERT INTO character_ip (character_id, ip_id, is_primary, source) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(character_id, ip_id) DO UPDATE SET is_primary = MAX(is_primary, excluded.is_primary)",
        (character_id, ip_id, 1 if make_primary else 0, source),
    )
    if make_primary:
        ent_con.execute(
            "UPDATE character_entity SET ip_id = ?, updated_at = ? WHERE character_id = ?",
            (ip_id, _now_ms(), character_id),
        )


def unlink_character_from_ip(
    ent_con: sqlite3.Connection, *, character_id: int, ip_id: int
) -> bool:
    """소속 하나를 끊습니다. 대표 소속은 끊지 않습니다 - 인물이 어느
    작품에도 안 속한 상태가 되면 드롭다운 어디에서도 고를 수 없게 되고,
    그건 지운 것과 같은데 지웠다는 기록은 남지 않습니다. 먼저 다른 소속을
    대표로 지정하십시오."""
    row = ent_con.execute(
        "SELECT is_primary FROM character_ip WHERE character_id = ? AND ip_id = ?",
        (character_id, ip_id),
    ).fetchone()
    if row is None or row["is_primary"]:
        return False
    ent_con.execute(
        "DELETE FROM character_ip WHERE character_id = ? AND ip_id = ?", (character_id, ip_id))
    return True


def resolve_canonical_character(
    ent_con: sqlite3.Connection, character_id: int
) -> Optional[dict[str, Any]]:
    """중복으로 접힌 행이면 대표 행을 돌려줍니다.

    같은 인물이 두 행으로 남는 경우가 있습니다 - danbooru 씨앗은 태그를,
    AniList 씨앗은 한글명을 갖고 오는데 이름 형태가 달라 빌드 때 브리지에
    실패하면 그렇게 됩니다. 중복 행을 지우지 않는 이유는 그쪽이
    danbooru_tag 를 들고 있는 경우가 많아서입니다: 지우면 태거가
    `krista_lenz` 를 뱉었을 때 닿을 곳이 없어집니다.

    그래서 행은 남기고 canonical_character_id 로 대표를 가리키게 한 뒤,
    읽는 쪽이 여기서 체인을 따라갑니다 - resolve_tag_id / resolve_media_id
    와 같은 방식이고, 순환 방어도 같습니다.
    """
    seen: set[int] = set()
    current = character_id
    while current not in seen:
        seen.add(current)
        row = ent_con.execute(
            "SELECT character_id, ip_id, canonical_name_ko, danbooru_tag, needs_label, "
            "is_active, canonical_character_id FROM character_entity WHERE character_id = ?",
            (current,),
        ).fetchone()
        if row is None:
            return None
        if row["canonical_character_id"] is None:
            if ent_con.execute("SELECT 1 FROM sqlite_master WHERE name='catalog_publication'").fetchone():
                blocked = ent_con.execute("SELECT 1 FROM catalog_publication WHERE entity_type='character' AND entity_id=? AND reason='field_conflict'", (row['character_id'],)).fetchone()
                if blocked:
                    return None
            return dict(row)
        current = row["canonical_character_id"]
    return None  # 순환 - 정상 데이터는 여기 오지 않습니다


def resolve_character_by_danbooru_tag(
    ent_con: sqlite3.Connection, danbooru_tag: str
) -> Optional[dict[str, Any]]:
    """태거 출력 하나를 대표 인물 행으로 해소합니다.

    is_active 를 조건에 넣지 않는 게 핵심입니다. 의상 변형
    (`hoshino_(swimsuit)_...`)과 개장형(`fubuki_kai_ni`), 그리고 중복으로
    접힌 행은 전부 is_active=0 인데, 태거는 바로 그런 태그를 뱉습니다.
    걸러내는 건 드롭다운이 할 일이고, 여기서는 닿는 것이 먼저입니다.
    """
    row = ent_con.execute(
        "SELECT character_id FROM character_entity WHERE danbooru_tag = ?", (danbooru_tag,)
    ).fetchone()
    return resolve_canonical_character(ent_con, row["character_id"]) if row else None


def link_source_to_entity(
    con: sqlite3.Connection, *, source_id: int, ip_entity_id: Optional[int]
) -> None:
    """source 행을 정본 작품에 묶습니다. None 은 묶음 해제 - 스톡 영상이나
    유물 사진처럼 작품이 없는 소스가 실제로 있으므로 정상적인 상태입니다."""
    con.execute(
        "UPDATE source SET ip_entity_id = ? WHERE source_id = ?",
        (ip_entity_id, source_id),
    )


# ---------------------------------------------------------------------------
# media
# ---------------------------------------------------------------------------

def get_or_create_media(
    con: sqlite3.Connection,
    *,
    media_type: str,
    file_path: str,
    source_id: int,
    file_hash: Optional[str] = None,
    duration_ms: Optional[int] = None,
    fps: Optional[float] = None,
    summary_text: Optional[str] = None,
    availability: str = "available",
    path_kind: str = "file",
    parent_media_id: Optional[int] = None,
    sequence_index: Optional[int] = None,
    source_offset_ms: Optional[int] = None,
) -> int:
    if media_type not in ("image", "video"):
        raise ValueError("media_type must be 'image' or 'video'")
    if availability not in ("available", "pending"):
        raise ValueError("availability must be 'available' or 'pending'")
    if path_kind not in ("file", "directory"):
        raise ValueError("path_kind must be 'file' or 'directory'")
    if file_hash:
        row = con.execute(
            "SELECT media_id FROM media WHERE file_hash = ?", (file_hash,)
        ).fetchone()
        if row is not None:
            return row["media_id"]
    cur = con.execute(
        "INSERT INTO media (media_type, file_path, file_hash, duration_ms, fps, "
        "source_id, summary_text, created_at, availability, path_kind, "
        "parent_media_id, sequence_index, source_offset_ms) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (media_type, file_path, file_hash, duration_ms, fps, source_id, summary_text,
         _now_ms(), availability, path_kind, parent_media_id, sequence_index,
         source_offset_ms),
    )
    return cur.lastrowid


def list_media_sequence(con: sqlite3.Connection, parent_media_id: int) -> list[dict[str, Any]]:
    """The split children of one recording, in the order they were cut out of
    it. Ordering is by sequence_index (see migration_004), not by media_id -
    re-exporting a single cut mints a new row with a higher id but the same
    position, and the position is what matters."""
    return [
        dict(row)
        for row in con.execute(
            "SELECT media_id, sequence_index, file_path, duration_ms, fps, availability "
            "FROM media WHERE parent_media_id = ? ORDER BY sequence_index",
            (parent_media_id,),
        )
    ]


def resolve_media_id(con: sqlite3.Connection, media_id: int) -> int:
    """Follows canonical_media_id chains so a duplicate-format row always
    resolves to the representative. Mirrors resolve_tag_id."""
    seen = set()
    current = media_id
    while current not in seen:
        seen.add(current)
        row = con.execute(
            "SELECT canonical_media_id FROM media WHERE media_id = ?", (current,)
        ).fetchone()
        if row is None or row["canonical_media_id"] is None:
            return current
        current = row["canonical_media_id"]
    return current  # cycle guard; well-formed data never reaches this


def merge_media_into(con: sqlite3.Connection, *, duplicate_media_id: int, canonical_media_id: int) -> None:
    """Marks duplicate_media_id as the same picture as canonical_media_id -
    the case where one image was saved twice in different formats, so the
    file hashes differ and get_or_create_media's dedupe cannot see it. The
    duplicate's row and cuts are left intact (the file is real and still on
    disk); the search layer skips non-canonical rows. Mirrors merge_tag_into."""
    if duplicate_media_id == canonical_media_id:
        raise ValueError("a media row cannot be its own canonical")
    con.execute(
        "UPDATE media SET canonical_media_id = ? WHERE media_id = ?",
        (canonical_media_id, duplicate_media_id),
    )


def set_media_availability(con: sqlite3.Connection, *, media_id: int, availability: str) -> None:
    if availability not in ("available", "pending"):
        raise ValueError("availability must be 'available' or 'pending'")
    con.execute("UPDATE media SET availability = ? WHERE media_id = ?", (availability, media_id))


def set_media_path_kind(con: sqlite3.Connection, *, media_id: int, path_kind: str) -> None:
    if path_kind not in ("file", "directory"):
        raise ValueError("path_kind must be 'file' or 'directory'")
    con.execute("UPDATE media SET path_kind = ? WHERE media_id = ?", (path_kind, media_id))


# ---------------------------------------------------------------------------
# scene_cut
# ---------------------------------------------------------------------------

def upsert_cut(
    con: sqlite3.Connection,
    *,
    media_id: int,
    cut_index: int,
    start_ms: int,
    end_ms: int,
    description_text: str,
    shot_type: Optional[str] = None,
) -> int:
    """Insert a cut, or overwrite it in place if (media_id, cut_index) already
    exists (e.g. re-running an analysis pass with a better description)."""
    con.execute(
        "INSERT INTO scene_cut (media_id, cut_index, start_ms, end_ms, shot_type, description_text) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(media_id, cut_index) DO UPDATE SET "
        "start_ms=excluded.start_ms, end_ms=excluded.end_ms, "
        "shot_type=excluded.shot_type, description_text=excluded.description_text",
        (media_id, cut_index, start_ms, end_ms, shot_type, description_text),
    )
    row = con.execute(
        "SELECT cut_id FROM scene_cut WHERE media_id = ? AND cut_index = ?",
        (media_id, cut_index),
    ).fetchone()
    return row["cut_id"]


# ---------------------------------------------------------------------------
# dialogue
# ---------------------------------------------------------------------------

def insert_dialogue(
    con: sqlite3.Connection,
    *,
    cut_id: int,
    text: str,
    speaker: Optional[str] = None,
    start_ms: Optional[int] = None,
    end_ms: Optional[int] = None,
) -> int:
    cur = con.execute(
        "INSERT INTO dialogue (cut_id, start_ms, end_ms, speaker, text) VALUES (?, ?, ?, ?, ?)",
        (cut_id, start_ms, end_ms, speaker, text),
    )
    return cur.lastrowid


def clear_cut_dialogue(con: sqlite3.Connection, *, cut_id: int) -> int:
    """Drops a cut's dialogue so a re-commit replaces it instead of appending.

    Cuts upsert on (media_id, cut_index) and cut_tag dedupes on its primary
    key, so both survive a second commit unchanged. Dialogue has no natural
    key to collide on, so without this every line lands again - and the FTS
    triggers index the copy too, which is how one corrected description turns
    into duplicate search hits for the same cut.
    """
    cur = con.execute("DELETE FROM dialogue WHERE cut_id = ?", (cut_id,))
    return cur.rowcount


# ---------------------------------------------------------------------------
# tag / cut_tag
# ---------------------------------------------------------------------------

# time_of_day and condition exist so that "교실 야경" / "도심 오피스 거리 폐허"
# don't have to be minted as separate setting values. Folding a time or a
# state into the place name multiplies the setting vocabulary (one tag per
# place per time) and makes a search for the place itself miss the variants.
#
# character is separate from subject for the same reason, one level up: a
# person's name has a canonical spelling that a dropdown picks from, and an
# attribute ("긴 머리", "부츠") is free vocabulary that should keep growing.
# Measured before splitting them, subject held 91 values with both kinds
# mixed in - so enforcing a canonical list on names would have frozen the
# attributes too. See migration_008 and entity_catalog.db.
TAG_CATEGORIES = (
    "subject", "character", "action", "mood", "setting", "use_case",
    "time_of_day", "condition",
)


def get_or_create_tag(con: sqlite3.Connection, *, category: str, value: str) -> int:
    if category not in TAG_CATEGORIES:
        raise ValueError(f"category must be one of {TAG_CATEGORIES}")
    normalized = value.strip()
    row = con.execute(
        "SELECT tag_id FROM tag WHERE category = ? AND value = ?", (category, normalized)
    ).fetchone()
    if row is not None:
        return row["tag_id"]
    cur = con.execute(
        "INSERT INTO tag (category, value) VALUES (?, ?)", (category, normalized)
    )
    return cur.lastrowid


def resolve_tag_id(con: sqlite3.Connection, tag_id: int) -> int:
    """Follows canonical_tag_id chains so lookups always land on the
    representative tag, even if a synonym was merged after the fact."""
    seen = set()
    current = tag_id
    while current not in seen:
        seen.add(current)
        row = con.execute(
            "SELECT canonical_tag_id FROM tag WHERE tag_id = ?", (current,)
        ).fetchone()
        if row is None or row["canonical_tag_id"] is None:
            return current
        current = row["canonical_tag_id"]
    return current  # cycle guard; well-formed data never reaches this


def merge_tag_into(con: sqlite3.Connection, *, synonym_tag_id: int, canonical_tag_id: int) -> None:
    """Marks synonym_tag_id as an alias of canonical_tag_id. Existing
    cut_tag rows pointing at the synonym are left as-is; resolve_tag_id
    (or the search layer) follows the chain at read time."""
    if synonym_tag_id == canonical_tag_id:
        raise ValueError("a tag cannot be its own canonical")
    con.execute(
        "UPDATE tag SET canonical_tag_id = ? WHERE tag_id = ?",
        (canonical_tag_id, synonym_tag_id),
    )


def tag_cut(
    con: sqlite3.Connection, *, cut_id: int, tag_id: int, source: Optional[str] = None
) -> None:
    """Attaches a tag to a cut, optionally recording who proposed it.

    Passing no source keeps the original INSERT OR IGNORE behaviour, which
    matters: callers that do not know the provenance (the exemplar matcher,
    reverse image search) must not overwrite a recorded 'session' with the
    'pending' default just by touching the row again. A caller that does say
    where the tag came from updates it, so re-committing a corrected job
    carries the correction through.
    """
    if source is None:
        con.execute(
            "INSERT OR IGNORE INTO cut_tag (cut_id, tag_id) VALUES (?, ?)", (cut_id, tag_id)
        )
        return
    con.execute(
        "INSERT INTO cut_tag (cut_id, tag_id, source) VALUES (?, ?, ?) "
        "ON CONFLICT(cut_id, tag_id) DO UPDATE SET source = excluded.source",
        (cut_id, tag_id, source),
    )


def tag_cut_by_value(
    con: sqlite3.Connection, *, cut_id: int, category: str, value: str,
    source: Optional[str] = None,
) -> int:
    """Convenience wrapper: get-or-create the tag, then attach it to the cut."""
    tag_id = get_or_create_tag(con, category=category, value=value)
    tag_cut(con, cut_id=cut_id, tag_id=tag_id, source=source)
    return tag_id


def prune_ingest_cut_tags(con: sqlite3.Connection, *, cut_id: int, keep_tag_ids) -> int:
    """Drops tags a previous commit of this cut wrote and this one did not.

    Without it, removing a tag in staging and re-committing leaves the old row
    in source.db, still searchable - the same shape as the duplicate-dialogue
    bug, one table over. Staging already treats a cut's tags as one closed set
    that is replaced wholesale; this makes the committed copy agree.

    Only tags this pipeline authored are eligible, and cut_tag.source is what
    makes that answerable. The ingest path always records a source, while
    writers that do not say where a tag came from - the exemplar matcher,
    reverse image search - leave the 'pending' default. So a commit owns what
    it wrote and never reaches what they did, which is the difference between
    replacing your own tag set and clearing someone else's work.
    """
    keep = [int(t) for t in keep_tag_ids or []]
    sql = "DELETE FROM cut_tag WHERE cut_id = ? AND source <> 'pending'"
    params: list[Any] = [cut_id]
    if keep:
        sql += f" AND tag_id NOT IN ({','.join('?' for _ in keep)})"
        params.extend(keep)
    return con.execute(sql, params).rowcount


# ---------------------------------------------------------------------------
# read helpers
# ---------------------------------------------------------------------------

def list_media_file_hashes(con: sqlite3.Connection) -> set[str]:
    """All non-null file_hash values currently in media - used to diff a
    project's on-disk files against what's already registered without
    pulling full rows over just to check membership."""
    return {
        row["file_hash"]
        for row in con.execute("SELECT file_hash FROM media WHERE file_hash IS NOT NULL")
    }


def list_work_titles(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every work title already in source.db, most-used first.

    The point is not typing convenience. The same work entered two ways
    splits every later search between the spellings, silently - nothing
    reports it, the second entry just finds less. Offering what exists makes
    the existing spelling the path of least resistance, which is the only
    mechanism that reliably prevents this.

    Lives here rather than in the web app because both writers of work_title
    need it: the identity form in the browser, and whoever fills the field
    from outside the app. A second copy of this query is a second vocabulary.
    """
    rows = con.execute(
        """
        SELECT s.work_title AS title,
               -- The category of the biggest source row for this title, not
               -- one row per category. Grouping by both put the same work in
               -- the list twice (once 'game', once 'ugc'), which is precisely
               -- the split this exists to prevent - offering the ambiguity
               -- back guarantees it gets picked inconsistently.
               (SELECT s2.source_category FROM source s2
                LEFT JOIN media m2 ON m2.source_id = s2.source_id
                WHERE s2.work_title = s.work_title
                GROUP BY s2.source_id
                ORDER BY COUNT(m2.media_id) DESC LIMIT 1) AS category,
               COUNT(m.media_id) AS uses
        FROM source s
        LEFT JOIN media m ON m.source_id = s.source_id
        WHERE s.work_title IS NOT NULL AND TRIM(s.work_title) <> ''
        GROUP BY s.work_title
        ORDER BY uses DESC, s.work_title
        """
    ).fetchall()
    return [
        {"title": r["title"], "category": r["category"], "uses": r["uses"]}
        for r in rows
    ]


def get_cut_detail(con: sqlite3.Connection, cut_id: int) -> Optional[dict[str, Any]]:
    row = con.execute(
        """
        SELECT sc.cut_id, sc.media_id, sc.cut_index, sc.start_ms, sc.end_ms,
               sc.shot_type, sc.description_text,
               m.file_path, m.media_type,
               s.work_title, s.source_category, s.is_official
        FROM scene_cut sc
        JOIN media m ON m.media_id = sc.media_id
        JOIN source s ON s.source_id = m.source_id
        WHERE sc.cut_id = ?
        """,
        (cut_id,),
    ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["dialogue"] = [
        dict(r)
        for r in con.execute(
            "SELECT dialogue_id, start_ms, end_ms, speaker, text FROM dialogue WHERE cut_id = ?",
            (cut_id,),
        ).fetchall()
    ]
    result["tags"] = [
        dict(r)
        for r in con.execute(
            "SELECT t.tag_id, t.category, t.value FROM cut_tag ct "
            "JOIN tag t ON t.tag_id = ct.tag_id WHERE ct.cut_id = ?",
            (cut_id,),
        ).fetchall()
    ]
    return result
