# 감독되는 테스트 실행

테스트는 pytest를 직접 부르지 않고 `scripts/run_tests.py`로 실행하며, 결과를 보고할 때 run_id를 적는다.

업무별 폴더·파일명·공용 fixture 규칙은 [테스트 구조](../tests/README.md)를 따른다.

macOS/POSIX에서 순차 pytest를 실행한다. Python 3.11 이상과 개발 의존성이 필요하다.
Linux는 장애 행렬 검증 전까지 지원 표시를 하지 않는다. Windows·xdist·pytest-timeout은 지원하지 않는다.

```sh
python -m pip install -r requirements-dev.txt
python scripts/run_tests.py --task-id T1 --agent-id implementer -- tests/gimpo/test_jobs.py -q
python scripts/run_tests.py --task-id FINAL --agent-id controller -- -q --durations=10
python scripts/run_tests.py --status <run_id>
```

실행 중인 worktree 입력 파일을 수정하지 않는다. 구현과 검증을 동시에 진행해야 하면 별도
worktree를 사용한다. 실행 전후 해시가 같아도 중간에 수정하고 복원한 사실은 검출하지 못한다.
동일 worktree의 실행은 OS 잠금으로 직렬화하며 상태 조회는 실행 잠금을 기다리지 않는다.
CLI가 사라지면 독립 감독 프로세스가 취소하고 정리를 마칠 때까지 잠금을 유지한다.

startup 30초, collection 60초, 테스트 protocol 합계 120초, 테스트 사이 30초,
최종 정리 30초, 전체 600초를 적용한다. 출력 증가로 마감을 늘리지 않는다.
감독 주기는 0.2초이고 사용자 진행 출력은 30초마다 나온다. 기본 테스트의 60초 체류에는
Python 스택을 요청한다. SIGTERM 후 10초, SIGKILL 후 5초까지 잔류를 확인한다.

정상적으로 오래 걸리는 테스트는 수집 때 검증되는 marker를 사용한다.

```python
@pytest.mark.runner_timeout(180, "실제 기본 120초 감독 정책을 검사하는 장애 주입")
def test_slow_case():
    ...
```

예외는 양의 유한 초와 사유가 필요하고 전체 600초를 넘길 수 없다. 예외의 slow 진단 기준은
예외 예산의 절반이다. 전체 예산은 늘어나지 않는다. 내부 timeout·xdist 설정, marker,
CLI 인자, PYTEST_TIMEOUT 환경 변수는 오류다. 감독 정책을 바꾸는 공개 인자는 없다.
pytest 설정/플러그인 비활성화 override도 CLI에서 거부한다.

`.test-runs/<run_id>/`에 manifest, append-only events, 상태/최종 결과, stdout/stderr,
선택적 stacks를 보존한다. pytest 임시 디렉터리도 실행별로 격리한다.
정상 pytest 코드는 보존하고 timeout은 124, 인프라/정리 실패는 125, 취소는 130이다.
0개 수집은 코드 5로 실패이며 skip/xfail은 수용 조건을 충족했다는 증거가 아니다.
스택 부재는 진단 실패로 기록하고 필수 증거가 완전한 정상 실행의 성공을 바꾸지 않는다.
Playwright trace는 자동 수집하지 않는다. 필요한 재현에서 별도로 활성화한다.

manifest는 tracked·untracked·ignored 설정/fixture의 경로·모드·내용 해시·삭제·심볼릭 링크를
기록한다. 실행 산출물(`.test-runs`, 캐시, logs, data/gimpo), 가상환경, SDD 임시 workspace,
다른 worktree는 제외한다. 이 제외 영역이나 저장소 밖 입력을 읽는 테스트의 결과는 재사용하지
않는다. 외부 입력은 저장소 안의 통제된 fixture로 옮겨 검증한다. 환경에는 Python·패키지·
로드된 plugin·OS·기본 설치 브라우저 revision 및 허용 환경값을 기록한다. 환경 전체나 비밀값을
덤프하지 않는다. `.env`, 사용자 데이터 또는 알려진 비밀 환경 입력이 있으면 수용 조건 검토까지
`reusable=false`로 둔다. 비표준 브라우저 경로와 외부 Python import 경로도 별도 검토 대상이다.

자동 결과 재사용 캐시는 없다. `reusable=true`도 수용 조건에 필요한 테스트를 모두 실행했는지
사람이 확인해야 한다. 리뷰 패키지에는 BASE..HEAD뿐 아니라 미커밋 diff와 untracked 파일을
포함하고, 리뷰 입력 manifest와 검증 fingerprint를 맞춘다. 현행 finishing의 의무 재실행은
`required-by-finishing` 사유로 과제 기록에 남긴다. 역할·모델·리뷰 루프는 기존 SDD 정본을 따른다.

감독 프로세스 자체를 SIGKILL하면 다음 실행/상태 조회가 PID와 생성 시각으로 신원을 확인해
INTERRUPTED로 복구하고 확인 가능한 잔류만 정리한다. 증거가 없어 신원이나 정리가 불확실하면
새 실행을 거부한다. 기록된 PID만 보고 수동 kill하거나 lock 파일을 삭제하지 않는다.
관측 전에 별도 세션으로 이탈한 자식, OS 중단, 감독 자체 사망 직후의 즉시 회수는 보장하지 않는다.

장애 검사는 작은 임시 git/pytest 프로젝트와 독립적인 외부 제한 시간을 사용한다.

```sh
python scripts/run_tests.py --task-id RUNNER --agent-id controller -- tests/infrastructure/test_runner.py tests/infrastructure/test_ui_cleanup.py -q
RUN_DEFAULT_TIMEOUT_ACCEPTANCE=1 python scripts/run_tests.py --task-id DEFAULT120 --agent-id controller -- tests/infrastructure/test_runner.py::test_default_policy_deadline -q
```

첫 명령은 축소 정책 장애 검사, 두 번째는 실제 60초 진단·120초 마감 확인이다.
새 실행기 결함은 실패 nodeid만 재현한 뒤 영향 범위를 검증한다.

구현 API 근거: [pytest hooks](https://docs.pytest.org/en/stable/reference/reference.html),
[faulthandler](https://docs.python.org/3/library/faulthandler.html),
[psutil](https://psutil.readthedocs.io/en/latest/).

검증과 같은 입력의 리뷰 패키지는 다음 명령으로 만든다. dispatch 직전 기록한 BASE를 사용한다.
`committed.patch`, `worktree.patch`, `untracked.patch`, 파일별 해시와 검증 fingerprint를 남긴다.
검증 후 파일이 달라졌으면 생성하지 않는다.

```sh
python scripts/review_snapshot.py --run-id <run_id> --base <BASE>
```

재검증 사유는 실행기 옵션 `--rerun-reason required-by-finishing`처럼 기록한다.
결과의 `elapsed_seconds`는 spawn부터 정리·증거 확정까지의 실제 경과 시간이며,
`test_seconds`는 pytest setup/call/teardown report 시간의 합이다. 둘을 더하지 않는다.

T4 과제·캐시 집계, 단계 시간 기록 및 T5 브라우저 수명 비교는
[시범 측정 기록](plans/2026-09-27-superpowers-pilot.md)을 따른다.
`scripts/workflow_metrics.py`는 로컬 usage의 고정 byte cutoff와 해시를 보존하고,
관측되지 않은 값은 0 대신 null로 기록한다. 가격이나 결제 금액을 추정하지 않는다.

UI 테스트는 모듈마다 Chromium 하나를 쓰고 테스트마다 새 context를 생성·종료한다.
기존 테스트별 브라우저 수명으로 비교하려면 pytest 인자에 `--ui-browser-scope=function`을
붙인다. 모듈별 기본값은 `--ui-browser-scope=module`이다. 제품의 브라우저 수명과는 별개다.

T4 후속 실측과 단계 journal 해석은 [수용 조건 대조](plans/2026-09-28-superpowers-t4-completion.md)를 따른다.
과제 명세의 `phase_journal`을 연결하면 과제별 시간이 채워진다. 미기록/미종료 단계는 null이며,
실제로 발생하지 않은 단계만 `observed_absent_phases`로 명시한다. 새 journal은 부팅 세션 ID를
사용하므로 구형 boot_time 전용 journal에 이어 쓰지 않는다. `measurement_acceptance`는 측정
완결성만 검사한다. 장애 감지와 품질 증거는 함께 검토해야 한다.

공용 화면 테스트는 `ui_context` fixture를 받아 `with open_page(ui_context, base) as (page, errors):`를
사용한다(`tests.support.ui.open_page`). context 옵션이 필요하면 `with ui_context(...) as context:`를
사용한다. 공용 서버는 `run_app_server(app, runtime=owned_runtime)`가 부분 setup 실패부터
서버·스레드·runtime 정리까지 소유한다. [자원 관리 회귀와 수정](plans/2026-09-28-test-fixture-resource-ownership.md).
