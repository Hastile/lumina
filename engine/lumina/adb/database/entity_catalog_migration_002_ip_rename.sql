-- entity_catalog.db 마이그레이션 002: work_entity -> ip_entity.
--
-- 왜 이름을 바꾸나
-- ----------------
-- 이 테이블이 담는 것은 "작품" 하나가 아니라 **IP** 입니다. 애니·만화·게임
-- 시리즈가 한 행이고, 스톡 영상이나 실제 인물은 애초에 들어오지 않습니다.
-- 씨앗을 준 `character_trend/trend.db` 도 같은 것을 `ip_catalog` / `ip_id`
-- 라고 부르므로, 이름을 맞추면 두 DB 를 오갈 때 매핑을 외울 필요가
-- 없어집니다.
--
-- 무엇이 바뀌나
-- -------------
--   work_entity              -> ip_entity
--   work_entity.work_id      -> ip_entity.ip_id
--   character_entity.work_id -> character_entity.ip_id
--   entity_alias.entity_type  'work' -> 'ip'
--   label_proposal.entity_type CHECK 의 'work' -> 'ip'
--   인덱스 이름의 work_ 접두사 -> ip_
--
-- entity_alias 와 label_proposal 은 CHECK 제약 안에 'work' 리터럴이 들어
-- 있습니다. SQLite 는 CHECK 를 ALTER 로 고치지 못하므로 두 테이블만
-- 새로 만들어 옮깁니다. 나머지는 RENAME 으로 끝납니다.

PRAGMA foreign_keys = OFF;

-- ---------------------------------------------------------------------------
-- 1. 테이블/컬럼 이름
-- ---------------------------------------------------------------------------

ALTER TABLE work_entity RENAME TO ip_entity;
ALTER TABLE ip_entity RENAME COLUMN work_id TO ip_id;
ALTER TABLE character_entity RENAME COLUMN work_id TO ip_id;

-- ---------------------------------------------------------------------------
-- 2. 인덱스 (이름만 바꾸는 방법이 없어 다시 만듭니다)
-- ---------------------------------------------------------------------------

DROP INDEX IF EXISTS uq_work_danbooru;
DROP INDEX IF EXISTS idx_work_popularity;
DROP INDEX IF EXISTS idx_work_needs_label;
DROP INDEX IF EXISTS idx_work_provenance;
DROP INDEX IF EXISTS idx_character_work;

CREATE UNIQUE INDEX IF NOT EXISTS uq_ip_danbooru
  ON ip_entity(danbooru_tag) WHERE danbooru_tag IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ip_popularity ON ip_entity(popularity DESC);
CREATE INDEX IF NOT EXISTS idx_ip_needs_label ON ip_entity(needs_label);
CREATE INDEX IF NOT EXISTS idx_ip_provenance ON ip_entity(provenance);
CREATE INDEX IF NOT EXISTS idx_character_ip ON character_entity(ip_id);

-- ---------------------------------------------------------------------------
-- 3. entity_alias - CHECK 안의 'work' 때문에 재작성
-- ---------------------------------------------------------------------------

CREATE TABLE entity_alias_new (
  alias_id     INTEGER PRIMARY KEY,
  entity_type  TEXT NOT NULL CHECK(entity_type IN ('ip', 'character')),
  entity_id    INTEGER NOT NULL,
  alias        TEXT NOT NULL,
  alias_key    TEXT NOT NULL,
  language     TEXT,
  alias_type   TEXT,
  source       TEXT,
  confidence   REAL NOT NULL DEFAULT 1.0,
  is_active    INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
  created_at   INTEGER NOT NULL DEFAULT (strftime('%s','now')*1000),
  UNIQUE(entity_type, entity_id, alias_key)
);

INSERT INTO entity_alias_new
  (alias_id, entity_type, entity_id, alias, alias_key, language, alias_type,
   source, confidence, is_active, created_at)
SELECT alias_id,
       CASE entity_type WHEN 'work' THEN 'ip' ELSE entity_type END,
       entity_id, alias, alias_key, language, alias_type,
       source, confidence, is_active, created_at
  FROM entity_alias;

DROP TABLE entity_alias;
ALTER TABLE entity_alias_new RENAME TO entity_alias;

CREATE INDEX IF NOT EXISTS idx_alias_key ON entity_alias(alias_key);
CREATE INDEX IF NOT EXISTS idx_alias_entity ON entity_alias(entity_type, entity_id);

-- ---------------------------------------------------------------------------
-- 4. label_proposal - 같은 이유로 재작성
-- ---------------------------------------------------------------------------

CREATE TABLE label_proposal_new (
  proposal_id   INTEGER PRIMARY KEY,
  entity_type   TEXT NOT NULL CHECK(entity_type IN ('character', 'ip')),
  entity_id     INTEGER NOT NULL,
  current_label TEXT NOT NULL,
  candidates    TEXT NOT NULL,
  evidence      TEXT,
  method        TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending', 'accepted', 'rejected')),
  chosen        TEXT,
  decided_at    INTEGER,
  sort_rank     INTEGER NOT NULL DEFAULT 0,
  created_at    INTEGER NOT NULL DEFAULT (strftime('%s','now')*1000),
  UNIQUE(entity_type, entity_id)
);

INSERT INTO label_proposal_new
  (proposal_id, entity_type, entity_id, current_label, candidates, evidence,
   method, status, chosen, decided_at, sort_rank, created_at)
SELECT proposal_id,
       CASE entity_type WHEN 'work' THEN 'ip' ELSE entity_type END,
       entity_id, current_label, candidates, evidence,
       method, status, chosen, decided_at, sort_rank, created_at
  FROM label_proposal;

DROP TABLE label_proposal;
ALTER TABLE label_proposal_new RENAME TO label_proposal;

CREATE INDEX IF NOT EXISTS idx_proposal_status ON label_proposal(status, sort_rank);
CREATE INDEX IF NOT EXISTS idx_proposal_entity ON label_proposal(entity_type, entity_id);

PRAGMA foreign_keys = ON;
