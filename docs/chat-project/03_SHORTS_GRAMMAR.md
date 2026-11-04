# 쇼츠 편집 문법 — Chat 참고 파일

이 파일은 저장소 원문을 묶은 배포 스냅샷이다. 실행은 `PROJECT_INSTRUCTIONS.md`와 `01_MCP_WORKFLOW.md`, 현재 MCP 도구 정의를 따른다. 과거 도구명·CLI·개인 메모리 지시는 현재 Chat 기능을 보장하지 않는다. 공통 문법과 사용자 확정 수정이 과거 빈도 통계보다 우선한다. 날짜가 붙은 관찰과 미완료 상태는 당시 기록이며 현재 상태는 다시 확인한다. 원문 링크는 로컬 출처를 표시한다. 이미지와 외부 자료는 별도로 열어야 한다.


---

## 원본: docs/shorts-editing/README.md

# Caddynote Shorts 편집 지침 (Codex / Claude 공용)

최신 공통 기준: 자막 완료 기준（로컬 참조: `D:/Dev/lumina/docs/editing-grammar/caption-quality.md`）. 초반 공백·끝 문장부호·긴 자막 후처리를 포함하며 아래 과거 관찰보다 우선한다. Phrase 관련 구현 언급은 과거 이력이며 현행 도구는 Lumina 내부 speech다.

## 2026-10-30 공통 컷 문법 추가

먼저 공용 편집 문법（로컬 참조: `D:/Dev/lumina/docs/editing-grammar/README.md`）과 공통 컷 문법（로컬 참조: `D:/Dev/lumina/docs/editing-grammar/common-cuts.md`）을 읽는다. 공통 컷 문법은 쇼츠에도 적용하며, 자막 밀도·화면 배치·사용자 확정 수치는 아래 쇼츠 문서에서 가져온다. 새 공통 문법의 기준 원본은 이 저장소이며 모델별 메모리에 별도 규칙을 복제하지 않는다. 아래의 'Claude 메모리 원본' 설명은 기존 쇼츠 문서의 이관 경위다. 관찰 통계를 기계적인 컷 간격으로 적용하지 않는다.

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
- `source_search`(work="엣지러너 2" 등)로 후보를 찾고, `source_sheet`로 마지막 후보만 눈으로 확인한다.
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

### 4. 배치 (V1)
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
- 효과는 트랙으로 정한다.
  - slam: V8 `꽝 박히기`로 이동(`clip_move track_delta=1`), 그리고 `caption_set style {"font_size":120}`
  - fade: V9 `밑에서 스무쓰하게 올라오기`로 이동(`track_delta=2`)
- 120으로 키운 자막이 한 줄에 들어가는지 프레임으로 확인한다.

### 6. 효과음
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


---

## 원본: docs/shorts-editing/caddynote-audience.md

1 year 2025-10-23–2026-10-23 (Studio CSVs in Downloads, "콘텐츠/트래픽 소스/구독 상태/시청자 연령_시청자 성별 2025-10-23_2026-10-23 …"): 18.9M views, 6,194 subs.

- Age/gender: male 76%; 25–34 36%, 18–24 22%, 35–44 16%, 45–54 12%, 13–17 11%. Largest cell 25–34 male 29.5%. Korea 95% (user, not exported).
- 99.2% of views from non-subscribers. Traffic: Shorts feed 76%, search 11%, browse 11%, suggested 1%, channel page 0.6%.
- Subs per engaged view: Shorts ~1/1,800, long-form ~1/120, concept videos ~1/40–50. Best-converting Shorts = something to see/hear (미쿠·테토 노래, 레제 댄스, 보컬로이드, 코난 그림체 프리큐어); worst = news/complaint (스틸볼런 엔딩 실망, 리제로 83화 작화) and character lists. 갭 차이: keep 85% but 1 sub per 21k.
- Shorts length (221 with ≥5k engaged): avp median 101% (≤25 s) → 85% (40 s+); length vs reach r=0.02.
- 계속 시청함 (1 year, 168 Shorts ≥20k views): mean 71%, median 72% (IQR 68–77). Top quartile has 2.2× the median engaged views of the bottom quartile; keep is unrelated to avp (r=-0.09) and sub conversion (0.09). 레제 댄스 is the exception (keep 63%, 3.56M views via rewatch/shares).
- First-second check (top 10 vs bottom 10 keep, frames at 0.3/1.8 s): pictures looked alike; the difference was text. Bottom: title main = in-universe technique/term (경화수월, 적혈조술, 흑섬과 패왕색, 츠가이란 무엇인가) 6/10, sub only describes (법봉을 휘두르는), first caption starts with a character/technique name 6/10 (4 were 주술회전 ability explainers). Top: main = a character's look/situation, sub = a mismatch (실제 경험담으로 만든, 원작하고 너무 다른), first caption starts with the work name or a common word 8/10. Rule written into 쇼츠 지침 v4.4 §8/§19.
- Long-form (user's frame: viewers click by choice, interested, understanding varies): two gaps — story (fans vs came-for-the-buzz) and concepts (character makers vs most fans). Long-form = 1% of views, 19% of subs. Avg view duration 1:36–4:26 whatever the length (호시노 15 min 3:41) → most see intro + first section only. CTR: character/question titles 4–7%, concept-word titles 1.3–2.2%. 레제 후유증 most-replayed: peak = comparison with 마키마·파워·코벤이 (6:56–7:50) and the reversal line (3:25–4:00); lowest = abstract intro, plot recap (5:37–6:01), media facts (극장판 형식, 성우). Peak sat after the 4:06 avg. Written as 7 rules in 롱폼 지침 v1.1 §0-1 (no recap block, comparison/reversal in first 4 min, compare each theory section with a known character, concept word then plain verdict, marketing = what it left the viewer, character+question titles, leave fans something to add). Retention exports (카제나/덴지/호시노, 2026-10-24): at 0:30 72/63/57%, 1:00 67/57/48%, 2:00 63/50/40%; after 2 min only 1–3 pts/min, theory sections 1–2 → the avg 2–4 min comes from the first minute, not late sections. Intro loss is a smooth decay per 1% bucket (5–9 s wide) — it can NOT be pinned to sentences; my first pass did (페스 line, fixed entry line, 개론 영상, appearance listing) and the user caught it ('그냥 페스캐가 둘이다 라고 한게 아닌데?'). Real spikes vs local median: 호시노 11:54 brand-identity recap re-listing earlier traits ×3.7 (7.4% in 9 s); theory-section entrances ×2 (호시노 7:59 비전, 덴지 4:48 타겟, 카제나 3:57 '이제 실제로 … 살펴보죠'); plot-following chapters ×2 (호시노 대책위원회 스토리); recap ending ×1.8 (덴지 9:00). Subscribers leave faster in the first 30 s (호시노 43 vs 58%). Added 레제 후유증/이시고리 (5 videos): section entrances spike ×1.6–2 in all five whether announced ('이제 ~ 살펴보죠', '이제 다음 생각해 볼 것은') or opened with a question (덴지 4:48, 레제 2:42) — no entrance form avoided it yet, so guideline lists only tests (cut the announcement, put the strongest material right after; 레제 6:44 entrance sat just before the heatmap peak). Re-binding: 호시노 11:54 and 이시고리 3:02 '비전은 … 대포를 쏘는 남자, 디저트를 갈망하는 남자…'. Plot retelling ×1.7–2 (호시노 story, 레제 5:19 recap and 0:36 phone-booth re-enactment — the only intro bucket that rose above trend). Listing similar characters (이시고리 '두 번째는 카시모 하지메') also spiked, unlike 레제's contrast with 마키마/파워/코벤이 → rule 2 = one contrast with main cast, not a list. Post-2-min loss varies: 호시노 1–3, 레제 3–4, 이시고리 7–8 pts/min. Lesson: compare a bucket to its neighbours before blaming a line.
- Comments: core = fans who know the work; react when told what they know ("동일인물인거 모르는 사람도 있냐?"); ㅋㅋ/meme register, but age data says 20–30s, so don't write down to teens. Long-form adds people making their own characters.

**Why:** the user asked to find the expected viewer and make guidelines + skills keep that viewer watching. My first comment-based guess "10~20대" was wrong; the age export corrected it.
**How to apply:** write for "unsubscribed 20–30s feed viewer, fan or newcomer, hears it once in order": start from what's on screen, end with a reason fans didn't know. In 콘텐츠 지침 v4 §0-1 (자료명 `caddynote-script-guidelines` (별도 자료; 필요 시 Bridge로 검색)), `caddynote-script` skill, voice.md. Edit side: `D:/Dev/lumina/docs/shorts-editing/shorts-edit-review-lessons.md`.


---

## 원본: docs/shorts-editing/shorts-edit-review-lessons.md

From the review of the first automated Short, 퓨키 데모 취소 (PUKEY GODDESS SHOT TRICK folder), which the user approved for upload on 2026-10-22. Each rule is something the user corrected or changed by hand.

**Sources**
- Never build a Short from one kind of source; the first version "looked single-source".
- Mix footage (trailers, PV, MV) with the images in the project folder: fan art, milestone illustrations, design sheets.
- Fetch outside evidence too:
  - X posts through api.fxtwitter.com or yt-dlp;
  - images in news articles;
  - browser captures of store or search pages.
- Tell the user what is still missing.
- A claim about a trend needs a real case on screen. For AI clones: the original vs. the copy as a stacked split screen, with a TMI holding only the original's name at the top of the upper panel.
- Every picture must tie to what the caption says. Art from an unrelated game (ReVamp) felt 뜬금없 and was removed.
- Literal or comic matches land:
  - a line about indie games not getting made → a Roblox search full of copies;
  - the last line → the character throwing up.
- A line about players trying a demo needs footage that looks like real play: UI, cards, the player's hands.

- When the script refers to an earlier season, look for that season's shots inside the new season's official teasers before asking for a recording. The user pointed out on 2026-10-22 that the 엣지러너 2 Official Teaser (jjhRSNkquOc) opens with a 1편 recap, 0–21 s: Lucy and David, Lucy's blood tears, David's death, then "DAVID'S DEAD."
- Do not use a promo's text-only card ("DAVID'S DEAD." on a plain background) as a shot.
  - The user rejected the card itself, not only its position (2026-10-22). Show the scene it refers to instead.
  - Burned-in text over real footage ("NIGHT CITY WILL BURN AGAIN" over a burning street) was kept. It was scaled down and moved up so it reads above the caption.
- Teasers put black frames (fades) of about 0.3–0.5 s between flash shots. Measure brightness (signalstats YAVG; about 21 here = black) and take only the bright spans. Never run one source range across the teaser's own cuts.

**Framing**
- Pick shots where the character reads clearly.
- When she is off-centre, push to 130% and shift x to centre her.
- Avoid shots under the caption that are busy with lyric typography or nearly black.

**Cut rhythm**
- Not one caption = one cut. Shots span 2–4 captions where the phrase is one unit. The user merged these by hand:
  - "돌연 취소하면서 아쉬움을 남겼습니다" into one shot;
  - "하지만 아직 이름을 알리지 못한 인디게임들은 사정이 다르죠" into one 4 s shot.
- "융융 전파 신드롬을 만들었던 제작진이 참여했고" is one source (one illustration with a pan).
- The opening name line is one smooth shot (a slow zoom on one image). A 0.7 s first cut felt like the frames jumped.
- When a source shot fades in from black, start right after the fade completes; measure the brightness (signalstats YAVG) to find the frame.
- No sliver of another shot inside a clip near its edges: check the source's own scene cuts and keep at least 0.3 s.

**Comparison Shorts** (사스케 닮은꼴, 2026-10-27)
- A section about a look-alike shows only that character. When the narration names the original ("사스케가 생각나는데요"), do not cut to it. The user said "사스케 장면 쓰지 말 것" after I put Sasuke on that line inside Uryu's section.

**SFX** (the user's own changes)
- A name of an earlier work gets the identity hit: the user moved Hit Guns onto "융융 전파 신드롬을".
- Bells 2 goes on lines that build toward something: "먼저 나올 수 있다는" and "이미 유명해진".
- "50만 건을" takes Epic Hit 3, pre-rolled so the hit lands on the caption.
- Do not put Kick 2 on every still image; the user removed one.

**TMI**
- TMI must read at the narration's tempo. A long TMI at a fast sentence start was rejected ("템포상 처음에 읽히기 어려움"), as was one about an unrelated subject.
- When the user supplies a fact, use their wording ("Alliance Arts 개발 및 유통 똑같음").

**Why:** these are the gaps between the grammar statistics and what the user accepts.

**How to apply:** check every edit against this list before showing it. Related: `D:/Dev/lumina/docs/shorts-editing/shorts-cut-grammar.md`, `D:/Dev/lumina/docs/shorts-editing/shorts-sfx-grammar.md`, `D:/Dev/lumina/docs/shorts-editing/shorts-tmi-grammar.md`, `D:/Dev/lumina/docs/shorts-editing/shorts-caption-effect-grammar.md`, `D:/Dev/lumina/docs/shorts-editing/lumina-replace-keep-neighbors.md`, `D:/Dev/lumina/docs/shorts-editing/shorts-upload-title-grammar.md`.


---

## 원본: docs/shorts-editing/shorts-cut-grammar.md

Learned 2026-10-21 from 2,221 footage clips in 177 Shorts (Premiere, read-only). Scene changes were detected with ffmpeg (scene score 0.3) and counted in timeline frames, because the timeline samples the source once per frame.

**User correction (2026-10-28): "모든 컷에 키프레임 주면 그건 슬라이드쇼지".**
- Do not answer "the cuts feel static" by adding zoom/position keyframes to every shot. Choose source ranges with actual expressions, gestures and actions first.
- Keep shot framing static by default. Add a camera move only when that particular shot needs it, such as keeping a moving hand visible; stop tracking when the subject stops.
- An across-the-board push/pull treatment is not a substitute for better footage selection or cut allocation.

**Rule the user states: cuts must never jump ("컷이 튀지 않게 하는 건 기본").**
- No frame of a neighbouring shot at a clip's edge: an in point a few frames before a source scene change shows the previous shot for 1–2 frames.
- Their own edits still have this at about 3–4% of clip starts (5 of 8 flagged starts were real when looked at; the rest were anime motion or flashes read as cuts). Clip ends are cleaner (92% clean).
- Automation must reach zero: put an edge exactly on the scene change, or at least ~3 timeline frames away from it. Judge it in timeline frames: a change less than one timeline frame inside never shows.
- Real jump cuts within one shot (skipping ≤1 s of the same shot with no scene change) are rare: 33 of 2,042 cuts.

**Grammar**
- About 12 footage clips per Short; median 2.0 s (10–90%: 0.9–4.0 s); 12% under 1 s, 4% over 5 s.
- 88% of cuts land on a caption start.
- 86% of clips are on V1; V2–V3 hold overlays.
- 25% of clips have a changed speed (see `D:/Dev/lumina/docs/shorts-editing/shorts-marker-workflow.md`: the marked moment is put on a caption start).
- Back-to-back clips of the same file mostly skip ahead to another moment (>3 s in 343 of 535 cases), not to the next frame.

**Framing and motion** (2,545 picture clips: 2,221 footage, 324 stills)
- 1920×1080 footage sits at position (540, 1060) at **115%** (on screen y 439–1681) or **130%** (y 358–1762). It is cropped left and right; the whole height shows; the title fills the black band above.
  - The template's V5 graphic has black bands at y 0–481 and 1675–1920, so footage shows only in the window y 481–1675.
  - Centred at y 1060, covering that window needs ≥113.9%. **115% is the smallest round scale with no gap at the bands**; 130% zooms in further (831 vs 939 source px wide).
  - Scale is per-shot framing (the user pointed out that some clips are zoomed in even further). Effective scale for 1920-wide 16:9 footage:
    - 114–120%: 47%, the base that fills the window.
    - 121–135%: 35% (130).
    - 136–200%: 9%. 28% of these are also moved sideways ±100–800 px to centre the subject.
    - Not by era, not to hide letterbox or burned-in text (tested). The same file is re-zoomed within a Short 17% of the time.
  - Below 114% (10%) is a deliberate layout, never a gap at the bands:
    - Stacked split screen for comparisons: two ~58% panels at y≈770 and y≈1380 inside the window.
    - Or a small picture-in-picture on V2/V4.
  - Automation needs the subject's (face's) position and size per shot, so the footage DB should detect them. Then: 115% floor, push in until the subject reads, and shift x to centre it.
- Footage is rarely animated (4%): pans left/right over ~2 s, or zoom-in ×1.15–1.35 over ~2 s. Effects are almost never used (Transform 12, Gaussian blur 2); transitions ~0.
- 61% of footage starts get a sound (whooshes, see `D:/Dev/lumina/docs/shorts-editing/shorts-sfx-grammar.md`).
- Stills appear in 55% of Shorts, about 3 per Short. They are shorter than footage (0.8–1.9 s) and 91% start on a caption start.
  - 64% are on V1, full frame (fill or between fit and fill).
  - 28% are on V2 as an inset smaller than fit, over the footage.
  - 27% are animated: a slow zoom-in ×1.15 over ~2 s (Ken Burns), or a pan (mostly left or up) over ~2.4 s.
  - 75% get a sound on their start, mostly Kick 2.

Not analysed yet: which shot goes with which caption content (needs looking at the footage).

**End of a Short** (measured 2026-10-22 on 172 Shorts; the user's rule: "쇼츠 영상 끝은 나레이션보다 소폭 길며, 나머지 트랙도 끝 맞출 것")
- The video ends a little after the narration: median 6 frames (0.2 s), 3–9 frames typical.
- Footage, captions, the title and the band graphic all end on that same frame (171 of 172).
- Audio ends at or before it, so sound tails are cut there.
- The template's title and band graphics run to 60 s, so trim them to the end.

**Shot ↔ narration (user corrections, 2026-10-25, 오미 Short)**
- One phrase = one shot. Cutting on every caption ("컷 너무 잘게") and uneven bursts of short clips ("컷 배분 더럽게") both get rejected; a hook built from 3 clips of 0.7–2 s with fast source cuts inside was rejected too.
- An event shot goes only where the narration tells that event: the punch only on "주먹까지 날리지만", never borrowed for the hook ("펀치는 주먹을 날리는 대목에만"). The hook uses a clean single shot of the subject instead.
- When the copy holds the subject back for a reveal ("…조연이 있습니다 / 바로 오미인데요", title "??"), the subject's face must not show before the reveal line ("오미가 왜 0초부터 나오는데"). The user's own fix (2026-10-26) did NOT use other footage (they deleted the trailer clips): it used the subject's footage with the face hidden — far silhouette walking (1 caption), body close-up cut off at the neck (2 captions), then one shot starting on the caption before the reveal that pans from her hand onto her face, the face landing just after "바로 오미인데요" starts. A static shot there is "재미없어".
- The user names a source for a beat ("구해주는건 e09 0619") when the beat needs the real event (the rescue), even if the shot is dark/wide; push in (150%) on the action.

**Cut distribution in the user's own 오미 edit (2026-10-26, "컷을 어떻게 배분했는지 봐라")**
- 19 shots in 40.6 s, 1.2–3.1 s (mean 2.1); every cut on a caption start.
- Default = one shot per 2 captions (modifier + predicate: "그녀는 헝클어진 / 녹색 머리에", "야구모자를 / 쓰고 있어"); cut at clause ends (-있어 / -지만 / -고 / -는데요), never inside a clause.
- One event clause = one long shot of 3–4 captions (punch "복수에…날리지만" 2.8 s, rescue "그가…했습니다" 2.6 s, closing line 3.1 s).
- A subject switch gets its own short shot: "그런데 정작 D는" = 1 caption, 1.2 s of D; then "오미의 충고를…" and "끝까지 카프리에게…" each a new shot.
- Description sections stay in one source (3 consecutive 1912 shots for 민소매/터프/몸매); story sections change source per event.
- Speeds 0.87–1.05 stretch a shot to fill its clause exactly instead of adding a cut.
- An inset illustrating a word starts on that word's caption (투지 image moved from "포켓몬스터의" to "투지가 떠오를 만큼"), over a full-figure shot of the subject so the comparison reads.

**Repeat offence: 사스케 닮은꼴 (2026-10-28).** The rules above were already here, and I still placed messy cuts. The user had to step in three times, then said "편집문법 학습 좀 하고. 다음에도 컷 배분 더럽게 하지말고".
- **D section.** A lightning shot dissolved into a white frame, so I trimmed it and rate-stretched it to fill the gap. The user said "D 컷 깔끔하게 배분해라". The fix was one shot per 2 captions at 100% speed: a cleaner source range, not a stretched one.
- **Nickname line.** The user extended my hologram shot over all 4 captions of "아예 / 사이버 사스케라는 / 별명까지 / 붙었습니다" (2.8 s). They deleted the extra shot I had put inside the sentence.
- **Revenge sentence.** I filled "심지어 자신의 일족을 몰살한 범인을 찾아 복수하려 한다는 설정마저 사스케와 닮았죠" with 5–6 shots: village, emblem, bodies, corpse, embers, eye. The user cleared it all and set markers for exactly 3 shots:
  - 3 captions: setup clause, bodies in fire;
  - 2 captions: "복수하려 / 한다는 설정마저", embers face;
  - 1 caption: fade tail "사스케와 닮았죠", eye close-up.
- **CTA.** I stretched the last clip to 85% to hide a jump. The user re-cut it at 100% from another source in point.

The approved result (20 shots, 37 s) shows what a clean distribution looks like:
- Every cut is on a caption start. Shots run 1.13–3.47 s; none is under 1.1 s.
- Captions per shot: 2,2,2,2,2,2,1,2,1,1,3,4,2,2,4,3,2,1,3,2.
- A 1-caption shot is only a full clause of ≥1.1 s ("친구라는 관계부터", "적 진영에 합류해", the closing fade).
- A short lead-in caption ("그리고", "아예", "심지어", 0.4 s) never stands alone; it rides with the next caption's shot.
- A punchline sentence is one long shot: "심지어 일본판 성우도 스기야마 노리아키로 똑같죠" = 1 shot, 3.5 s; the nickname line = 1 shot, 2.8 s.
- Speed is 100% except one 84.6% shot, placed on the user's own marker.

**Hold or cut at each caption boundary (archive, 2026-10-28).**
- Data: 180 unique Shorts from 230 .prproj (read-only), 5,590 caption-to-caption boundaries.
- A boundary counts as a cut when the topmost picture on V1–V3 changes within 2.5 frames of the next caption's start.
- 87% of picture changes land on a caption start. Captions run back to back (pause < 0.05 s at 99% of boundaries), so pauses don't decide cuts.
- **The sentence decides, not the clock.**
  - **Sentence end** (the previous caption fades, or ends in 다/요/죠/까/?): cut 83%. After a fade on a sentence ending: 96%.
  - The only hold at a sentence end is when the shot is still under 0.7 s (cut 31%).
  - If the next sentence opens with a slam: 50%.
  - **Inside a sentence: hold.** Cut 19% overall.
    - Phrase-ending captions: 12–24% at any shot age, even past 3 s. Long sentences are not split for length; the sentence's length is the shot's length.
    - Clause ends (-고 -지만 -는데 -면 -며 -서): 12% if the shot is under 1 s, 41% at 1–2 s, 53% at ≥2 s. This is the one place a long sentence takes a second shot.
  - **Never cut onto the sentence tail.** Next caption is a fade: cut 10%.
  - Don't split a modifier from its head or an object from its verb: after captions ending in 의 / 을를 / adnominal -ㄴ/-는, cut 12–14%.
  - **Mid-sentence cuts happen when a new thing must be shown**, not for rhythm:
    - a list item (after 와/나: 31%);
    - a name reveal ("진격의 거인으로 유명한 → WIT 스튜디오입니다");
    - a subject switch ("명조의 린네나 → 니케의 티아를 보면").
- A sentence averages ~3.9 captions and ~0.55 mid-sentence cuts, so shots come out at ~2–2.5 captions on average. "One shot per 2 captions" is that average, not a rule to apply per pair.
- Scripts: `D:\Dev\lumina\docs\shorts-editing\cut_grammar_extract.py` and `cut_grammar_analyze.py` (run with `uv run --project engine python`). They use `lumina.io.prproj.extract`; captions = text graphics on V4+ centred at y 900–1250, effect taken from Transform keyframes.

**Plan the cuts before placing anything.** Write the plan, check it, then place:
1. List the captions with start frames. Group them into shots by sentence, using the hold/cut rules above:
   - one sentence = one shot by default;
   - add a cut inside a sentence only at a clause end once the shot is ≥1 s, or where a new thing must be shown (list item, name reveal, subject switch);
   - never cut right before the fade tail;
   - a lead-in caption under 0.5 s joins the next caption.
2. Reject the plan if any of these hold:
   - a shot is under 1.1 s;
   - a cut is not on a caption start;
   - a sentence has more shots than clauses;
   - more than one 1-caption shot comes in a row.
3. Pick sources per shot afterwards. If a source range has a flaw (white flash, dissolve, a jump), choose another range or source. Do not stretch it below 87% or trim it into a sliver.
4. After placing, read the V1 shot lengths back against the plan before showing the user.

**Why:** cut placement is the core of the edit step, and the user's baseline is that a cut must never jump. Messy distribution has now been corrected on two Shorts in a row (오미, 사스케 닮은꼴).
**How to apply:**
- Start cuts on caption starts, at 1.1–4 s.
- Plan the shot table first (above).
- Check every clip edge against the source's scene changes: snap the edge onto the change, or move it ≥3 frames away.

Related: `D:/Dev/lumina/docs/shorts-editing/autonomous-shorts-editing-goal.md`, `D:/Dev/lumina/docs/shorts-editing/shorts-sfx-grammar.md`, `D:/Dev/lumina/docs/shorts-editing/shorts-marker-workflow.md`, `D:/Dev/lumina/docs/shorts-editing/shorts-edit-review-lessons.md`.


---

## 원본: docs/shorts-editing/shorts-caption-effect-grammar.md

Learned 2026-10-21 from 5,720 captions in 173 Shorts (Premiere, read-only). Captions are Pretendard-Bold at 100 px, white with a dark stroke, animated by a Transform effect.

- Pop (scale 70→100): the default, 58% of captions. Sentence starts and normal phrases; it carries the whoosh at sentence-start cuts.
- Slam (scale 180→100, 15%): emphasis. 64% of slams are also 120% size, 11% are 140%. Used mid-sentence on the key phrase, and in the hook (first 3 s); 85% get a sound.
- Fade + slide (opacity 0→100 with position, 24%): sentence tails, 67% of them ending a sentence. A calm read, usually with no sound.
- No animation + special style (black text, colored run, other fonts such as S-CoreDream-9Black, two lines): name or list cards and inserted titles.
- An annotation (a second, smaller layer about 48 px) adds side notes or the source; it shows up more often on emphasized captions.

**Why:** caption effects are part of step 5 and drive the SFX choice (`D:/Dev/lumina/docs/shorts-editing/shorts-sfx-grammar.md`).
Track default is 100 px for all three tracks (the user's call, 2026-10-21). 120% on a slam is a per-cue override, not the default.

Rules the user set on 2026-10-21:
- An emphasized caption that is worth enlarging goes to 120 (per-cue font_size). It must still fit the 810 px box on one line. Measured: "독특한 컨셉으로" is 731 px at 120 and OK; "죽일 만큼 사랑하는" (856) and "엘프처럼 뾰족하게" (826) would wrap, so they stay at 100.
- TMI captions (furigana-like side notes) are Pretendard-Medium 48, same stroke and shadow, box 810×576, vertical center.
  - Above a 100 caption: y 680 (box top; center 968, just over the main text at 1055).
  - Above a 120 caption: y 670.
  - A more supplementary note goes below at y 960, split into lines with line breaks where it reads well ("odyk 캐릭터 디자이너\nAiobahn 뮤비\n나나히라 성우의 환상적인 조화").
  - In the Premiere edits the TMI is a second layer of the same graphic, so it animates with its caption.
- Lumina: TMI are graphic clips (text layers) on one ordinary video track named "TMI" (V4, below the title), not caption tracks. The user rejected three TMI caption tracks because they push the lower tracks out of view.
  - Several notes at the same time go into one graphic as separate layers (e.g. "Femme Soûle" at y 680 and "우연찮게도 ‘술’이라 부름" at y 960).
  - Each TMI graphic gets the same preset as its main caption as a clip effect, so they move together.
  - Main captions stay caption cues (no graphics conversion).
  - Verified 2026-10-21.

**Details (parsed 2026-10-21, 5,720 captions, 1,144 sentences)**
- Sentence template: pop at the start, slam on the focus phrase, fade on the last caption.
  - 3 captions: PSF 38%, PPF 38%.
  - 4 captions: PPSF 34%, PPPF 22%, PSPF 20%.
  - 5 captions: PPPSF 23%, PPSPF 21%, PPPPF 14%.
  - A sentence ending in F is followed by a sentence starting with P (772 of 955).
- Slam = the focus phrase just before the predicate:
  - 88% are mid-sentence and only 3% at the start. 42% sit on the second-to-last caption, and 55% are followed by the closing F.
  - They lean towards objects (을/를 15% vs 9% on pops) and subjects (이/가 15% vs 11%).
  - Count per sentence: one in 55% of sentences, none in 36%, two in 8%. Two only in long compound sentences, one per clause.
  - Slams never come back to back (826 single slams, 8 pairs). They are 11–19% of the captions in a Short.
  - Sentences with no slam are mostly the closing sentence or CTA (댓글), transitions, plain list items and questions. The first sentence almost always has a slam; slams are 21% of captions in the first 10% of the video.
- Fade is the end of a breath group, not only the sentence end. 32% of fades are mid-sentence, on clause endings (~고 ~며 ~서 ~면 ~는데 ~때문에: 39% vs 15% on pops).
  - After a mid-sentence fade there is a pause (38% vs 7% after a pop) and a cut (62% vs 20%), and the next caption pops.
  - Sentence ends without a fade: a slam on the final predicate as a reveal ("가짜입니다", "나체입니다").
  - Fades are 40% of captions in the last 10% of the video (ending and CTA).
- Size:
  - Slams: 120 (64%), 140 (11%), 100 (25%). A 100 slam is a long caption (7–9 characters vs 5–7) that would not fit at 120.
  - Pops at 120 (8% of pops): names or topics that open a point (은/는/이/가), more often in the first 10% of the video, with W3 or K2.
  - 140 (121 captions in 59 Shorts): very short, 3–6 characters, a punchline or key term ("가짜입니다", "말장난입니다", "PTSD가", "범부", "디저트에서"). Mostly slams with 뚜훅 or Guns; 24% in the first 10% of the video.
  - 200: a quoted single word or line ("“손”", "당장 하자!", "데라 씨...!").
  - 83: long captions shrunk to fit ("댓글로 알려주세요!", "화제가 되고 있습니다").
  - Mixed size (40 captions): the name or term at 120/140 and its particle or copula at 100, e.g. 토우지[140]+도, 오노 켄쇼[120]+와, 땅따먹기[140]+입니다.
- Current practice (user, 2026-10-21, corrected): emphasis is 120 only. 140 and font changes are both rare now. They are allowed as exceptions, e.g. a special font for a quoted line or technique name, or 140 for the one big reveal. Default: Pretendard-Bold, with 120 on the slammed focus phrase. The 140/200 and font stats below describe older edits.
- Fonts:
  - Pretendard-Bold: everything else.
  - GangwonEduSaeeumMedium (handwriting, 22 in 16 Shorts): inner monologue, the imagined viewer's reaction or a character's line ("어디서 본 것 같은데...?", "이걸 애니로 만든다고?", "굳이 내가 해야 하나...?"). Pop or slam.
  - 산돌제비 (SD Jebi; "SDJebib-eBd" and "SanSwB" are the same family, the user confirmed 2026-10-21): the display font for a named thing or a line, 20 captions in 13 Shorts, almost always slammed at 1.4–2.0×.
    - Technique or skill names: "경화수월", "불의유희", "재계상", and English names run one per caption ("Eight-Handled Sword" → "Divergent Sila" → …, K1 each).
    - Quoted lines and catchphrases: "“알아”", "죽이겠다...!를", "\"계획대로다!\"를", "당장 하자!", "데라 씨...!".
    - The big reveal of a Short: "남자입니다", "가짜입니다", "아르센 뤼팽".
    - About half are black text with a white stroke, at 1.8–2.0×; the rest white with the usual dark stroke. Sounds: 뚜훅 on reveals, Guns/Cash on names, K1 on English-name runs.
  - S-CoreDream-6Bold at about 36: image credits ("pixiv #…", "@…").
  - List cards (S-CoreDream-9Black, black text with a white stroke, no background, no animation, 0.6–1.2 s each, K2): name at 120 and the work/source as a 48 run or a second line ("료멘 스쿠나vs 고죠 사토루" + "주술회전", "노 게임 노 라이프" + "(2014)").

**User corrections on the 엣지러너 2 Short (2026-10-22).** The user re-edited my caption split in the GUI. These rules override the statistics above where they disagree.

- **Mid-sentence fade only on a coordinate clause.** Kept as a fade: "주역 대부분이 살아남았고," which states one fact before the next.
  - A clause that sets up what follows stays a pop. The user moved these from fade to pop: "더 슬플 거라더니" (contrast), "준비해 놓고도" (concession), "말을 아꼈다면" (condition).
  - The pause cannot tell the two apart. TTS pauses are about 0.3 s after every clause end. Decide by the connective's meaning.
- **A sentence-final reveal slam is only for a short punch predicate** ("가짜입니다", "나체입니다").
  - A long final predicate closes with a fade. The user changed "희망적이었습니다" (8 syllables) from slam to fade, even though it was the twist.
- **Merge a too-short caption into the next one.** "제가" (spoken 0.18 s) became "제가 생각하는 문제는". "그런데" (0.30 s) stayed on its own. The threshold is roughly 0.25 s spoken.
- **Drop the caption of a line that only says the work's name already shown in the main title.**
  - On the user's instruction: "바로" stays as its own caption, and "사이버펑크 엣지러너 2인데요" got no caption.
- **Confirmed unchanged:** five slams, each on the focus phrase right before the predicate: 실망을 안긴 / 휴지를 / 주역 대부분이 / 너무 큰소리를 / 큰 비극을 기대하게.
  - 120 only where it fits one line (810 px). "큰 비극을 기대하게" stays at 100. The closing sentence has no slam.

**How to apply:** pop by default, slam the one key phrase of a sentence, fade the sentence tail, and use card style for names in a list. Apply the 2026-10-22 corrections on top. Related: `D:/Dev/lumina/docs/shorts-editing/autonomous-shorts-editing-goal.md`, `D:/Dev/lumina/docs/shorts-editing/shorts-edit-review-lessons.md`.


---

## 원본: docs/shorts-editing/shorts-sfx-grammar.md

Learned 2026-10-21, read-only, from every Shorts sequence (has a "쇼츠 타이틀" clip) in D:\유튜브 채널\Project. Captions were joined with caption style and animation, picture changes, narration pauses (A1 envelope) and Kiwi tags. About 15 sounds per Short; 44% of captions get one.

**Hard timing rules**
- A sound starts on a caption's first frame. Epic Hit is pre-rolled about 0.7 s so its hit lands there.
- One sound per caption, at least 0.5 s apart.
- Tracks: A2/A3 main sounds, A4 bells and 물방울.

**The caption effect decides most of it** (see `D:/Dev/lumina/docs/shorts-editing/shorts-caption-effect-grammar.md`)
- Slam caption (scale 180→100, usually 120% size): 85% get a sound — a hit 44%, 뚜훅 25%, 티딩 ~10%, bells 5–8%.
- Pop caption (70→100) at a sentence start on a new shot: 68% get a whoosh.
- Fade/slide caption (a sentence tail, usually ending in EF): 83% get nothing. Exceptions:
  - a question gets Bells;
  - a letdown or negation gets 물방울.
- Name or list cards (no animation, black text, colored run, other font, two lines): Kick 2 49–81%.

**Per sound and number**
- Whoosh: sentence start + new footage + a narration pause of 0.15 s or more. Never on a sentence tail.
  - W3: frame 0 only.
  - W4: the structural whoosh — the body opener right after the hook, and the conclusion or outro (45% of W4s fall in the last two sentences). Favored after 그래서/따라서 and 즉.
  - W1, W5, W6, W7: rotation in the body. W1 and W6 lean to contrast or addition openers (그런데, 하지만, 또, 게다가). W5 has a long swell and goes on longer stretches. W7 is rare.
  - The same file is never used twice in a row. Mid-sentence cuts usually get nothing.
- Kick 2: list items, name cards and inserted images (the card style above). The same K2 repeats on every item. In a list of character pairs it is stacked with 빨강 파랑.wav.
- Kick 1: a light point inside an explanation — a definition, a translation or a term ("달의 송곳니", "Eight-Handled Sword", "계단을 오르는"). Pop or slam; often the first emphasis in a Short.
- Kick 5: a summarizing evaluation late in the video ("작품의 개성이 되었죠", "영화에도 진심인 애니라", "단 7화로").
- H:Guns: an identity, name or nickname reveal at the payoff of a sentence ("HOT LIMIT입니다", "이즈미 커티스를", "대포 주술사라는"). Early (position ≈0.35).
- H:Cash: a surprising action, event or absurd fact ("남자라는 것입니다", "이걸 애니로 만든다고?", "돌로 만드는 사고를"). Early to middle; the most slam-heavy (79%).
- H:Impactor: a twist or knockout blow, mostly in the second half ("모든 사건의 배후였다는", "키메라의 재료가 되었다는", "개차반", "똑같다는 겁니다").
- Epic Hit 3: the turning-point word inside an explanation, which builds curiosity ("현대 주술사가 (아니라는)", "다른 점이", "바로 생각나는"). It is used more in the last sentence. E4 and E2 are rare variants of it.
- Bells 2: the curiosity gap — the hook question's ending ("무슨 뜻일까요?", "들어보셨나요?"), "이유가 있습니다", or a fact announced to be explained next. Mostly the sentence end, on fade or pop captions.
- 뚜훅:
  - In the first 3 s, on the slammed key word of the hook question ("스바루가 【기억을 잃은】 이유는 무엇일까요?").
  - Later, on an odd or twisted fact ("유타는 후지와라가 【아닙니다】", "【노리고 만든】 캐릭터").
- 티딩:
  - At the end: the CTA ("댓글로 알려주세요!") or the closing verdict.
  - In the middle (≈6–18 s): the "aha" answer or trivia payoff ("원조가 있습니다!", "바로 화면 비율입니다", "이제는 도로롱으로").
- 난감한 물방울: a letdown, negation or awkwardness, usually a fade tail in 죠/네요 ("아쉽네요", "잘 와닿지 않습니다", "깜빡 속았어요"). Negation words appear in its captions 8× more often than elsewhere.
- Rare sounds:
  - SCH Button 5: an "item/variant" mention ("다양한 옷을", "말장난입니다").
  - 신서유기 or 짜잔: comic reveals.
  - H:Deep: a heavy symbolic statement.
  - Real voice or OST clips from the source go on A1.
- Opening pattern: W3 (frame 0) → 뚜훅 (slammed hook word) → B2 (question end) → W4 (body).

**Volume (user, 2026-10-21)**
- Strong sounds get clip Volume -3 dB; the confirmed list is below.
- In Premiere only A2 and A3 are selected for Loudness Auto-Match.
- Bells 2 and 난감한 물방울 sit on A4 so they stay out of the match: Premiere measures their short sound on integrated loudness and boosts them about +6 dB (measured: B2 +5.9, 물방울 +6.2 at the -21 LUFS SFX target).
- Lumina's audio.auto_match measures clips with under 1 s of sound on momentary max, so it gives only +1.3 / -0.9 dB there, which is fine.
- 티딩 is also under 1 s. Lumina gives it -11.0 dB, where the user's Premiere projects hold -7.0. I set -7.0 by hand on the 엣지러너 2 Short (2026-10-22).
- The other gains matched the archive: 뚜훅 -6.4, Impactor -6.1, Kick 5 -7.9, whooshes -0.6 to -3.2.
- Volume -3 is volume_db, which Auto-Match (gain_db) leaves alone, so it survives the match.
- What the past projects actually hold (Premiere import reads levels since commit af5765f): no clip Volume changes on any SFX, Kick 5 and 티딩 included.
  - Track faders are A2/A3/A4 at -3 dB (170 Shorts) and A5 (BGM) at -6 dB.
  - The Audio Gain is pure Auto-Match: K2 -5.4, Kick 5 -7.8, 티딩 -7.0, 뚜훅 -6.4, Whoosh -0.6 to -3.2, Impactor -6.1.
  - Bells and 물방울 have gain 0 everywhere, even the few on A2/A3.
- Rule confirmed by the user (2026-10-21): both apply.
  - Track faders: A2, A3 and A4 at -3 dB, the BGM track (A5) at -6 dB.
  - Clip Volume -3 dB on exactly 티딩, Kick 5 and Epic Hit 2 ("일단 이 3개"; there may be more later). This is newer than the archived projects.
  - Auto-Match goes on A2 and A3 only; A4 (Bells 2, 물방울) stays untouched.

**BGM (user, 2026-10-22)**
- BGM must move between -24 and -27 dB on the track meter, measured post-fader on the BGM alone.
- The user corrected this: it is meter dB (peak), not LUFS. -25 LUFS is about 8 dB too loud.
- It sits on A5, whose track fader is -6 dB; set the clip's Audio Gain.
- Check it by soloing A5, exporting a wav and simulating the meter (40 ms peak, 24 dB/s fall). Aim for a median of about -25.5 dB.
- Pick a stretch of the track with an even level.
- 퓨키 example: the Yukari OST from 114 s with gain -14.4 dB on top of the -6 fader reads 10th–90th percentile -27.7 to -23.9 dB, median -25.5.

The Guns/Cash/Impactor/E3 split comes from reading sentences, not hard features; treat it as a tendency. Clip volumes were not captured (the extractor drops audio levels).

**Stay inside this palette (user, 2026-10-28).** On the 사스케 닮은꼴 Short I put DEOKDI_SFX_PROJECT magic sounds (Spirit Appearance, Magical Shimmer) on a Ransōtengai scene. The user said "내가 쓰던 sfx가 아니여서 어색함". I replaced them with W5 by the pop/new-shot rule. Even when a scene seems to call for a themed sound (magic, energy), pick from the sounds above.

**Why:** Claude places the SFX itself in the edit step (`D:/Dev/lumina/docs/shorts-editing/autonomous-shorts-editing-goal.md`).
**How to apply:** go caption by caption. Pick the caption effect first (`D:/Dev/lumina/docs/shorts-editing/shorts-caption-effect-grammar.md`), then the sound by the rules above; snap it to the caption start and rotate the whoosh variants. Related: `D:/Dev/lumina/docs/shorts-editing/shorts-copy-grammar.md`, `D:/Dev/lumina/docs/shorts-editing/shorts-automation-pipeline.md`.


---

## 원본: docs/shorts-editing/shorts-tmi-grammar.md

User, 2026-10-21: TMI is literally TMI. These are things that would drag the flow if the narration said all of them.

Parsed 2026-10-21: 604 TMI layers in 146 of 178 Shorts (median 4 per Short). They are spread through the middle of the video, rarely in the first or last 10%.

**What goes in a TMI** (81% add words that the narration never says):
- Identity label for something the narration only describes (most common): the voice actor's name on "성우는 여자입니다" (와카야마 시온), the work on a character name (원피스), the technique's name (만해 관음개홍희개), the creator (마이클 폰드스미스).
- Original script or spelling of a term the narration says: 緑(みどり), Boogie-Woogie, 夏油傑, ガブッと.
- The exact figure or a correction: "정확히는 3,852명", "약 300분 (애니 15화 분량)", "엄밀히는 원안이 맞는데…".
- Lore or mechanism detail behind a claim, memo style, often 2 lines below: "계활을 외치면 1분간 / 몸을 내어줄 것", "실제로 1화 공개 후 / 프리렌 2기를 제치고 화제가 됐음".
- The narrator's own aside or joke: "나루토 달리기 뭔데...", "흉측한 비주얼은 덤으로...", "콜라보 고증 확실하네 ㅋㅋ".
- Spoiler warnings ("스포 주의!") and small lists ("트리니티 : 둥근 실루엣 / 게헨나 : 뾰족한 실루엣").

**Form**
- Default to one TMI attached to one caption cue, with the same start and end. Spanning two or more main captions is uncommon and should be an exception, not a way to give a long note more reading time (user, 2026-10-29: "tmi가 자막 2개 이상 걸치는 경우 웬만하면 잘 없음"). The coronavirus/mask aside belongs only to "인상을 줍니다", not the whole "차갑고 / 위협적인 / 인상을 줍니다" phrase. Follow a user-specified cue exactly.
- Never in the narration's polite register (0% end in 니다/요/죠).
  - 76% are noun phrases or labels.
  - 12% are asides ending in ... ? ! ㅋ.
  - 8% are memo endings (~음 ~임 ~함 ~것); 4% plain speech.
- Mostly one line (84%), 7–19 characters. Pretendard-Medium 48.
- Labels, original names and asides go above the caption (y 680; y 670 over a 120 caption). Longer explanations and memos go below (y 960), split into 2–3 lines.
- A TMI hangs on the caption that mentions its referent: the noun or role it names, the claim it details.

**User correction (오미 Short, 2026-10-25).** I put "일본명 카스미" on "포켓몬스터의 투지가" and the user removed it: "카스미는 중요하지 않음". They added instead:
- the name of the gear the narration only describes: "고릴라 암즈" on "기계 팔을 하고 있어";
- the concrete look behind a vague word: "가슴이 트인 클리비지룩" on "상당히 여성스러운";
- a jab at a rival character: "AI 좋아 청년..." over "끝까지 카프리에게".
Original names and spellings are still valid TMI ("원어명 완전배제 아님", 2026-10-25). The test is whether the note matters to this Short. 카스미 added nothing to a passing comparison; notes about the subject and its story did.

**User corrections (사스케 닮은꼴, 2026-10-28).** I offered several TMI candidates and the user picked two.
- The original term "퀸시 (멸각사)" went over "일족의".
- The memo on "적 진영에 합류해" became "유하바하의 후계자로 지명됐었음".
  - Its tense is past because the story later reverses it: "지명됐었음으로 과거로 빠지는게 나을듯? 나중에는 유하바하 토벌하니까". A fact the story later undoes is written as past (~었음), so fans don't read it as current.
  - I placed this memo below the caption by its type (y 960). The user moved it above (y 680): "3번 tmi 위로 넣자". It is a one-line memo of 15 characters, so the below-caption slot is for genuinely long 2–3 line notes. Short one-liners go above, matching the other TMI in the Short.
- Pretendard has no kanji: 滅却師 rendered as empty boxes. Write the Korean reading (멸각사), or check that the font has the glyphs and look at a frame.

**Why:** Claude writes TMI in the edit step from the script and research.
**How to apply:** while writing the narration, move names, original spellings, exact numbers, lore and side jokes out of it into TMI on the caption that mentions them. Keep them short, never polite-register, and place them by type. See `D:/Dev/lumina/docs/shorts-editing/shorts-caption-effect-grammar.md` for placement and styling.


---

## 원본: docs/shorts-editing/shorts-copy-grammar.md

Learned 2026-10-20 from 176 sub/main pairs in "쇼츠 타이틀" graphics across D:\유튜브 채널\Project\**\*.prproj (read-only via lumina.io.prproj.extract).

- Sub + main read as ONE noun phrase: sub = modifier/hook, main = head noun (the topic).
  - ~47% 관형형 sub → noun-phrase main ("공포물보다 무섭다는 / FX전사 쿠루미")
  - ~9% subject sub (-가/-는/-의) → "~이유" or question main ("쇼우 터커가 / 최악의 악역인 이유")
  - ~13% noun phrase → noun phrase apposition ("최강의 경마 애니 / 죠죠 스틸 볼 런")
  - ~7% quote/exclamation/question sub → noun-phrase main ("\"스콜피온 테일!\" / 샤울라와 전갈")
- Sub almost never ends a sentence (5/176). Hearsay "-다는/-라는" is common. Colloquial meme tone (그 잡채, 감다살, 킹받게).
- Main ends in a noun: noun phrase 60%, "~들" 14%, "~이유" 10%, question 9%. The work/character name usually sits in main (83 main-only vs 36 sub-only).
- Lines break only at phrase boundaries.
- Length: sub median 8 Hangul (6–10), main median 7 (5–9); none overflowed, 9% came within 50px of the edge, so the fit check uses margin 0 (hard limits sub 11 / main 9).
- "?" masking is a deliberate reveal effect (user confirmed 2026-10-21). Seen in 11 of the sequences: the title opens with the payoff word as "?" (work/character name or twist noun, e.g. "?????을 파쿠리한 / 옷코츠 유타", "메이가 ??인 이유") and cuts to the full copy exactly at the start of the caption where the narration first says that word. Only one word group is hidden, particles stay visible, one "?" per syllable. Lumina: `shorts_set_copy` with `hide` + `reveal_at` (commit 1a2f96a).

**Why:** Claude is to write the copy itself in the automation pipeline.
**How to apply:** write copy in this grammar, then check it with Lumina `shorts.set_copy` dry_run. Related: `D:/Dev/lumina/docs/shorts-editing/shorts-automation-pipeline.md`, `D:/Dev/lumina/docs/shorts-editing/autonomous-shorts-editing-goal.md`.


---

## 원본: docs/shorts-editing/shorts-project-per-folder.md

The user said "프로젝트는 쪼개지 마셈" (2026-10-22) when I made a .lumina per Short. They then chose "엣지러너 2 안에 통합 프로젝트".

The setup that matches:
- One project per work folder, named after the folder: `…\사이버펑크\엣지러너 2\엣지러너 2.lumina`, from templates/caddynote.lumina.
- Each Short is a copy of the template sequence `시퀀스 02 (쇼츠 제목 폴더명 (복사해서 사용))`, renamed `시퀀스 02 (<short title>)`, as in 팜 술.lumina's `시퀀스 02 (팜술 출시일 떴다)`.
- The base `시퀀스 02 (shorts)` stays empty.
- Narration (`<short title>.wav`) and captions (`<short title>.srt`) live next to the project and keep the Short's title.
- If the folder already has its project, add the new sequence to it instead of creating one.
- A per-Short folder the user asks for ("탈리아 양 폴더 만들고", 2026-10-23) is a subfolder of the work folder for that Short's files (`…\엣지러너 2\탈리아 양\` holds the wav, srt and raw TTS). The sequence still goes in the work's project (엣지러너 2.lumina). I first made `…\사이버펑크\탈리아 양\탈리아 양.lumina` and the user corrected: "엣지러너 2 안에 넣어야지".

- When the top folder is a franchise holding one folder per character (`Project\나루토\나루토`, `…\오로치마루`), the character folder is the project folder: `…\나루토\우치하 사스케\우치하 사스케.lumina`. Folder and project use the character's full name (우치하 사스케, not 사스케). I first made `나루토\사스케\` with `나루토\나루토.lumina` and the user corrected (2026-10-27): "우치하 사스케 하고 안에 사스케 lumina로 해야지". Obsidian mirrors it (`Caddynote\나루토\우치하 사스케\`).

- Setup before footage, as in 엣지러너 2's 탈리아/오미 sequences: a bin `shorts/<short title>` holds `<title>.wav`, the raw take imported and renamed `<title> - 원본.mp3`, and the sequence. The A1 wav is tagged dialogue. V4 is named `TMI`. The A1 clip ends where the voice ends, and V5, V6 and the last caption end 6 frames later.

**Why:** the user's Premiere projects work the same way. 루시.prproj holds several Shorts sequences.

**How to apply:** at step 3 (Lumina setup), open or create the folder's project first, then duplicate and rename the copy sequence. Related: `D:/Dev/lumina/docs/shorts-editing/shorts-automation-pipeline.md`, `D:/Dev/lumina/docs/shorts-editing/lumina-mcp-visible-work.md`.


---

## 원본: docs/shorts-editing/move-sources-not-copy.md

The user said "원본 남길 필요 없음" (2026-10-22) after I copied the Special Talk mp4 from Downloads\hitomi_downloader_GUI into the Short's project folder and kept the original.

**Why:** a second copy of a large video (546 MB here) in Downloads only takes space. The project folder is where sources live.

Windows controlled folder access protects `C:\Users\HETX\Videos` (OBS recordings land there): Git Bash `mv`/`rm` and lumina-engine.exe get "Permission denied", but PowerShell `Remove-Item -LiteralPath` works (2026-10-28). So copy with a hash check, then delete the original from PowerShell.

**How to apply:** move downloaded sources into the project folder, or copy, check that the hash matches, and delete the original. Related: `D:/Dev/lumina/docs/shorts-editing/shorts-project-per-folder.md`, `D:/Dev/lumina/docs/shorts-editing/shorts-automation-pipeline.md`.


---

## 원본: docs/shorts-editing/shorts-marker-workflow.md

User, 2026-10-21: markers mark cut timing; they line captions up with them, and they change a clip's speed so a marker lands in the right place.

The past Premiere projects confirm it (read-only):
- Shorts sequences hold no sequence markers. The markers are clip markers on the footage (master clip markers, source time).
- 263 footage clip markers fall inside clips of Shorts timelines. 87% of them land within 0.1 s of a caption start; the marked moment sits a median 1.5 s into the clip.
- 24% of those clips have speed ≠ 100% (86–174%), stretched so the marked moment hits the caption start.

Lumina has this since commit 7983dd4 (2026-10-21):
- Clip markers on media: `marker.add` with clip_id / media_id. M with a clip selected marks the clip; M in the Source monitor marks the media.
- They show as flags on the clips and snap.
- `clip.align_marker` with mode speed keeps the clip's start, first frame and end; mode move slides the clip.
- Premiere import brings the master clip markers along. Projects imported before that need re-importing to get them.

**Why:** the edit step should put the action moment of a footage clip on the narration's caption start.
**How to apply:** when placing footage, mark (or reuse) the moment, then `clip.align_marker` it onto the caption start. Speed is usually 85–130%; beyond that, move or re-cut instead. Related: `D:/Dev/lumina/docs/shorts-editing/autonomous-shorts-editing-goal.md`, `D:/Dev/lumina/docs/shorts-editing/shorts-sfx-grammar.md`.


---

## 원본: docs/shorts-editing/shorts-upload-title-grammar.md

Read 2026-10-22 from the latest 50 public Shorts of @caddynote (channel UCGtxg9R3VwkKa0QrNYRwfhg, via vidIQ user videos).

The upload title is not the in-video copy (see `D:/Dev/lumina/docs/shorts-editing/shorts-copy-grammar.md`). It is one line, about 10–25 characters, plain spoken Korean.

**Patterns, with top performers**
- Hearsay noun phrase "~다는/라는 ○○":
  - "원작 만화 50화를 7화에 압축한다는 애니" (210k)
  - "애니 속 촌스럽다는 영어 번역명" (196k)
  - "디자인만큼은 깔 수 없다는 캐릭터" (103k)
- A line from the narrator, usually pointing at the character with 얘/이거/이걸:
  - "얘를 두 달이나 기다려야 한다니" (the previous 팜술 Short, 170k)
  - "이거 보고 피고 싶단 생각 하는 거 아니지?"
  - "얘는 미소녀인가 미소년인가"
- Viewer question, which invites comments: "죠죠에서 가장 기묘한 스탠드 디자인은?" (113k), "당신의 최애 ~는?", "가장 ~는?".
- "~인 이유" and "~의 결말": "블루아카 사유리 체형 논란의 결말" (117k), "하이바라의 술식이 '증기'인 이유".
- The work is often left unnamed in the title ("애니", "만화", "게임", "얘") and goes into the tags.

**Why:** Claude proposes the upload title at the end of the pipeline.

**How to apply:** give 3–5 options in these patterns, with one recommendation, plus tags (work name in Korean and English, characters, topic words). A follow-up Short on the same work can echo the earlier title's voice (e.g. 얘를 … ).
