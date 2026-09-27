# 테스트 구조

업무 영역별로 테스트를 찾고 실행한다. `test_*.py` 이름을 유지하되 폴더와 중복되는 업무
접두어는 생략한다. 예를 들어 `test_gimpo_jobs.py`는 `gimpo/test_jobs.py`,
`test_t2_ui.py`는 `t2/test_ui.py`다. 각 하위 폴더를 Python 패키지로 두어 같은 파일명도
pytest 수집과 import에서 충돌하지 않는다.

| 폴더 | 책임 |
|---|---|
| `gimpo/` | 김포 예약 검증, 상태 저장, 작업 실행, 브라우저, 알림, 화면. `fixtures/`에 김포 HTML/응답 자료 보관 |
| `t2/` | 인천 T2 예약 검증, 스케줄러, 저장소, 개인정보 마스킹, 화면 |
| `notifications/` | 공용 알림 설정·전송·백그라운드 발송 |
| `settings/` | 공용 설정 API와 설정 대화상자 |
| `web/` | 앱 설정, HTTP 라우트, 웹 보안 |
| `ui/` | 공통 화면 컴포넌트와 서비스 간 화면 일관성 |
| `infrastructure/` | 테스트 실행기, 시간·usage 집계, 공용 브라우저 격리와 자원 정리 검증 |
| `support/` | 수집하지 않는 공용 보조 코드: 브라우저 fixture, 서버/페이지 scope, 정리, 유한 대기 |

공유 pytest fixture와 외부 HTTP 차단은 루트 `conftest.py`에서 적용한다. `t2_server`는 T2
화면뿐 아니라 공통 화면 비교에서도 사용하므로 루트에 유지한다. 공용 browser plugin은
`tests.support.ui_browser`에서 로드한다. 화면 테스트는 `ui_context` fixture와
`tests.support.ui.open_page`를 사용하며 context는 매 테스트/페이지 scope마다 새로 만든다.

업무 전용 fake와 입력/상태 보조 함수는 해당 영역의 `fakes.py`, `helpers.py`에 둔다.
다른 `test_*.py`나 `conftest.py`를 import해서 보조 함수를 가져오지 않는다.
검증 동작은 기존 테스트에 유지하며, 이 정리는 테스트 목록과 assertion을 줄이지 않는다.

```sh
python scripts/run_tests.py --task-id GIMPO --agent-id controller -- tests/gimpo -q
python scripts/run_tests.py --task-id T2 --agent-id controller -- tests/t2 -q
python scripts/run_tests.py --task-id WEB --agent-id controller -- tests/web tests/settings -q
python scripts/run_tests.py --task-id INFRA --agent-id controller -- tests/infrastructure -q
python scripts/run_tests.py --task-id FINAL --agent-id controller -- -q --durations=10
```

과거 `docs/plans` 측정 보고서와 `.test-runs`의 nodeid/명령은 당시 실행의 원자료다.
과거 기록의 파일 경로는 소급해서 바꾸지 않는다. 현재 실행 명령은 이 문서와
[테스트 실행기 문서](../docs/test-runner.md)를 따른다.
