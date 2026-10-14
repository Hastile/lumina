-- entity_catalog.db 마이그레이션 004: 하위 작품을 최상위 IP 로 접습니다.
--
-- 왜 필요한가
-- -----------
-- danbooru 는 시리즈를 작품 단위로 쪼개 태그를 답니다. `fate/stay_night`,
-- `fate/grand_order`, `fate/extra` 가 각각 다른 copyright 태그이고, 빌드는
-- 그걸 그대로 IP 행으로 만들었습니다. 그 결과:
--
--   * 같은 서번트가 작품별로 다른 IP 밑에 흩어집니다. 실제로 페이트(387)
--     아래 119명, Fate/Grand Order(389) 아래 25명이 따로 있습니다.
--   * IP 드롭다운에 `Fate/EXTELLA`, `Fate/Zero` 처럼 캐릭터가 0명인
--     항목이 9개나 뜹니다.
--
-- 무엇을 신호로 쓰나
-- ------------------
-- **접두사만 겹치는 것은 쓰지 않습니다.** 실측하면 `몬스터 헌터 라이즈` ⊂
-- `몬스터`, `킹덤 하츠` ⊂ `킹덤`, `그랑블루 판타지` ⊂ `그랑블루` 가 전부
-- 걸리는데 셋 다 다른 작품입니다. 쓰는 것은 둘뿐입니다.
--
--   tag_path  danbooru 태그가 `부모/자식` 꼴이고 부모가 IP 로 존재
--   sequel    한글명이 부모 + 명시적 속편 표지(`:re`, ` 2`, `GT`, `슈퍼`)
--
-- 캐릭터는 부모로 옮기고, 옮기다 같은 이름이 부모에 이미 있으면 그건
-- 같은 인물이므로 canonical_character_id 로 접습니다(migration_003).

ALTER TABLE ip_entity
  ADD COLUMN canonical_ip_id INTEGER REFERENCES ip_entity(ip_id);

CREATE INDEX IF NOT EXISTS idx_ip_canonical ON ip_entity(canonical_ip_id);
