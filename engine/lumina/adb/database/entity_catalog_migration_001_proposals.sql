-- entity_catalog.db 마이그레이션 001: 라벨 검수 큐.
--
-- 왜 큐가 필요한가
-- ----------------
-- 캐릭터 3,373명이 라틴 표기로 남아 있고, 남은 무료 경로는 로마자 음차뿐
-- 입니다. 음차기를 **검증된 한글명 110개에 대고 채점하면 94.5%** 가
-- 맞습니다. 문제는 틀리는 5.5% 가 하필 가장 많이 검색될 이름들이라는
-- 것입니다 - 측정된 실패는 다음과 같았습니다.
--
--     son_goku         음차 '손 고쿠'      실제 '손오공'
--     sanji            음차 '산지'         실제 '상디'
--     kujo_jotaro      음차 '쿠조 조타로'   실제 '쿠죠 죠타로'
--     kamado_tanjirou  음차 '카마도 탄지로우' 실제 '탄지로'
--
-- 위험도로 걸러낼 수 있는지 재봤지만 안 됩니다: 장음 포함 93.8%,
-- post_count>=2000 95.4%, 성+이름 92.6% - 전부 기준선 94.5% 와 같습니다.
-- 실패가 예측되지 않으므로 **자동 확정은 어떤 조건에서도 안전하지
-- 않습니다**. 그래서 쓰는 대신 제안하고, 사람이 고릅니다.
--
-- 왜 후보가 여러 개인가
-- ---------------------
-- 실패 절반은 오답이 아니라 **표기 관습의 갈래**였습니다. 장음을 적을
-- 것인가(탄지로우/탄지로), jo 를 조로 쓸 것인가 죠로 쓸 것인가, n 을
-- 받침으로 붙일 것인가(제니츠/젠이츠). 하나를 찍어 틀리느니 갈래를 모두
-- 내놓고 고르게 하는 편이 검수 속도도 빠릅니다 - 타이핑이 클릭이 됩니다.
--
-- 아무것도 제안이 맞지 않으면 custom 으로 직접 넣습니다. 그것도 아니면
-- reject - 라틴 표기가 유지되고 needs_label 도 1 로 남습니다. 거절은
-- 실패가 아니라 "이 이름은 음차로 얻을 수 없다" 는 기록입니다.

CREATE TABLE IF NOT EXISTS label_proposal (
  proposal_id   INTEGER PRIMARY KEY,
  entity_type   TEXT NOT NULL CHECK(entity_type IN ('character', 'work')),
  entity_id     INTEGER NOT NULL,

  -- 제안 당시의 라벨. 나중에 다른 경로로 라벨이 채워졌는데 큐가 그걸
  -- 모르고 덮어쓰는 일을 막기 위해, 수락 시점에 이 값과 실제 값을
  -- 대조합니다.
  current_label TEXT NOT NULL,

  -- 후보들. JSON 배열이고 순서가 곧 추천 순위입니다.
  candidates    TEXT NOT NULL,
  -- 무엇을 근거로 만들었는지. danbooru 태그와 분절 결과.
  evidence      TEXT,
  method        TEXT NOT NULL,          -- transliteration | ...

  status        TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending', 'accepted', 'rejected')),
  chosen        TEXT,                   -- 수락된 표기 (후보 중 하나이거나 직접 입력)
  decided_at    INTEGER,

  -- 검수 순서. 작품 인기 → 캐릭터 포스트 수. 실제로 쓸 인물부터 나옵니다.
  sort_rank     INTEGER NOT NULL DEFAULT 0,

  created_at    INTEGER NOT NULL DEFAULT (strftime('%s','now')*1000),
  UNIQUE(entity_type, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_proposal_status ON label_proposal(status, sort_rank);
CREATE INDEX IF NOT EXISTS idx_proposal_entity ON label_proposal(entity_type, entity_id);
