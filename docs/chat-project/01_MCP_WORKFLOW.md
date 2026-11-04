# Chat에서 Lumina 편집 실행

2026-10-31 갱신. Chat → GPT Bridge → 로컬 Lumina MCP(stdio) → Lumina GUI 구조의 운영 기준이다. Lumina MCP 자체는 로컬 프로세스이며 Chat에 직접 연결할 원격 HTTP 서버가 아니다. Bridge는 중계·파일 작업을 맡고, GUI 연결·대상 보호·편집 동작은 Lumina가 맡는다. 도구 스키마 전체를 정적 파일로 복제하지 않는다. 호출 시 연결된 도구 정의를 확인한다.

## 연결과 역할

| 목적 | 사용할 연결 | 기준 |
|---|---|---|
| 프로젝트/시퀀스 조회·편집·렌더·저장 | Bridge `lumina_list_tools` / `lumina_call_tool` → Lumina MCP | GUI의 같은 문서에서 작업 |
| 로컬 문서·대본·폴더 검색/읽기 | GPT Bridge | 먼저 `get_workspace_info` |
| 실제 소스 파일 이동 | GPT Bridge `move_file` | 이동 결과 확인 후 Lumina에 가져오기/재연결 |
| 음성 정렬·자막 | Lumina 내부 speech_* 및 caption_* | 정렬 후 문구·분할·시작점까지 검수 |

2026-10-31 Bridge 재시작 후 `get_workspace_info`로 확인한 루트는 다음과 같다. 작업할 때 다시 조회한다.

| workspace | 로컬 루트 | 용도 |
|---|---|---|
| `default` | `D:\Dev` | 개발 폴더와 편집 지침 |
| `YouTube` | `D:\유튜브 채널` | 영상 프로젝트와 정리된 소스 |
| `Downloads` | `C:\Users\HETX\Downloads` | 일반 다운로드 |
| `Videos` | `C:\Users\HETX\Videos` | 비디오 폴더·OBS 녹화 소스 |
| `YouTubeVideos` | `C:\Users\HETX\Downloads\hitomi_downloader_GUI\hitomi_downloaded_youtube` | 다운로드한 유튜브 영상 |
| `Obsidian` | `D:\Obsidian` | 대본·노트 |
| `flower` | `C:\Users\HETX\flower` | Unity 프로젝트 |

Bridge 파일 인자는 해당 루트의 **상대 경로**, Lumina의 프로젝트 경로는 로컬 **절대 경로**를 사용한다. `YouTube`는 프로젝트 폴더이고 `YouTubeVideos`는 다운로드 소스 폴더이므로 혼동하지 않는다.

예: Bridge `read_file(workspace="default", path="lumina/docs/editing-grammar/README.md")`. YouTube 루트에서 프로젝트를 찾을 때는 `Project/...`부터 탐색한다. 파일명·경로는 실제 목록을 보고 확정한다.

녹화 소스는 `list_directory(workspace="Videos", path="", depth=1)`, 유튜브 다운로드 소스는 `list_directory(workspace="YouTubeVideos", path="", depth=1)`로 탐색한다. 하위 폴더가 있으면 실제 목록에 나온 경로로 좁힌다. 이동을 요청받으면 해당 workspace를 출발지로, `YouTube`의 확인된 프로젝트 하위 폴더를 목적지로 지정한다. 목적지 부모 폴더가 있어야 하며 기존 파일을 덮어쓰지 않는다.

폴더 추가 후 현재 Chat의 도구 정의에 새 workspace가 보이지 않으면 GPT Bridge 연결/도구 목록을 새로고침한 뒤 다시 확인한다. 이름을 임의로 바꾸거나 `..` 경로로 우회하지 않는다.

## 첫 작업

아래의 Lumina 도구명은 Chat에 직접 노출된 함수명이 아니다. 먼저 `lumina_list_tools(name="get_editor_status")`처럼 정확한 이름으로 현재 스키마를 확인하고, `lumina_call_tool(name="get_editor_status", arguments={})`처럼 중계 호출한다. 다른 Lumina 도구도 동일하다. 파일 도구는 Bridge에 직접 호출한다.

1. `get_editor_status()`로 GUI 상태만 조회한다.
2. 사용자가 지정한 실제 `.lumina` 경로로 `ensure_editor(project_path=...)`를 호출한다. GUI가 없으면 실행하고, 열린 대상이면 미저장 상태를 유지하며 활성화한다.
3. `list_projects()`와 `get_project()`로 경로·활성 프로젝트·시퀀스 목록을 확인한다.
4. 대상 시퀀스를 `sequence_set_active`로 열고, 명시한 ID로 `get_timeline_state`를 읽는다. 필요한 클립 속성은 `get_clips`로 확인한다.
5. 컷 계획 → 소스 확인 → 작은 편집 단위 → 결과 재조회 → 합성 프레임 검수 → 저장 순으로 진행한다.

예: `ensure_editor`의 현재 정의를 확인한 뒤 `lumina_call_tool(name="ensure_editor", arguments={"project_path":"<실제 절대 경로>"})`로 준비한다. 이미 열린 프로젝트를 매번 `open_project`로 다시 열지 않는다.

프로젝트 이름만 같다는 이유로 대상을 확정하지 않는다. 현재 활성 탭이 사용자가 요청한 프로젝트라는 보장도 없다. 새 프로젝트가 필요한 경우에만 `new_project`를 사용한다.

## 편집과 오류 복구

- `begin_transaction(label=...)` 후 관련 변경을 수행한다. 확인된 실패이고 현재 열린 transaction이 자신의 작업이면 `rollback_transaction`, 성공이면 `commit_transaction`한다. 끊김으로 transaction 상태가 불명확하면 먼저 재조회한다. 다른 사람의 작업을 임의로 undo하지 않는다.
- `clip_place`/`clip_move` 반환의 `covered`를 확인한다. 정지 이미지의 기본 길이, 효과음 꼬리, 한 프레임 초과로 이웃 클립이 지워질 수 있다.
- `TARGET_CHANGED`는 잘못된 프로젝트에 계속 쓰지 말라는 신호다. `ensure_editor`로 요청 대상을 재확인하고 타임라인을 다시 읽는다.
- 시간 초과는 미실행의 증거가 아니다. 중복 배치·중복 import·중복 export를 피하도록 현재 클립/미디어/작업 목록을 확인한다.
- GUI 연결 실패를 `.lumina` 직접 수정이나 헤드리스로 우회하지 않는다. 도구가 없는 기능은 누락 기능과 필요한 입력을 보고한다.
- 파일 이동은 Bridge `move_file`의 결과를 확인한다. 기존 등록 미디어라면 현재 미디어 ID를 조회해 `media_relink` → 경로 검증 → `save_project`한다. 경로 오류를 고치려고 장면을 삭제하거나 다른 소스로 바꾸지 않는다.

## 자막·음성 준비

### Lumina 내부 음성 기능 우선 사용

새 텍스트 패널은 대본·캡션·그래픽을 구분하고 모든 자막 트랙을 표시한다. Chat도 같은 엔진을 Bridge `lumina_call_tool`로 호출한다. 현재 도구 정의에서 아래 도구가 제공되는지 먼저 확인한다. 구형 실행 중인 앱은 업데이트 후 다시 연결해야 한다.

1. `speech_status`로 런타임을 확인하고 대상 시퀀스 및 실제 내레이션 오디오 트랙 ID를 읽는다.
2. `speech_start(sequence_id=..., audio_track_id=..., mode="retime", caption_track_ids=[...])`로 최신 자막의 문구·분할·ID를 유지하며 음성 정렬한다. 새 받아쓰기는 `mode="transcribe"`, 작성한 대본 정렬은 `mode="script"`다. 롱폼은 `shorts=false`다.
3. 반환된 job ID로 `speech_job`을 조회한다. `preview`는 분석 완료이며 아직 편집하지 않은 상태다. 결과의 문구·ID·경계·겹침을 검수하고 주요 지점을 재생해 확인한다. 글자 수 비례 배분을 음성 정렬로 대체하지 않는다.
4. 검수한 작업은 `speech_apply(job_id=...)`로 한 번에 적용한다. Undo 가능하며 분석 이후 타임라인이 달라졌으면 적용을 거부한다. 상태를 다시 읽고 필요하면 재분석한다. 대상 프로젝트를 확인하고 저장한다.
5. 자막 분할은 `caption_split_text`를 사용한다. 현재 문구를 `expected_text`로 넣고 `offset`은 Unicode 문자 기준이다. 저장된 음성 정렬 정보가 없으면 실제 확인한 `at` 시각을 명시한다. 커서 앞뒤 문구와 타임라인 경계를 동시에 분할한다. 마침표·띄어쓰기·줄바꿈 수정은 음성 정렬 정보를 유지한다. `caption_merge_next(direction="previous")`는 이전 자막과 병합한다.

대본 정렬과 기존 자막 재정렬은 자막 끝을 다음 자막 시작에 연결해 중간 빈 구간을 없앤다. 이는 엔진의 기본 정렬 범위다. 최종 표시 정책은 첫 자막 0프레임(명시한 무자막 인트로 제외)이며, 적용 후 별도로 확인·보정한다. 마지막 끝은 실제 발화와 포맷별 끝 기준을 따른다. GUI는 Enter 분할, Shift+Enter 강제 줄바꿈, 맨 앞 Backspace 병합을 지원하며 문구 수정은 자동 반영된다. 프로젝트 파일 저장은 별도다.

내부 기능은 선택한 내레이션 트랙의 컷·속도·배치를 반영해 분석하며 Phrase 서버를 사용하지 않는다. 자세한 설치·운영 설명은 `D:\Dev\lumina\docs\text-panel.md`다. 외부 Phrase 자동 우회는 사용하지 않는다.

### 외부 Phrase 절차 폐기

현행 작업은 위의 Lumina 내부 speech 절차와 아래 자막 품질 후처리를 사용한다. 옛 Phrase 프로젝트·prepare_retime·외부 SRT 왕복은 기본/자동 대체 경로에서 제외한다.

기존 음성 처리 기준은 Hard Limiter ceiling -4 dB, boost 0, look-ahead 7 ms, release 100 ms다. 임의 EQ/loudnorm을 추가하지 않는다. 이 수치를 구현하는 도구가 현재 없으면 미처리 상태로 남긴다. 과거 ffmpeg 근사식·개발 CLI를 Chat에서 실행할 수 있다고 가정하지 않는다.

## 검수 기록

| 항목 | 기록할 내용 |
|---|---|
| 대상 | 프로젝트 절대 경로, 시퀀스명/ID, fps |
| 변경 | 구간, 목적, 실제 반영 내용 |
| 화면 근거 | 원본 완성 출력 / Lumina 합성 / 소스 프레임 / 타임라인 |
| 수행한 검수 | 정지 프레임 / 연속 재생 / 직접 청취 / 음량 측정을 각각 표시 |
| 남은 문제 | 없는 소스, 미지원 효과, 오프라인, 미검수 구간 |
| 저장/출력 | 저장 성공, 출력 작업 상태, 결과 파일 경로 |

`export_media` 요청 성공은 완료가 아니다. 반환된 작업 상태와 필요 시 `list_jobs`로 완료·실패를 확인한다. 출력 검증 성공과 직접 시청·청취는 별개다.

## 과거 문서와 충돌할 때

- `lumina_call_tool`이 Chat의 기본 실행 경로다. `lumina_live_status`는 Bridge의 보조 상태 도구이며 GUI 가시성이나 전체 MCP 정상 동작의 증거로 삼지 않는다. 정상 경로는 중계된 `get_editor_status`와 `ensure_editor`다.
- `clip.place`, `shorts.set_copy` 같은 과거 표기는 개념·내부 명령명일 수 있다. 현재 MCP의 `clip_place`, `shorts_set_copy` 등 실제 도구를 검색한다.
- CLI, Python, ffmpeg, 로컬 개인 메모리 읽기는 Chat에서 자동 제공되는 기능이 아니다. 필요한 자료는 Bridge 또는 업로드된 파일로 읽는다.
- 공통 문법의 의미 중심 컷 판단이 예전 빈도 통계보다 우선한다. 쇼츠의 사용자 확정 최소 길이·마커 조건은 보존하되, ‘항상 두 자막마다 컷’, ‘4초를 넘으면 무조건 컷’으로 바꾸지 않는다.
- 업로드된 참고 기록의 시퀀스 ID·미디어 개수·미완료 목록은 과거 스냅샷이다. 현재 프로젝트 상태로 취급하지 않는다.

## 문서 원본

저장소 기준 `docs/editing-grammar/README.md`, `docs/editing-grammar/lumina-workflow.md`, `docs/mcp-lifecycle.md`, `docs/shorts-editing/README.md`를 사용한다. 현재 도구 정의와 구현 확인 위치는 `engine/lumina/mcp_server.py`, `engine/lumina/client.py`다. 코드 전체는 Chat 프로젝트에 상시 업로드할 필요가 없다.

## 연결 장애 기록과 재검증

2026-10-31: 도구 검색에는 `get_editor_status`가 있었으나 호출은 `UNKNOWN_METHOD: editor.status`로 실패했다. 원인은 Bridge 설정의 `lumina.liveOnly=true`가 실행한 구형 `LiveEngine`이다. 이 계층은 Lumina의 `GuiEngine`을 거치지 않고 내부 메서드를 GUI 엔진으로 직접 전달했다. `ensure_editor`와 대상 보호 역시 같은 계층을 필요로 하므로 상태 조회만 임시 대체하지 않는다.

Bridge 사용자 설정을 백업하고 `liveOnly=false`, 실행 인자를 `uv run --project D:\Dev\lumina\engine python -m lumina mcp`로 바꿨다. 이는 헤드리스 편집을 켠 것이 아니다. 현재 Lumina의 기본은 `GuiEngine`이며 `LUMINA_HEADLESS=1`을 설정하지 않는다. Bridge를 재시작한 후 중계된 `get_editor_status`가 `mode=gui`, `connected=true`, `headless=false`를 반환했고 프로젝트 개요 조회도 성공했다.

연결 결과는 도구 검색 성공 / GUI 엔진 연결 / 프로젝트 읽기 / 합성 이미지 반환 / 편집 / 저장·내보내기를 분리해 기록한다. GUI 엔진 연결은 창이 현재 화면에 보이거나 가려지지 않았다는 보장이 아니다. `visibleWindow=not_checked`는 미확인이며 그 자체가 연결 실패는 아니다. 사용자 프로젝트를 시험 삼아 변경하지 않는다. 쓰기 테스트가 필요하면 별도 테스트 프로젝트로 범위를 명시한다.


## 연속 프레임 검수 — 2026-10-31 추가

Bridge의 lumina_list_tools에서 inspect_source_frames, inspect_timeline_frames의 실제 정의를 조회한 뒤 lumina_call_tool로 호출한다. 로컬 Lumina MCP에 구현되므로 기존 MCP 프로세스는 재연결해야 한다. GUI 재설치는 필요 없다.

- 원본: `inspect_source_frames({path, start_frame, count:12, step:1})`. 실제 이미지, 0기준 표시 순서 프레임 번호, PTS, RGB 변화량. 최대 24장, 시트당 6장. EOF로 부족하면 complete=false, 범위 밖이면 오류. 정확한 번호를 위해 파일 처음부터 디코딩하므로 긴 파일은 시간 초과할 수 있다.
- 합성: `inspect_timeline_frames({sequence_id, start_frame, count:12})`. 명시한 시퀀스의 연속 합성 프레임, ticks, timecode. 소스 번호를 그대로 넣지 않는다. 검사 중 프로젝트/타임라인이 바뀌면 결과를 폐기한다.
- 예: 시퀀스 300번 프레임에서 새 컷이면 start_frame=294, count=12로 294~305 확인. [in,out)에서 299는 이전 컷 마지막, 300은 다음 컷 처음이다. 소스와 시퀀스의 fps/속도 매핑은 별도 확인.
- step>1은 표본이며 1프레임 혼입 없음의 증거가 아니다. 변화량은 샷·인물·결함 판정 점수가 아니다. complete=true도 품질 통과를 뜻하지 않는다.
- DB 후보 → 실제 소스 범위 훑기 → 양 끝/의심 위치 연속 프레임 → 의미·음성 기준 배치 → 합성 경계 → 정상 속도 시청 순으로 검수한다. 혼입/인지 시간/끝무렵 동작 분류는 편집 문법을 따른다. 도구 자체는 자동 트림/배치를 하지 않는다.
- 영상·음성을 실제로 볼/들을 수 없는 환경이면 리듬·동작 완료·청취는 미검수로 남긴다. 정지 프레임으로 정상 속도 검수를 대체하지 않는다.


## 자막 품질 후처리 필수 — 2026-10-31

음성 정렬 성공과 자막 편집 완료는 다르다. retime은 문구·분할을 유지하므로 긴 자막·끝 쉼표/마침표를 자동 수정하지 않는다. script도 입력 줄을 보존한다. 최신 기준은 통합 편집 문서의 「자막 완료 기준」을 따른다.

1. 현재 본 자막을 읽어 첫 시작 프레임·공백/겹침·종결 쉼표/마침표·긴 구간을 전수 검사한다.
2. 화면용 문구 끝 쉼표/마침표를 정리하고 의미 단위로 분할한다. 분할 시간이 불명확하면 먼저 내부 speech 정렬로 음성 위치를 확보한다. 사용자 확정 분할·스타일은 보존한다.
3. 최신 분할로 필요한 retime을 수행하고 preview 전 행을 검수한다. 시간만 정렬하라는 요청이 아닌 자막 품질 개선 요청에서는 과병합을 그대로 보존하지 않는다.
4. 적용 뒤 일반적인 시작 영상은 첫 자막을 시퀀스 0프레임에 맞춘다. 명시한 무자막 인트로만 예외다. 엔진이 첫 음성 검출 시각을 유지하는 것은 자동 동작이며 원하는 최종 표시 시작과 다를 수 있다. 첫 발화 누락 여부를 먼저 확인하고 별도 편집 명령으로 보정한다.
5. 연속 내레이션의 앞 out=다음 in, 의도하지 않은 공백·겹침 0개, 제거 대상 종결부호 0개를 확인한다. 롱폼 3.5초 초과 전수 검토, 5초 초과/여러 문장 우선 분할 검토. 강제 균등 분할은 금지한다.
6. 처음·분할 경계·최장 자막·끝부분의 합성과 실제 음성을 검수한 뒤 저장한다. 첫 프레임, 공백/겹침/종결부호 수, 분할 전후 개수, 최장 구간 및 유지 예외, 미청취 여부를 보고한다.

Phrase 서버·옛 Phrase 프로젝트·외부 SRT 왕복을 기본 경로로 사용하지 않는다. 내부 speech 도구가 없거나 실패하면 런타임/연결을 진단하고 미완료로 남긴다. 사용자 요청 없이 외부 Phrase로 자동 우회하지 않는다. 정책이 엔진에서 자동 수행된다고 가정하지 않는다.


## 2026-11-01 구현 변경: 새 버전 자동 후처리

새 Lumina 버전은 모든 speech 모드의 preview 생성 시 첫 자막 0프레임·연속 구간 연결·끝 문장부호 정리·음성 문장/절 경계 분할을 자동 처리한다. 위의 retime 문구/분할 보존 및 수동 후처리 설명은 구버전 기준이다. 기존 경계를 합치거나 문구를 재작성하지 않지만 긴 행은 분할되며 첫 조각만 원래 ID를 유지한다. 원래 트랙·스타일은 보존한다. preview의 caption_quality=gapless-clean-split-v1과 quality_warnings를 확인한다. 안전한 경계가 없는 긴 행은 자동 처리 완료로 간주하지 않는다. 적용 전 검수와 적용 후 실제 음성/합성 검수는 계속 필요하다.
