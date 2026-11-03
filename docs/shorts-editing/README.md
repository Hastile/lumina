# Caddynote Shorts 편집 지침 (Codex / Claude 공용)

최신 공통 기준: [자막 완료 기준](../editing-grammar/caption-quality.md). 초반 공백·끝 문장부호·긴 자막 후처리를 포함하며 아래 과거 관찰보다 우선한다. Phrase 관련 구현 언급은 과거 이력이며 현행 도구는 Lumina 내부 speech다.

## 2026-10-30 공통 컷 문법 추가

먼저 [공용 편집 문법](../editing-grammar/README.md)과 [공통 컷 문법](../editing-grammar/common-cuts.md)을 읽는다. 공통 컷 문법은 쇼츠에도 적용하며, 자막 밀도·화면 배치·사용자 확정 수치는 아래 쇼츠 문서에서 가져온다. 새 공통 문법의 기준 원본은 이 저장소이며 모델별 메모리에 별도 규칙을 복제하지 않는다. 아래의 'Claude 메모리 원본' 설명은 기존 쇼츠 문서의 이관 경위다. 관찰 통계를 기계적인 컷 간격으로 적용하지 않는다.

이 폴더의 `*.md`는 Claude Code 메모리(`C:\Users\HETX\.claude\projects\D--Dev-lumina\memory\`)에서
2026-10-23에 복사한 것이다. 원본은 Claude 메모리이고, 규칙이 바뀌면 그쪽이 먼저 갱신된다.
여기 내용과 사용자의 지시가 다르면 사용자의 지시를 따른다.

## 누가 보는가

`caddynote-audience.md` 요약 (2026-10-24 Studio 지표와 댓글):

- 쇼츠는 피드를 넘기다 걸린 20~30대 서브컬처 시청자(남성 76%, 비구독 99%)가 **한 번, 순서대로** 본다. 그 작품을 본 팬과 처음 보는 사람이 섞여 있다.
- 넘길지는 첫 1~2초에 정한다(계속 시청 1년 평균 71%). 남은 사람은 거의 끝까지 본다(평균 조회율 중앙값 95%).
- 그래서 편집에서는 두 가지를 지킨다.
  - 첫 컷과 타이틀 카피가 영상이 무엇을 보라는 건지 바로 보여준다.
  - 내레이션이 가리키는 것은 그 순간 화면에 있다(쇼츠 대본 지침 13장, `shorts-edit-review-lessons.md`의 '샷은 자막을 걸친다').
- 계속 시청 상위·하위 10편의 첫 화면을 비교하니 그림은 비슷했고 타이틀 카피와 첫 자막이 갈랐다: 하위는 작중 기술·용어(경화수월, 적혈조술)로 열었다. 타이틀은 기술 이름보다 캐릭터의 모습·어긋남을 건다.

## 먼저 읽을 파일

| 파일 | 내용 |
|---|---|
| caddynote-audience.md | 누가 보는가: 지표, 댓글, 쓸 때 떠올릴 사람 |
| shorts-edit-review-lessons.md | 사용자가 직접 고친 것들. 편집을 보여주기 전에 이 목록으로 점검 |
| shorts-cut-grammar.md | 컷 길이, 프레이밍(115/130%, y 1060), 장면 전환 프레임 금지, 끝 처리 |
| shorts-caption-effect-grammar.md | 자막 효과 pop / slam / fade, 120 크기, TMI 배치 |
| shorts-sfx-grammar.md | 효과음 선택, 트랙, 음량, BGM |
| shorts-tmi-grammar.md | TMI에 넣을 것과 형식 |
| shorts-copy-grammar.md | 쇼츠 타이틀(sub/main) |
| shorts-project-per-folder.md, move-sources-not-copy.md | 폴더·프로젝트 규칙 |
| shorts-automation-pipeline.md | 전체 파이프라인, 리미터 값, Phrase Studio, 소스 DB |
| lumina-mcp-visible-work.md, lumina-replace-keep-neighbors.md | Lumina MCP 작업 방식 |

## 작업 순서 (탈리아 양 쇼츠, 2026-10-23 기준)

### 1. 내레이션과 자막
1. ElevenLabs 테이크(mp3)를 작품 폴더 아래 쇼츠 폴더로 **이동**한다. 예: `…\사이버펑크\엣지러너 2\탈리아 양\`.
2. Hard Limiter를 걸어 `<쇼츠 제목>.wav`를 만든다.
   `ffmpeg -i take.mp3 -af "volume=-0.46dB,alimiter=limit=0.630957:attack=7:release=200:level=false:latency=true" -c:a pcm_s16le -ar 44100 "<제목>.wav"`
3. Phrase Studio로 SRT를 만든다. 서버는 `D:\Dev\phrase-studio\.venv\Scripts\python.exe -m uvicorn server:app --host 127.0.0.1 --port 8765`로 띄운다.
   - `POST /api/projects`: wav를 multipart로 올린다.
   - `POST /api/projects/{id}/analyze`: `{"script", "language":"ko", "precise":true, "shorts":true}`
   - `GET /api/jobs/{id}`: 작업이 끝날 때까지 확인한다.
   - `GET /api/projects/{id}`: `segments`를 받아 `<제목>.srt`로 저장한다.
   - 대본에서 오디오 태그와 괄호(TMI) 부분은 빼고 넘긴다.
4. 대본 최신본(TMI 괄호 포함, 오디오 태그 제외)을 Obsidian에 저장한다. 최근 쇼츠는 기록이 자주 빠졌다.
   `python -I %USERPROFILE%\.claude\skills\caddynote-script\obsidian.py save script.txt --work <작품> --sub "<하위 폴더>" --name "<쇼츠 제목>"`
   - 위치: `D:\Obsidian\Caddynote\<작품>\<하위 폴더>\(shorts) <쇼츠 제목>.md`. 이름은 wav·SRT 파일명과 같게 둔다.
   - 편집 중에 내레이션이 바뀌면 같은 명령으로 다시 저장한다(이전 본은 볼트 `.trash`로 간다).

### 2. Lumina 세팅
- 작품 폴더의 프로젝트 하나에 넣는다(예: `엣지러너 2.lumina`). 쇼츠마다 프로젝트를 만들지 않는다.
- `시퀀스 02 (쇼츠 제목 폴더명 (복사해서 사용))`를 복제하고 이름을 `시퀀스 02 (<쇼츠 제목>)`으로 바꾼다.
- 트랙 배치:
  - wav: A1, 0프레임
  - SRT: V7 `자막 - 커졌다 줄어들기`
  - 타이틀: `shorts_set_copy`로 sub/main을 넣는다. 먼저 `dry_run`으로 화면 안에 들어가는지 확인한다.

### 3. 소스 찾기
- **먼저 쇼츠 폴더를 DB에 넣는다.** 손으로 옮기거나 `scdet`·컨택트시트로 새 녹화를 훑지 않는다.
  `cd D:\Dev\lumina\engine; uv run python -m lumina.sourcedb intake "<쇼츠 폴더>" --dry-run`
  - OBS 폴더(`C:\Users\HETX\Videos`) 바로 아래의 녹화를 쇼츠 폴더로 **이동**하고, 폴더 안에서 DB에 없거나 옛 버전으로 잘린 파일을 자른다.
  - 그다음 태거, Whisper, SigLIP을 돌리고, 설명이 없는 컷은 Claude가 설명을 쓴다. 비용은 컷당 약 $0.004이고, `--max-cost`(기본 $2)를 넘으면 설명은 건너뛴다.
  - OBS 시각 이름(`2026-10-25 13-10-38`)은 첫 대사로 바꾼다. 사용자가 붙인 이름은 그대로 둔다.
  - dry-run 목록에 다른 쇼츠의 녹화가 섞여 있으면 파일을 지정한다: `intake "<폴더>" "C:\Users\HETX\Videos\E06 1656 ….mp4" …`
  - 끝나면 새 컷이 한 줄씩(`#cut_id 작품 구간 샷 인물 "설명"`) 출력된다. 이 목록으로 바로 고른다. 폴더 하나에 수 분 걸리므로 백그라운드로 돌린다.
- **컷 계획표(4단계 1~2)를 먼저 쓰고, 계획 전체를 `source_plan` 한 번으로 찾는다.** 자막마다 `source_search`를 부르지 않는다.
  - 샷마다 `text`(덮는 내레이션), `look`(화면에 보여야 할 것을 한국어 그림 묘사로: 누가, 무엇을, 샷 크기), `seconds`(샷 길이)를 준다. 작품은 `work`, 인물은 `character`로 거른다.
  - `look`이 핵심이다. 그림 검색(SigLIP)은 설명이 없는 컷도 찾는다. 묘사를 쿼리로 쓰면 정답 컷이 상위 8개에 77% 들고, 내레이션을 그대로 넣으면 7%다.
  - 87% 속도로도 샷 길이를 못 채우는 컷은 빠진다. DB에 없는 인물 이름은 필터 대신 검색어로 쓰고 결과 첫 줄에 그렇게 알린다.
  - 이미 놓은 컷은 `exclude_cuts`로 뺀다. `(also S3)`는 다른 샷에도 후보로 나온 컷이다.
  - 첫 호출은 그림 인코더를 띄우느라 15초쯤 걸리고, 그다음부터는 1초 안팎이다.
- 마지막 후보만 `sheet_top`(샷마다 앞 N개) 또는 `source_sheet`로 눈으로 확인한다. 개별 보충 검색은 `source_search`.
- DB 설명은 틀릴 수 있다. 실제 파일과 구간은 직접 확인한다.
  - 예: DB의 "OT 65.9–67.1 총 겨눔"은 실제로는 Teaser #2의 66.40–66.94였다.
- 영상 하나를 훑을 때는 `contact_sheet.py`를 쓴다.
  `python contact_sheet.py <video> <start> <end> <step> <out.jpg> [cols] [width]`
- 장면 전환 시각은 `ffmpeg -i <video> -vf scdet=threshold=12 -an -f null -`로 뽑는다(`lavfi.scd.time`).
  - 컷 in은 장면 전환 직후, out은 다음 전환 직전으로 잡는다.
  - 다른 장면이 한 프레임이라도 끼면 안 된다.
- 트레일러에 없는 장면은 사용자가 OBS로 녹화한 본편 클립(예: `E10 2626 살아가자.mp4`)에서 찾는다.
  - 어느 화의 어느 구간을 녹화했는지는 프로젝트 폴더의 `소스 커버리지.html`에서 본다. 클립을 추가했거나 "소스 커버리지 최신화" 요청을 받으면 저장소 루트에서 다시 만든다.
    `python tools/source_coverage.py "<프로젝트 폴더>" --episodes <화 수> --episode-min <화 길이(분)>`
    (엣지러너 2: `--episodes 10 --episode-min 26`). claude.ai의 「엣지러너 2 소스 커버리지」 아티팩트는 Claude만 갱신할 수 있다.
- 끝내 없으면 "구하지 못한 소스"로 사용자에게 알린다.

### 4~6. 배치·자막 효과·효과음을 한 번에: `shorts_build`
- 순서: `shorts_plan`(초안) → 고치기 → `source_plan`(샷마다 `look`, 한 번) → **`shorts_build`** → 결과의 check에 나온 것만 고치기 → `get_frame_strip` 한 장 → 저장.
- `shorts_build`는 한 명령, 한 번의 되돌리기로 다음을 한다: `shots`를 V1에 배치(place_shots 규칙) → 영상 끝을 내레이션 + 6프레임에 맞춤(V1·V5·V6·마지막 자막을 늘이거나 자르고, 넘치는 소리는 자름) → 자막 효과(`effects`를 안 주면 초안대로) → 효과음(`sounds`를 안 주면 초안대로; `hit?`은 `hits_to_choose`로 돌려주니 뜻을 보고 `shorts_place_sfx`로 추가) → `shorts_check` 결과를 돌려준다.
- 하나라도 거부되면(짧은 소스, 없는 효과음 이름 등) 아무것도 바뀌지 않는다.
- **눈으로 컷마다 보지 않는다.** `shorts_check`가 글로 문제만 돌려준다: 1.1초 미만 샷, 검은 틈, 자막 시작이 아닌 컷, 87–105% 밖 속도, 장면 경계 3프레임 안쪽(이웃 샷이 비침, 소스 DB), 샷 안의 장면 전환, 어두운 샷, 끝 처리, 겹친 자막, 810px를 넘는 120 자막, SFX 태그 누락, 0.5초 안쪽 효과음. 그다음 `get_frame_strip` 한 장으로 그림만 확인한다.
- 초안 규칙을 바꾸면 PC에서 아카이브와 비교해 F1이 오를 때만 유지한다(토큰 0): `uv run --project engine python docs/shorts-editing/calibrate_plan.py cuts.jsonl` (`cuts.jsonl`은 `cut_grammar_extract.py`로 만든다).

### 4. 배치 (V1)
- **계획 초안은 `shorts_plan` 한 번으로 받는다.** V7 자막(또는 `srt`)을 읽어 아래 규칙대로 샷 묶음, 자막마다 효과(pop/slam/fade)와 효과음 제안, 규칙 위반 목록을 한 표로 돌려준다. 머릿속으로 자막을 하나씩 묶지 않는다.
  - 초안은 자막 어미만 보고 만든 것이다. 의미로 판단할 곳(이름 공개·주어 전환은 `reveal`에 자막 번호, 타격음 `hit?`은 뜻으로 고르기)만 고친다.
  - 고친 샷 목록을 그대로 `source_plan`(샷마다 `look` 추가)과 `clip_place_shots`에 넘긴다.
- **놓기 전에 컷 계획표부터 쓴다** (`shorts-cut-grammar.md`의 'Plan the cuts before placing anything'). 오미·사스케 닮은꼴 두 편 연속 "컷 배분 더럽게"로 고쳐졌다.
  1. 자막을 시작 프레임과 함께 나열하고 **문장 단위로** 샷을 묶는다(아카이브 180편, 자막 경계 5,590개 측정).
     - 문장 끝(마지막 자막 fade)에서는 바꾼다(83~96%). 샷이 0.7초도 안 됐으면 유지한다.
     - 문장 안에서는 유지한다(19%). 샷이 3초를 넘어도 같다. 문장이 길면 샷도 길다.
     - 문장 안에서 바꾸는 곳은 셋뿐이다.
       - 샷이 1초 이상 된 절 끝(-고/-지만/-는데/-면): 41~53%
       - 나열 항목이 바뀔 때(와/나 뒤): 31%
       - 이름 공개나 주어 전환처럼 새로 보여줄 대상이 나올 때
     - 문장 꼬리(fade) 직전에는 자르지 않는다(10%). 꾸밈말과 머리말 사이, 목적어와 동사 사이도 자르지 않는다(12~14%).
     - 0.5초 미만 도입 자막("그리고", "아예", "심지어")은 다음 자막과 한 샷이다.
  2. 다음 중 하나라도 걸리면 다시 묶는다.
     - 1.1초 미만 샷이 있다.
     - 자막 시작이 아닌 곳에 컷이 있다.
     - 한 문장의 샷 수가 절 수보다 많다.
     - 1자막 샷이 연달아 나온다.
  3. 소스는 그다음에 고른다. 소스 구간에 흰 플래시, 디졸브, 튐 같은 흠이 있으면 다른 구간이나 다른 소스로 바꾼다. 늘리거나 잘게 잘라 가리지 않는다.
  4. 놓은 뒤 V1 샷 길이를 계획표와 대조하고 나서 보여준다.
- **배치는 `clip_place_shots` 한 번으로 한다.** 샷마다 `clip_place`, `clip_set_speed`, `clip_set_property`를 따로 부르지 않는다.
  - 샷마다 `at`, `end`(다음 컷 지점), 그리고 `cut_id`(source_plan 결과) / `media_id` / `path` 중 하나를 준다. 정지 이미지도 `end`로 길이가 정해진다.
  - 자동 규칙:
    - 장면 경계 0.3초 안쪽에서 시작한다.
    - 100% 속도로 놓고, 모자라면 87%까지 늦춘다. 그보다 짧으면 거부한다.
    - 16:9 영상은 115%, y 1060에 놓는다. DB상 인물이 옆에 있으면 130%로 키우고 가운데로 옮긴다.
    - 오디오는 가져오지 않는다.
  - 프레이밍이 틀리면 그 샷에만 `scale`/`position`을 준다. 직접 고른 구간은 `source_in`/`source_out`으로 준다(87–105%).
  - 실패는 전부 아니면 전무다. 짧은 샷, 겹침, 계획 밖 클립 덮기(`allow_cover`가 없을 때) 중 하나라도 있으면 아무것도 바뀌지 않고 이유가 돌아온다. 되돌리기도 한 번이다.
- 확인은 `get_frame_strip`(샷마다 시작 + 몇 프레임의 시각)으로 한 장에 본다. 컷 경계의 튐은 `inspect_timeline_frames`로 따로 본다.
- 시간 단위:
  - 1프레임 = 8,467,200,000 ticks (30fps). 시퀀스 시간은 `"128f"`처럼 프레임으로 준다.
  - 자막 시작 프레임은 `get_timeline_state`의 V7 클립에서 읽는다.
- 길이 맞추기:
  1. `clip_place(source_in, source_out, at, take_audio=false)`로 놓는다.
  2. `clip_set_speed(speed = 놓인 프레임 수 / 목표 프레임 수)`를 적용한다.
  3. 결과 duration이 목표 프레임에 정확히 맞는지 확인한다.
- 속도는 100%가 기본이다. 절에 정확히 맞출 때만 87–105%로 조정한다. 사용자 마커에 맞추는 경우는 예외다(사스케 닮은꼴 84.6%).
  - 더 늦춰야 하면 소스 구간을 다시 고른다. 흠을 가리려고 늘리지 않는다(사스케 닮은꼴 CTA 85% → 사용자가 100%로 다시 잘랐다).
- 1.1초 미만 샷은 만들지 않는다. 짧은 자막은 이웃 자막과 한 샷으로 묶는다.
- **정지 이미지는 반드시 `source_out`(예: `"51f"`)을 준다.** 안 주면 기본 5초로 들어가 뒤 클립을 덮는다.
  - `covered`가 비어 있지 않으면 즉시 되돌린다.
- 프레이밍:
  - 16:9 영상은 position `[540, 1060]`, scale 115가 기본이다.
  - 인물이 작거나 옆에 있으면 130 이상으로 키우고 x를 옮긴다.
  - 얼굴이 자막(y≈1055)에 가리면 y를 올린다. 이때 띠 창(y 481–1675)을 다 덮는지 확인한다.
- 끝 처리:
  - 영상 끝 = 내레이션 끝 + 6프레임.
  - V5 그래픽, V6 타이틀, 마지막 자막을 같은 프레임에서 끝낸다.
- 확인: `get_frame_image`로 컷마다 한 장씩 본다. 어둡거나 인물이 안 보이거나 엉뚱한 장면이면 바꾼다.

### 5. 자막 효과
- **`shorts_caption_effects` 한 번으로 전부 적용한다.** `items: [{clip_id, effect}]`(shorts_plan 표의 effect 열). 효과 트랙으로 옮기고, slam은 810 px 한 줄에 들어갈 때만 120으로 키운다(안 들어가면 100과 이유를 돌려준다). 문제가 하나라도 있으면 아무것도 바뀌지 않고, 되돌리기는 한 번이다.
- 효과는 트랙으로 정한다.
  - slam: V8 `꽝 박히기`로 이동(`clip_move track_delta=1`), 그리고 `caption_set style {"font_size":120}`
  - fade: V9 `밑에서 스무쓰하게 올라오기`로 이동(`track_delta=2`)
- 120으로 키운 자막이 한 줄에 들어가는지 프레임으로 확인한다.

### 6. 효과음
- **`shorts_place_sfx` 한 번으로 놓는다.** `sounds: [{at: "<자막 첫 프레임>f", sound: "W4"}]`, `end_at`(영상 끝)을 준다. 이름(W1–W7, Kick 2, Epic Hit 3, Bells 2, 뚜훅, 티딩, 난감한 물방울, H:Cash…)은 프로젝트 미디어에서, 없으면 `LUMINA_SFX_DIRS`(기본 `D:/유튜브 채널/SFX`, `D:/Motion Bro`)에서 찾는다. 아래 트랙·프리롤·꼬리 자르기·sfx 태그·A2/A3 Auto-Match·볼륨 규칙은 명령이 적용한다.
- 트랙:
  - A2: Whoosh (KF - Whoosh Dramatic 1/3/4/5/6)
  - A3: Hit, Kick, 뚜훅, 티딩
  - A4: Bells 2, 난감한 물방울
- 타이밍: 소리는 자막 첫 프레임에 놓는다. Epic Hit 3만 21프레임(0.7초) 앞당긴다.
- 겹침: 같은 트랙에서 꼬리가 다음 소리를 덮지 않게 한다(`covered` 확인).
- 음량:
  - 모두 `clip_set_audio_tag sfx`로 태그를 단다.
  - A2와 A3만 `audio_auto_match`를 적용한다.
  - 티딩은 gain -7, volume -3으로 따로 맞춘다.

### 7. 저장과 보고
- 단계마다 `begin_transaction` / `commit_transaction`으로 묶는다. 실수하면 `rollback_transaction`으로 되돌린다.
- `save_project`로 저장한다.
- 보고에는 구하지 못한 소스, 늦춘 속도, 중복 사용한 소스, 아직 안 한 것(BGM, TMI)을 적는다.
