-- entity_catalog.db - 작품/캐릭터 정본 카탈로그 (공통)
--
-- 왜 별도 파일인가
-- ----------------
-- 이 카탈로그를 읽는 주체가 둘입니다. adb-mcp 은 인제스트 드롭다운과
-- source.db 의 work_title 을 여기에 묶고, character_trend 는 자기 크롤러가
-- 새로 발견한 이름을 여기에 되먹입니다. 어느 한쪽의 DB 안에 두면 다른 쪽이
-- 남의 앱 파일을 쓰게 되므로, 둘 다 바깥인 곳에 둡니다.
--
--   기본 경로: D:\Dev\shared\entity_catalog.db
--   덮어쓰기 : ENTITY_CATALOG_PATH
--
-- 무엇을 푸는 테이블인가
-- ----------------------
-- 실측된 문제 세 가지입니다.
--
--   1. source.work_title 이 자유 텍스트고 uq_source_work 가
--      (work_title, origin_ref) 라, 오타 한 글자가 조용히 새 source 행을
--      만듭니다. 실제로 '보물섬 (Treasure Island)' 이 is_official 0/1 로
--      두 행 있습니다.
--   2. WD 태거는 `alphonse_elric` 만 뱉고 한글명을 모릅니다.
--      trend.db 는 '강철의 연금술사' 를 알지만 그 태그를 모릅니다.
--      **두 쪽을 잇는 컬럼이 어디에도 없습니다.** danbooru_tag 가 그것입니다.
--   3. tag.category 7 축에 인물 자리가 없어 subject 안에 인물명과
--      속성(긴 머리, 부츠)이 섞여 있습니다.
--
-- 씨앗은 둘인데 세계관이 다릅니다 (실측)
-- --------------------------------------
--   trend.db      : AniList+Wikidata, 383 작품 / 2,874 캐릭터.
--                   작품 한글명 98.4%. manga·anime 시장 카탈로그.
--   ip_catalog.db : Danbooru 9.2M 포스트, 43,994 작품 / taggable 2,678.
--                   태거 출력과 직결되지만 캐릭터 한글명이 0.
--
-- 겹침은 작품 4.6% / 캐릭터 10.6% 뿐이고, 그 원인은 이름 정규화 실패가
-- 아니라 커버리지입니다 - trend.db 에 touhou·vocaloid·kancolle 가 아예
-- 없습니다. 그래서 둘을 합치는 게 아니라 **양쪽을 출처를 적어 나란히
-- 담고**, 겹치는 것만 한 행으로 병합합니다. provenance 가 그 기록입니다.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- 작품
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ip_entity (
  ip_id            INTEGER PRIMARY KEY,

  -- 드롭다운에 실제로 뜨는 라벨. 한글이 원칙이지만, 확보하지 못한 건
  -- 지어내지 않고 danbooru 태그를 그대로 두고 needs_label=1 을 세웁니다
  -- (CLAUDE.md: "없는 제목은 지어내지 않는다").
  canonical_name_ko  TEXT NOT NULL,
  name_key           TEXT NOT NULL UNIQUE,   -- 정규화 키. 중복 유입 방지용.

  title_native       TEXT,
  title_english      TEXT,

  -- 브리지. WD 태거 출력과 ip_catalog.db 조인에 쓰는 유일한 키.
  danbooru_tag       TEXT,
  -- trend.db ip_catalog.id 역참조. 그쪽이 갱신되면 따라갈 수 있게.
  anilist_ip_id      INTEGER,

  media_type         TEXT,      -- anime / manga / game / mixed
  origin_country     TEXT,
  release_status     TEXT,      -- finished / releasing / ...

  -- 정렬 축. trend.db 에서만 옵니다 - danbooru 전용 행은 NULL 이고,
  -- 그때는 danbooru_post_count 로 정렬합니다.
  popularity         INTEGER,
  recognition_score  REAL,
  official_kr        INTEGER,
  danbooru_post_count INTEGER,

  provenance         TEXT NOT NULL,  -- anilist | danbooru | both | user
  label_source       TEXT,           -- 한글 라벨이 어디서 왔는지
  bridge_basis       TEXT,           -- danbooru_tag 를 어떻게 맞췄는지

  needs_label        INTEGER NOT NULL DEFAULT 0 CHECK(needs_label IN (0,1)),
  is_active          INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),

  created_at         INTEGER NOT NULL DEFAULT (strftime('%s','now')*1000),
  updated_at         INTEGER NOT NULL DEFAULT (strftime('%s','now')*1000)
);

-- 부분 유니크: danbooru 태그는 한 작품에만 붙습니다. NULL 은 얼마든지.
CREATE UNIQUE INDEX IF NOT EXISTS uq_work_danbooru
  ON ip_entity(danbooru_tag) WHERE danbooru_tag IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_work_popularity ON ip_entity(popularity DESC);
CREATE INDEX IF NOT EXISTS idx_work_needs_label ON ip_entity(needs_label);
CREATE INDEX IF NOT EXISTS idx_work_provenance ON ip_entity(provenance);

-- ---------------------------------------------------------------------------
-- 캐릭터
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS character_entity (
  character_id       INTEGER PRIMARY KEY,
  ip_id            INTEGER REFERENCES ip_entity(ip_id) ON DELETE CASCADE,

  canonical_name_ko  TEXT NOT NULL,
  name_key           TEXT NOT NULL,

  name_native        TEXT,
  name_english       TEXT,

  danbooru_tag       TEXT,
  -- WD 태거가 실제로 이 이름을 뱉을 수 있는가. 자동 식별 경로가 닿는
  -- 범위와 드롭다운에만 있는 범위를 구분하기 위해서.
  taggable           INTEGER NOT NULL DEFAULT 0 CHECK(taggable IN (0,1)),

  popularity         INTEGER,
  danbooru_post_count INTEGER,

  provenance         TEXT NOT NULL,
  label_source       TEXT,
  bridge_basis       TEXT,

  needs_label        INTEGER NOT NULL DEFAULT 0 CHECK(needs_label IN (0,1)),
  is_active          INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),

  created_at         INTEGER NOT NULL DEFAULT (strftime('%s','now')*1000),
  updated_at         INTEGER NOT NULL DEFAULT (strftime('%s','now')*1000),

  -- trend.db 의 uq_ip_character_name 과 같은 규칙. 같은 이름이 다른
  -- 작품에 있는 건 정상(동명이인)이고, 같은 작품 안 중복만 막습니다.
  UNIQUE(ip_id, name_key)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_character_danbooru
  ON character_entity(danbooru_tag) WHERE danbooru_tag IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_character_work ON character_entity(ip_id);
CREATE INDEX IF NOT EXISTS idx_character_taggable ON character_entity(taggable);
CREATE INDEX IF NOT EXISTS idx_character_needs_label ON character_entity(needs_label);

-- ---------------------------------------------------------------------------
-- 별칭
-- ---------------------------------------------------------------------------
--
-- trend.db 의 ip_aliases 모양을 그대로 씁니다. alias_key 정규화 규칙도
-- 같게 맞춰서(공백·구두점 제거, casefold, NFKC) 두 DB 의 키가 서로
-- 교환 가능합니다.
--
-- 이 테이블이 드롭다운의 검색 입력을 받습니다: 사용자가 '진격거'든
-- 'AoT'든 'Shingeki no Kyojin'이든 쳐도 같은 ip_id 로 떨어집니다.

CREATE TABLE IF NOT EXISTS entity_alias (
  alias_id     INTEGER PRIMARY KEY,
  entity_type  TEXT NOT NULL CHECK(entity_type IN ('ip','character')),
  entity_id    INTEGER NOT NULL,
  alias        TEXT NOT NULL,
  alias_key    TEXT NOT NULL,
  language     TEXT,
  alias_type   TEXT,            -- canonical | alternative | danbooru_tag
  source       TEXT,
  confidence   REAL NOT NULL DEFAULT 1.0,
  is_active    INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
  created_at   INTEGER NOT NULL DEFAULT (strftime('%s','now')*1000),
  UNIQUE(entity_type, entity_id, alias_key)
);

CREATE INDEX IF NOT EXISTS idx_alias_key ON entity_alias(alias_key);
CREATE INDEX IF NOT EXISTS idx_alias_entity ON entity_alias(entity_type, entity_id);

-- ---------------------------------------------------------------------------
-- 빌드 기록
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
