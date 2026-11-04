# MCP 시작·유지와 GUI 프로젝트 연결

MCP 서버 연결, GUI 엔진 연결, 작업할 프로젝트 선택은 각각 다른 상태다.
기본 MCP는 GUI가 없어도 시작하고 도구 목록·상태 확인·소스 검색을 제공한다.
프로젝트 편집은 GUI 엔진에서 실행한다. GUI 연결 실패를 헤드리스 편집으로 대체하지 않는다.

## 처음 시작되는 과정

1. 에이전트 호스트가 등록된 MCP 설정을 읽고 `uv run --project ... python -m lumina mcp`를 자식 프로세스로 실행한다.
2. 호스트와 MCP가 표준 입출력(stdio)으로 초기화하고 도구 목록을 교환한다. MCP는 요청을 기다리는 이벤트 루프를 유지한다. 매 도구 호출마다 서버를 새로 만드는 방식이 아니다. 연결을 공유하는 범위와 시작 시점은 호스트가 관리한다.
3. 이 시점에는 문서 엔진을 생성하거나 GUI를 자동 실행하지 않는다. `get_editor_status`도 창을 열지 않는다.
4. `ensure_editor(project_path)` 또는 GUI 엔진이 필요한 요청이 들어오면 실행 중인 GUI의 `~/.lumina/engine.json`에서 주소·토큰을 읽고 WebSocket으로 연결한다. GUI가 없으면 설치된 Lumina를 실행하고 엔진 준비를 기다린다.
5. Electron GUI가 자신의 Python 엔진을 실행한다. GUI와 MCP는 이 엔진의 같은 문서·명령·이벤트를 사용한다.

MCP는 독립적인 Windows 상주 서비스가 아니다. 호스트가 stdio 연결을 끝내거나 프로세스를 종료하면 MCP도 종료된다. MCP 종료는 GUI를 종료하지 않는다. Windows에서는 기존 Explorer 데스크톱을 통해 GUI를 실행해, MCP 호스트가 Job Object로 자식 프로세스를 일괄 정리해도 GUI가 같이 종료되지 않도록 한다. 사용자 지정 홈은 `--lumina-home`으로 전달한다. GUI를 닫으면 그 GUI의 엔진은 종료되지만 MCP 서버는 대기할 수 있다. 다음 작업은 GUI를 다시 연결하고 대상 프로젝트를 준비한 뒤 진행한다.

## 프로젝트 준비의 세 경우

| 현재 상태 | `ensure_editor(project_path)` 동작 | 보존되는 것 |
|---|---|---|
| GUI 꺼짐 | GUI 실행 → 엔진 준비 대기 → 대상 파일 열기 | 디스크의 대상 프로젝트. 실행 실패 시 편집하지 않고 오류 반환 |
| GUI 켜짐, 대상 프로젝트가 이미 열림 | 해당 열린 프로젝트를 활성화. 디스크에서 다시 읽지 않음 | 미저장 편집·현재 문서·Undo 기록 |
| GUI 켜짐, 다른 프로젝트가 열림 | 다른 프로젝트를 열어 둔 채 대상 프로젝트를 추가로 열고 활성화 | 기존 프로젝트와 그 미저장 편집 |

대상 프로젝트가 열린 탭 중 하나에 있지만 비활성인 경우도 두 번째 경우다.
이름이 같은 프로젝트를 혼동하지 않도록 실제 파일 경로로 찾는다.
경로를 생략한 `ensure_editor()`는 GUI 연결과 현재 활성 프로젝트 확인까지만 한다.
새 프로젝트가 필요하면 `new_project`로 명시한다.

준비 이후 사용자가 다른 프로젝트를 활성화하면 다음 편집은 `TARGET_CHANGED`로 중단한다.
에이전트가 `ensure_editor(project_path)`로 대상을 다시 확인해야 계속할 수 있다.
GUI 종료·재시작으로 요청이 끊겼을 때는 이미 실행됐을 가능성이 있는 편집을 자동 재전송하지 않는다.

## 기본과 예외

- 기본: `GuiEngine`. MCP가 먼저 떠 있어도 헤드리스 문서는 만들지 않는다.
- 명시적인 무인 작업 예외: MCP 환경에 `LUMINA_HEADLESS=1`을 설정하면 기존 헤드리스/GUI 전환 방식을 사용한다. 사용자가 요청한 경우에만 사용한다.
- CLI의 `render` 및 기존 `call`/`exec`는 별도 경로다. 이 문서의 GUI 우선 정책은 에이전트 MCP 기본값에 적용한다.
- GUI 실행 파일 지정: `LUMINA_EDITOR_EXE`. 기본 탐색은 설치본 → 사용 가능한 로컬 빌드다.
- `LUMINA_HOME`을 지정했다면 GUI와 MCP가 같은 값을 사용해야 한다. 테스트는 별도 홈으로 실제 편집 작업과 격리한다.
- MCP 소스나 설정을 갱신한 뒤 이미 실행 중인 MCP는 한 번 재연결해야 새 코드를 읽는다.

실제 경로는 `engine/lumina/mcp_server.py`, `engine/lumina/client.py`, `app/src/main/index.ts`, `engine/lumina/server.py`에서 확인한다.

검증: `app/test/mcp-gui-sync.mjs`는 실제 MCP stdio와 GUI를 연결해 열린 대상의 미저장 편집, 다른 프로젝트 보존, 이벤트 복구, 재실행을 확인한다. `app/test/mcp-auto-start.py`는 GUI 없는 상태의 자동 실행과 MCP 종료 후 GUI 유지를 확인한다. 후자는 Windows에서 `LUMINA_EDITOR_EXE`를 테스트 GUI로 지정해 실행하며, 실제 사용자 프로젝트 대신 격리된 임시 프로젝트를 사용한다.
