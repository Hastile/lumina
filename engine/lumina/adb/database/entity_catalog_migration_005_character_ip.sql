-- entity_catalog.db 마이그레이션 005: 캐릭터와 IP 를 N:M 으로.
--
-- 왜 바꾸나
-- ---------
-- 지금까지 캐릭터는 IP 를 하나만 가질 수 있었습니다(character_entity.ip_id).
-- 그래서 여러 작품에 걸친 인물을 다루려면 IP 를 합치는 수밖에 없었고,
-- 실제로 Fate 계열 9개를 `페이트` 하나로 접었습니다. 그 결과 `Fate/Zero`
-- 와 `Fate/Grand Order` 가 드롭다운에서 사라졌는데, 둘은 제목이 크게 다른
-- 별개 작품입니다 - 인제스트에서 출처로 고를 수 있어야 하는 값입니다.
--
-- 반대로 `역전재판 2`, `도쿄 구울:re`, `드래곤볼 GT` 처럼 이름만 바뀐
-- 사실상의 후속 시즌은 접힌 채로 두는 게 맞습니다. 컷의 출처로 굳이
-- 구분할 이유가 없습니다.
--
-- 두 요구를 동시에 만족시키려면 소속이 여럿일 수 있어야 합니다. 한 작품에
-- 여러 인물이 나오고, 한 인물이 같은 작품군의 여러 작품에 나옵니다.
--
-- ip_id 를 왜 남기나
-- ------------------
-- character_entity.ip_id 는 **대표 소속**으로 남깁니다. 지우지 않는 이유는
-- 둘입니다.
--
--   * UNIQUE(ip_id, name_key) 가 거기 걸려 있습니다. 같은 작품 안에 같은
--     이름이 둘 생기는 것을 막아 온 장치이고, 접합 테이블로 옮기면
--     선언적으로는 강제할 수 없습니다.
--   * 드롭다운은 인물마다 "어디 소속인지" 한 줄을 보여 줘야 하는데,
--     소속이 여럿이면 그중 하나를 골라야 합니다. 그 선택을 매번 계산하는
--     대신 대표를 못박아 둡니다.
--
-- 그래서 규칙은: **ip_id 는 언제나 character_ip 에 is_primary=1 로 같은
-- 행이 있어야 한다.** 아래 backfill 이 그 상태로 시작하고, 이후로는
-- source_db 의 헬퍼가 둘을 함께 씁니다.

CREATE TABLE IF NOT EXISTS character_ip (
  character_id INTEGER NOT NULL REFERENCES character_entity(character_id) ON DELETE CASCADE,
  ip_id        INTEGER NOT NULL REFERENCES ip_entity(ip_id) ON DELETE CASCADE,
  -- 대표 소속인가. 인물당 정확히 하나가 1 이어야 합니다.
  is_primary   INTEGER NOT NULL DEFAULT 0 CHECK(is_primary IN (0,1)),
  -- 이 연결이 어디서 왔는지. backfill | consolidation | user | danbooru
  source       TEXT,
  created_at   INTEGER NOT NULL DEFAULT (strftime('%s','now')*1000),
  PRIMARY KEY (character_id, ip_id)
);

CREATE INDEX IF NOT EXISTS idx_character_ip_ip ON character_ip(ip_id);
CREATE INDEX IF NOT EXISTS idx_character_ip_primary ON character_ip(character_id, is_primary);

-- 현재 소속을 그대로 대표 연결로 옮겨 심습니다.
INSERT OR IGNORE INTO character_ip (character_id, ip_id, is_primary, source)
SELECT character_id, ip_id, 1, 'backfill'
  FROM character_entity
 WHERE ip_id IS NOT NULL;
