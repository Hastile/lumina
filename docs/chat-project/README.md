# Chat 프로젝트에 넣는 캐디노트 편집 자료

이 폴더는 Chat에서 **GPT Bridge로 로컬 Lumina MCP를 중계해 편집**하도록 준비한 배포 패키지다. 소스 코드를 업로드해 MCP 연결을 대신하는 방식이 아니다.

## 넣는 방법

1. `PROJECT_INSTRUCTIONS.md` 본문을 Chat 프로젝트의 지침에 붙여 넣는다.
2. `01_MCP_WORKFLOW.md`부터 `05_REFERENCE_EVIDENCE.md`까지 다섯 파일을 프로젝트 참고 파일로 추가한다. 쇼츠만 작업해도 공통 문법과 실행 절차는 함께 넣는다.
3. 해당 Chat에 GPT Bridge를 연결하고 `lumina_list_tools`와 `lumina_call_tool`이 보이는지 확인한다. Lumina MCP는 로컬 stdio 서버이므로 Bridge가 중계한다. 지침과 파일만 추가했다고 도구가 연결되는 것은 아니다.
4. 아래 시작 요청으로 변경 없는 연결 점검부터 한다.

```text
프로젝트 지침과 01_MCP_WORKFLOW.md, 02_COMMON_GRAMMAR.md를 읽어줘.
Bridge의 lumina_list_tools로 실제 정의를 확인하고 lumina_call_tool로 get_editor_status를 호출해줘.
GUI가 연결되어 있으면 list_projects와 get_project로 열린 프로젝트를 조회해줘.
아직 편집하거나 저장하지 말고, 보이는 프로젝트 경로·시퀀스와 사용 가능한 도구를 알려줘.
```

실제 작업 요청 예:

```text
<실제 .lumina 절대 경로>의 ‘시퀀스 02 (<제목>)’를 편집해줘.
03_SHORTS_GRAMMAR.md를 읽고 현재 타임라인과 대본을 확인한 다음,
컷 계획에 따라 V1 소스를 배치하고 경계·프레이밍을 검수해서 저장해줘.
```

## 자료 구성

| 파일 | 역할 |
|---|---|
| PROJECT_INSTRUCTIONS.md | 지침 칸에 넣는 행동·판단 기준 |
| 01_MCP_WORKFLOW.md | Bridge를 통한 로컬 MCP 연결, 파일 도구 분담, 복구, 저장·검수 |
| 02_COMMON_GRAMMAR.md | 직관성·도입·의미 단위 컷·소스 증거 |
| 03_SHORTS_GRAMMAR.md | 쇼츠 시청자, 사용자 수정, 컷·자막·SFX·TMI·카피·폴더·마커 |
| 04_LONGFORM_GRAMMAR.md | 롱폼 설명 구조, 자막 폭·분할 |
| 05_REFERENCE_EVIDENCE.md | 참고 편집 분석과 검수 한계, 외부 연출 연구 기록 |
| SOURCES.json | 원본 경로·SHA-256·배포 파일 대응표 |
| build_bundle.py | 원본 문서 변경 후 참고 파일을 다시 만드는 유지보수 스크립트 |

02~05는 저장소 원문을 묶은 스냅샷이다. 각 절에 원본 경로를 붙이고, 원본의 상대 링크·위키 링크는 로컬 출처 경로로 바꿨다. 이미지 자체는 포함하지 않는다. 화면 근거가 필요하면 Bridge로 해당 이미지를 읽거나 Lumina에서 확인한다. 텍스트의 이미지 경로만 읽고 이미지를 봤다고 하지 않는다.

원문에 남은 Codex/Claude 이름은 자료의 이관 이력이다. 예전 CLI 실행법·개인 메모리 경로·통계는 현재 Chat의 기능이나 강제 규칙이 아니다. 현재 실행은 01과 연결된 도구 정의를 따른다. `shorts-automation-pipeline.md`는 과거 개발 상태와 CLI 설명이 많아 전체 업로드에서 제외하고 필요한 운영 내용만 01에 정리했다.

프로젝트별로 대본, 확정 사용자 수정, 사용할 프로젝트·시퀀스 경로, 필요한 소스 목록을 추가한다. 대용량 영상·전체 앱 소스·캐시·인증 토큰·engine.json은 이 기본 패키지에 넣지 않는다. 작업 중 실제 프로젝트 상태는 MCP로 읽는다.

## 갱신

저장소 루트에서 `python docs/chat-project/build_bundle.py`를 실행하면 02~05와 SOURCES.json을 갱신한다. Chat에 추가한 파일도 새 버전으로 교체한다. 업로드 파일은 로컬 수정과 자동 동기화된다고 가정하지 않는다. 01과 지침은 MCP 동작이 변경될 때 검토한다.

연결 수정 후 확인(2026-10-31): Bridge의 `lumina_call_tool`을 통한 `get_editor_status`와 `get_project`가 성공했다. 구형 liveOnly 중계 계층을 끄고 Lumina의 정식 GUI 우선 MCP로 연결했다. 현재 열린 편집 프로젝트는 변경·저장하지 않았다. 실제 Chat에서는 갱신한 지침으로 다시 연결을 확인한다.

Chat 프로젝트에서 지침·파일·연결 소스를 공유하는 구조는 [OpenAI 공식 프로젝트 문서](https://learn.chatgpt.com/docs/projects)를 참고했다. 실제 도구 사용 가능 여부는 해당 Chat의 연결 상태로 확인한다.
