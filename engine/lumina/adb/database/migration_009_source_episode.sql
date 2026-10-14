-- Migration 009: 출처에 편(arc)과 화수를 적습니다.
--
-- 왜 필요한가
-- -----------
-- 지금 source 가 담는 위치 정보는 work_title 하나뿐입니다. '원피스' 까지는
-- 적히는데 그 아래가 없어서, 컷을 다시 찾을 때 1,000편이 넘는 화 중
-- 어디였는지 알 방법이 없습니다.
--
-- 왜 숫자 두 칸이 아닌가
-- ----------------------
-- 처음에는 (시즌, 화수) 두 정수를 생각했습니다. 실제 작품을 대보니 깨집니다.
--
--   원피스 1089화              시즌이 없습니다 - 연속 넘버링입니다
--   블리치 천년혈전편 7화        '시즌' 이 숫자가 아니라 이름입니다
--   원피스 필름 레드            화수 자체가 없습니다
--
-- 그래서 편 이름은 텍스트(subtitle), 화수는 정수(episode)로 나눕니다. 둘 다
-- NULL 이 될 수 있고, 그게 정상인 경우가 실제로 있습니다.
--
-- 쿨(cour) 단위는 나누지 않습니다 - 보통 총화수로 세기 때문에, subtitle 은
-- 편 단위까지만 적습니다. 한 작품에 생기는 subtitle 값이 몇 개 안 되므로
-- 표기가 갈릴 여지도 그만큼 줄어듭니다.
--
-- 유니크 키를 왜 바꾸나
-- ---------------------
-- 기존 uq_source_work 는 (work_title, origin_ref) 입니다. 그대로 두면
-- 원피스 1089화와 1090화가 **같은 source 행으로 합쳐집니다** - 둘 다
-- work_title='원피스', origin_ref=NULL 이니까요.
--
-- COALESCE 를 쓰는 이유: sqlite 는 유니크 인덱스에서 NULL 을 서로 다른
-- 값으로 봅니다. 그대로 두면 (원피스, NULL, NULL) 행이 몇 개든 들어가
-- 백스톱 구실을 못 합니다.

ALTER TABLE source ADD COLUMN subtitle TEXT;
ALTER TABLE source ADD COLUMN episode INTEGER;

DROP INDEX IF EXISTS uq_source_work;

CREATE UNIQUE INDEX uq_source_work
  ON source(work_title, COALESCE(subtitle, ''), COALESCE(episode, -1));

CREATE INDEX IF NOT EXISTS idx_source_episode ON source(work_title, episode);
