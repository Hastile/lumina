-- Migration 008: 작품/캐릭터를 정본 카탈로그에 묶습니다.
--
-- 무엇이 문제였나 (실측)
-- ----------------------
-- source.work_title 은 자유 텍스트고 uq_source_work 가 (work_title,
-- origin_ref) 라, 한 글자 차이가 조용히 새 source 행을 만듭니다. 실제로
-- 있었던 것:
--
--   source.db : '슈퍼 뒤에서 담배 피는 두 사람'
--   카탈로그   : '슈퍼 뒤에서 담배 피우는 두 사람'
--
-- 둘은 같은 작품인데 어떤 쿼리도 그렇게 보지 않습니다. 검색은 조용히 반으로
-- 갈리고, 갈렸다는 사실을 알려주는 곳이 없습니다.
--
-- 왜 진짜 FOREIGN KEY 가 아닌가
-- ------------------------------
-- 정본 카탈로그는 다른 파일(D:\Dev\shared\entity_catalog.db)에 있습니다.
-- adb-mcp 와 character_trend 가 함께 읽어야 해서 그렇게 뒀고, SQLite 의
-- 외래키는 ATTACH 를 해도 DB 를 건너가지 못합니다. 그래서 ip_entity_id 는
-- **약한 참조**입니다 - 무결성은 source_db.link_source_to_entity() 가
-- 쓰기 시점에 확인하고, 카탈로그가 없는 환경에서는 그냥 NULL 로 남습니다.
--
-- NULL 이 정상인 경우
-- -------------------
-- 카탈로그는 IP 카탈로그입니다. 스톡 영상('왕관앵무 스톡 사진'), 유물
-- 사진('이집트 파세두 무덤 벽화'), 실제 동물('터키시 반 고양이') 은 작품이
-- 아니므로 해소될 것이 없고, 해소되지 않는 게 맞습니다. 실측: 23행 중
-- 10행이 이 부류입니다. needs_entity 로 "아직 안 묶음" 과 "묶을 게 없음" 을
-- 구분하지 않는 이유가 이것 - 구분해봐야 판단은 사람이 합니다.

ALTER TABLE source ADD COLUMN ip_entity_id INTEGER;

CREATE INDEX IF NOT EXISTS idx_source_ip_entity ON source(ip_entity_id);

-- tag.category 에 'character' 축을 추가합니다.
--
-- 스키마상으로는 category 가 제약 없는 TEXT 라 ALTER 가 필요 없지만, 축
-- 목록은 코드 두 곳(mcp/source_db.py 의 TAG_CATEGORIES, webapp/tag_rules.py)
-- 에 박혀 있으므로 그 둘도 같이 고쳐야 이 마이그레이션이 완결됩니다.
--
-- 왜 필요한가: subject 축 91개 안에 인물명(아로나, 스나오오카미 시로코)과
-- 속성(긴 머리, 부츠, 보라 눈)이 섞여 있었습니다. 둘은 검색에서 하는 일이
-- 다릅니다 - 인물명은 정본이 있어야 하고 드롭다운으로 고르는 값이지만,
-- 속성은 자유롭게 늘어나도 되는 값입니다. 한 축에 두면 인물명에 정본을
-- 강제하는 순간 속성까지 같이 막힙니다.
--
-- 기존 행은 tag.category 를 UPDATE 하는 것으로 옮깁니다. cut_tag 는
-- tag_id 를 가리키므로 따라옵니다 - 어떤 컷도 태그를 잃지 않습니다.
