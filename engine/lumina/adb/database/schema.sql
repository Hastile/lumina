PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE source (
  source_id       INTEGER PRIMARY KEY,
  source_category TEXT NOT NULL,                         -- anime / live_action / official_art / game / stock / ugc
  is_official     INTEGER NOT NULL CHECK(is_official IN (0,1)),
  work_title      TEXT,
  origin_ref      TEXT,                                  -- 공식 URL 등
  -- 작품 아래의 위치 (migration_009). 작품명만으로는 '원피스' 까지밖에
  -- 못 적는데, 컷을 다시 찾으려면 몇 화인지가 필요합니다.
  --   subtitle : 편(arc) 이름. 숫자가 아니라 텍스트입니다 - '천년혈전편',
  --              '4기'. 쿨 단위로는 나누지 않습니다(보통 총화수로 셉니다).
  --   episode  : 총화수. 숫자라 표기가 갈릴 일이 없고 정렬이 됩니다.
  -- 둘 다 NULL 이 정상인 경우가 있습니다 - 극장판과 스톡 영상.
  subtitle        TEXT,
  episode         INTEGER,
  created_at      INTEGER NOT NULL DEFAULT (strftime('%s','now')*1000)
);
-- COALESCE 를 쓰는 이유: sqlite 는 유니크 인덱스에서 NULL 을 서로 다른
-- 값으로 봅니다. 빼면 (원피스, NULL, NULL) 행이 몇 개든 들어갑니다.
CREATE UNIQUE INDEX uq_source_work
  ON source(work_title, COALESCE(subtitle, ''), COALESCE(episode, -1));

CREATE TABLE media (
  media_id     INTEGER PRIMARY KEY,
  media_type   TEXT NOT NULL CHECK(media_type IN ('image','video')),
  file_path    TEXT NOT NULL,
  file_hash    TEXT UNIQUE,
  duration_ms  INTEGER,                                  -- image는 NULL
  fps          REAL,                                      -- image는 NULL
  source_id    INTEGER NOT NULL REFERENCES source(source_id) ON DELETE CASCADE,
  summary_text TEXT,                                      -- 임베딩 대상 요약
  created_at   INTEGER NOT NULL DEFAULT (strftime('%s','now')*1000),
  -- 같은 그림의 다른 포맷본(a.png / a.webp)은 해시가 달라 자동 중복제거가 안 되므로,
  -- tag.canonical_tag_id와 같은 방식으로 대표 행을 가리킨다 (migration_003).
  canonical_media_id INTEGER REFERENCES media(media_id),
  -- 'pending' = 설명만 있고 아직 실제 파일을 확보하지 못한 행. 검색에서 제외된다.
  availability TEXT NOT NULL DEFAULT 'available' CHECK(availability IN ('available','pending')),
  -- 'directory' = 낱개가 아니라 폴더 단위로 묶어 등록한 자산 번들. 파일로 열면 안 된다.
  path_kind    TEXT NOT NULL DEFAULT 'file' CHECK(path_kind IN ('file','directory')),
  -- 이 녹화가 본편의 어디서 시작하는지 (migration_010). 컷의 시각을
  -- 본편 타임코드로 되돌리는 기준이고, 통짜/스톡 영상에는 NULL 입니다.
  source_offset_ms INTEGER,
  -- 녹화 하나를 컷별 파일로 쪼개 내보냈을 때, 그 조각들이 원본과 순서를 잃지
  -- 않도록 붙잡아 두는 두 컬럼 (migration_004). 통짜 미디어는 둘 다 NULL.
  parent_media_id INTEGER REFERENCES media(media_id),
  sequence_index  INTEGER
);
CREATE INDEX idx_media_source ON media(source_id);
CREATE INDEX idx_media_canonical ON media(canonical_media_id);
CREATE INDEX idx_media_availability ON media(availability);
CREATE INDEX idx_media_parent ON media(parent_media_id, sequence_index);

CREATE TABLE scene_cut (
  cut_id           INTEGER PRIMARY KEY,
  media_id         INTEGER NOT NULL REFERENCES media(media_id) ON DELETE CASCADE,
  cut_index        INTEGER NOT NULL,                      -- 단일컷 영상/이미지는 0
  start_ms         INTEGER NOT NULL,
  end_ms           INTEGER NOT NULL,
  shot_type        TEXT,                                  -- close/medium/wide 등
  description_text TEXT NOT NULL,                          -- 임베딩 핵심 필드
  UNIQUE(media_id, cut_index)
);
CREATE INDEX idx_scene_cut_media ON scene_cut(media_id);

CREATE TABLE dialogue (
  dialogue_id INTEGER PRIMARY KEY,
  cut_id      INTEGER NOT NULL REFERENCES scene_cut(cut_id) ON DELETE CASCADE,
  start_ms    INTEGER,
  end_ms      INTEGER,
  speaker     TEXT,
  text        TEXT NOT NULL
);
CREATE INDEX idx_dialogue_cut ON dialogue(cut_id);

CREATE TABLE tag (
  tag_id           INTEGER PRIMARY KEY,
  category         TEXT NOT NULL,                         -- subject / action / mood / setting / use_case
  value            TEXT NOT NULL,
  canonical_tag_id INTEGER REFERENCES tag(tag_id),         -- 동의어를 대표 태그로 흡수
  UNIQUE(category, value)
);

CREATE TABLE cut_tag (
  cut_id INTEGER NOT NULL REFERENCES scene_cut(cut_id) ON DELETE CASCADE,
  tag_id INTEGER NOT NULL REFERENCES tag(tag_id) ON DELETE CASCADE,
  -- 이 태그를 누가 제안했는지 (migration_012). 설명문은 상류에
  -- description_source 가, 신원은 metadata_source 가 남기는데 태그만
  -- 없었습니다. 검색이 실제로 조인하는 값이라, 틀렸을 때 피해가 가장 큰
  -- 필드가 근거 기록이 없는 유일한 필드였습니다.
  --
  -- 'pending' 은 출처가 기록되지 않았다는 뜻입니다 - 이 컬럼 이전 행과,
  -- 출처를 말하지 않는 경로(exemplar matcher, 역이미지 검색)가 여기 들어
  -- 옵니다. 'human' 으로 두지 않는 이유는 아무도 검토하지 않은 값을
  -- 검토된 것처럼 만드는 게 정확히 이 컬럼이 막으려는 일이기 때문입니다.
  source TEXT NOT NULL DEFAULT 'pending'
         CHECK(source IN ('pending','session','human','model')),
  PRIMARY KEY(cut_id, tag_id)
);
CREATE INDEX idx_cuttag_tag ON cut_tag(tag_id, cut_id);

-- Full-text search (see migration_002_fts.sql for the standalone delta).
CREATE VIRTUAL TABLE cut_search USING fts5(
  description_text,
  content='scene_cut',
  content_rowid='cut_id'
);
CREATE TRIGGER scene_cut_ai AFTER INSERT ON scene_cut BEGIN
  INSERT INTO cut_search(rowid, description_text) VALUES (new.cut_id, new.description_text);
END;
CREATE TRIGGER scene_cut_ad AFTER DELETE ON scene_cut BEGIN
  INSERT INTO cut_search(cut_search, rowid, description_text) VALUES ('delete', old.cut_id, old.description_text);
END;
CREATE TRIGGER scene_cut_au AFTER UPDATE ON scene_cut BEGIN
  INSERT INTO cut_search(cut_search, rowid, description_text) VALUES ('delete', old.cut_id, old.description_text);
  INSERT INTO cut_search(rowid, description_text) VALUES (new.cut_id, new.description_text);
END;

CREATE VIRTUAL TABLE dialogue_search USING fts5(
  text,
  content='dialogue',
  content_rowid='dialogue_id'
);
CREATE TRIGGER dialogue_ai AFTER INSERT ON dialogue BEGIN
  INSERT INTO dialogue_search(rowid, text) VALUES (new.dialogue_id, new.text);
END;
CREATE TRIGGER dialogue_ad AFTER DELETE ON dialogue BEGIN
  INSERT INTO dialogue_search(dialogue_search, rowid, text) VALUES ('delete', old.dialogue_id, old.text);
END;
CREATE TRIGGER dialogue_au AFTER UPDATE ON dialogue BEGIN
  INSERT INTO dialogue_search(dialogue_search, rowid, text) VALUES ('delete', old.dialogue_id, old.text);
  INSERT INTO dialogue_search(rowid, text) VALUES (new.dialogue_id, new.text);
END;

-- Tag full-text search (see migration_005_tag_fts.sql for the standalone delta).
--
-- Why this was missing and why it matters: cut_search indexes
-- scene_cut.description_text and dialogue_search indexes dialogue.text, and
-- that was the whole of retrieval. Character names do not live in either -
-- they are attached as `subject` tags, by the WD tagger, by the booru hits
-- reverse search returns, and by the exemplar matcher. So a search for a
-- character matched only the cuts where the name happened to make it into
-- the prose, and missed every cut where it was recorded properly.
--
-- Unlike the other two, this cannot be an external-content table: tags reach
-- a cut through the cut_tag join, so there is no column on one row to point
-- at. It is a regular FTS5 table holding one document per cut - the tag
-- values joined - which also means plain DELETE works, and rebuilding one
-- cut's document on every tag change stays a two-statement trigger.
--
-- Duplication is real but tiny (tag text is short, and it is stored once per
-- cut rather than once per cut_tag row).

CREATE VIRTUAL TABLE tag_search USING fts5(tag_text);

-- Synonyms are indexed under both names. merge_tag_into deliberately leaves
-- cut_tag rows pointing at the synonym and resolves at read time, so
-- indexing only the stored value would make a search for the canonical name
-- miss every cut tagged before the merge - and a search for the synonym miss
-- everything tagged after it.
CREATE VIEW cut_tag_text AS
  SELECT ct.cut_id AS cut_id,
         group_concat(
           t.value || CASE
             WHEN c.value IS NOT NULL AND c.value <> t.value THEN ' ' || c.value
             ELSE ''
           END, ' ') AS tag_text
    FROM cut_tag ct
    JOIN tag t ON t.tag_id = ct.tag_id
    LEFT JOIN tag c ON c.tag_id = t.canonical_tag_id
   GROUP BY ct.cut_id;

CREATE TRIGGER cut_tag_ai AFTER INSERT ON cut_tag BEGIN
  DELETE FROM tag_search WHERE rowid = new.cut_id;
  INSERT INTO tag_search(rowid, tag_text)
    SELECT cut_id, tag_text FROM cut_tag_text WHERE cut_id = new.cut_id;
END;

CREATE TRIGGER cut_tag_ad AFTER DELETE ON cut_tag BEGIN
  DELETE FROM tag_search WHERE rowid = old.cut_id;
  INSERT INTO tag_search(rowid, tag_text)
    SELECT cut_id, tag_text FROM cut_tag_text WHERE cut_id = old.cut_id;
END;

-- A tag's value changing (or being merged into a canonical) rewrites every
-- cut carrying it, not just one.
CREATE TRIGGER tag_au AFTER UPDATE ON tag BEGIN
  DELETE FROM tag_search
   WHERE rowid IN (SELECT cut_id FROM cut_tag WHERE tag_id = new.tag_id);
  INSERT INTO tag_search(rowid, tag_text)
    SELECT cut_id, tag_text FROM cut_tag_text
     WHERE cut_id IN (SELECT cut_id FROM cut_tag WHERE tag_id = new.tag_id);
END;

-- Backfill for rows that predate the index.

-- Source full-text search (see migration_011_source_fts.sql for the delta).
--
-- Why this was missing and why it matters: cut_search, dialogue_search and
-- tag_search were the whole of retrieval, and none of them carry the work.
-- A cut from 이세계 콰르텟 could not be reached by searching "콰르텟" even
-- though the title sat on its source row. CLAUDE.md's shot-naming rule
-- ("Name the work, the character, the moment") assumes the work is part of
-- the query, so leaving it unindexed made the documented way of searching
-- the one way that could not work.
--
-- External content over `source` (source_id is the rowid). One source spans
-- many cuts, so the finder joins source -> media -> scene_cut and every cut
-- of a matching work becomes a candidate. Weak evidence on its own, and
-- deliberately so: the combined-evidence bonus is what lifts a cut matching
-- the work *and* something specific above one that only shares a title.

CREATE VIRTUAL TABLE source_search USING fts5(
  work_title,
  subtitle,
  content='source',
  content_rowid='source_id'
);

CREATE TRIGGER source_ai AFTER INSERT ON source BEGIN
  INSERT INTO source_search(rowid, work_title, subtitle)
    VALUES (new.source_id, new.work_title, new.subtitle);
END;
CREATE TRIGGER source_ad AFTER DELETE ON source BEGIN
  INSERT INTO source_search(source_search, rowid, work_title, subtitle)
    VALUES ('delete', old.source_id, old.work_title, old.subtitle);
END;
CREATE TRIGGER source_au AFTER UPDATE ON source BEGIN
  INSERT INTO source_search(source_search, rowid, work_title, subtitle)
    VALUES ('delete', old.source_id, old.work_title, old.subtitle);
  INSERT INTO source_search(rowid, work_title, subtitle)
    VALUES (new.source_id, new.work_title, new.subtitle);
END;
