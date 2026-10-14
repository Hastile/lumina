-- entity_catalog.db 마이그레이션 003: 같은 인물의 중복 행을 잇습니다.
--
-- 왜 필요한가
-- -----------
-- 카탈로그는 씨앗을 둘에서 받습니다. danbooru 쪽은 태그(`hange_zoe`)를,
-- AniList 쪽은 한글명(`한지 조에`)을 가져오는데, 이름 형태가 달라 빌드
-- 당시 브리지에 실패하면 **같은 인물이 두 행으로 남습니다.** 진격의 거인
-- 에서 실제로 넷이 나왔습니다.
--
--     4038 Levi        (levi_(shingeki_no_kyojin))  ↔  2095 리바이
--     3493 Hange Zoe   (hange_zoe)                  ↔  2496 한지 조에
--      250 Krista Lenz (krista_lenz)                ↔  2501 히스토리아 레이스
--      275 Zeke                                     ↔  2492 지크 예거
--
-- 크리스타/히스토리아는 브리지 실패가 아니라 **한 인물의 두 이름**이라,
-- 이름을 맞춰도 합쳐지지 않는 종류입니다. 어느 쪽이든 드롭다운에 같은
-- 사람이 두 줄로 뜨고, 더 나쁘게는 절반의 컷이 한쪽에 절반이 다른 쪽에
-- 붙습니다.
--
-- 왜 지우지 않는가
-- ----------------
-- 중복 행이 danbooru_tag 를 들고 있는 쪽인 경우가 많습니다. 지우면 태거가
-- `krista_lenz` 를 뱉었을 때 닿을 곳이 사라집니다. 그래서 source.db 가
-- 같은 문제를 푸는 방식(`media.canonical_media_id`, `tag.canonical_tag_id`)
-- 을 그대로 씁니다 - 행은 남고, 대표를 가리키고, 읽는 쪽이 체인을
-- 따라갑니다. is_active=0 이라 드롭다운에는 한 줄만 뜹니다.

ALTER TABLE character_entity
  ADD COLUMN canonical_character_id INTEGER REFERENCES character_entity(character_id);

CREATE INDEX IF NOT EXISTS idx_character_canonical
  ON character_entity(canonical_character_id);
