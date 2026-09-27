# 공용 UI 테스트 자원 관리 회귀 수정

2026-09-28. 공통 UI 셸 병합 후 테스트 코드에서 확인한 회귀를 수정했다.
사용자가 언급한 btw의 구체적인 결론은 이 대화에 전달되지 않아 질문을 남겼으며,
아래 내용은 현재 코드와 실행 기록에서 직접 확인한 문제다.

## 확인된 문제

- RF-01: 공용 `open_page()`와 개별 화면 테스트가 Chromium을 직접 실행했다.
  T5의 모듈별 `ui_browser`가 있어도 실제 UI 테스트는 이를 우회했다.
  함수 끝의 `browser.close()`에 의존해 assertion/setup 실패 시 context 정리를 명시적으로 보장하지 않았다.
- RF-02: 새 `run_app_server()`는 `thread.start()`를 try 밖에서 호출하고 `thread.join()`에 제한이 없었다.
  서버 정리가 실패하면 호출자의 `runtime.close()`/`service.close()`도 실행되지 않았다.
  기존 T2의 부분 setup·유한 정리 보장이 공용화 과정에서 빠졌다.

기준 전체 run `20260928T010044-8c8286b39f`는 329 passed, 1 skipped 뒤
**601.01초에 suite TIMED_OUT**으로 종료했다. 개별 assertion 실패는 없었고 정리는 성공했다.
전체 600초 예산을 소진한 것이며 단일 테스트 hang으로 해석하지 않는다.
완료된 김포 UI report의 setup/call/teardown 합은 295.56초, 김포 browser 검사는 141.91초였다.
이것만으로 Chromium 기동 비용이 전체 지연의 유일한 원인이라고 단정하지 않는다.

## 수정

`open_page()`를 context manager로 바꾸고 `ui_context`를 통해 매 호출마다 새 context를 생성한다.
Chromium/Playwright 수명은 기존 `ui_browser` fixture가 소유한다. 모든 김포 UI, T2 UI,
설정 화면, 공통 UI 모듈 및 화면 일관성 테스트를 같은 수명 계약으로 연결했다.
viewport·timezone·init script·네트워크 경로 차단·기존 assertion을 유지했다.
스크린샷 보조 테스트도 각 viewport를 별도 context로 실행한다.

`run_app_server()`는 서버 생성 이전부터 finally 보호를 시작하고 `cleanup_ui_server()`를 호출한다.
서버 shutdown·join·socket close와 선택적 runtime close를 유한하게 시도하며 앞 단계가 실패해도
다음 정리를 수행한다. 테스트 서버의 정상 shutdown 확인 간격은 0.05초다.
김포/T2 fixture는 자신이 만든 runtime/service를 공용 서버 scope에 넘긴다.
서버만 있는 설정 fixture는 runtime 없이 같은 정리를 사용한다.

## 검증

대표 run `20260928T011603-7dc80a73e7`: **18 passed**, pytest 82.10초,
실행기 wall 83.72초, 입력·환경 불변, 증거 오류·잔류 프로세스 0개.
김포 결제/종료 레이어, 시간대, T2 모바일, 설정 팝업, 공통 UI 및 화면 일관성을 포함한다.
추가 회귀 검사는 실제 context의 예외 후 종료·스토리지/스크립트 격리와
서버 생성 실패 및 본문 예외 후 runtime/socket 정리를 확인한다.
기존 UI assertion이 변경 전과 동일하게 남아 있는지도 AST로 대조했다.

최종 전체 실행은 기존 600초 suite/120초 test 정책을 유지한다.
최종 run·fingerprint·개수·정리·비교 자료는 `.test-runs/t4-completion/final.json`에 기록한다.
기준 실행은 중간에 종료됐으므로 전체 스위트 속도 개선율을 계산하지 않는다.
UI 화면 검사만 같은 완료 nodeid를 대조할 수 있으며 단일 실행 비교는 반복 실험의 대체가 아니다.
실제 브라우저 클라이언트의 비동기 통합 검사(`test_gimpo_browser.py`)는 별도 수명 계약을 유지한다.
