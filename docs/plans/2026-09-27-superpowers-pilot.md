# T4 시범 관측과 T5 브라우저 수명 실험

현재 T4 상태: [2026-09-28 추가 측정](2026-09-28-superpowers-t4-completion.md)에서 새 실제 과제 3개의 단계 시간을 확보해 수용 조건을 충족했다. 아래는 2026-09-27 당시의 후향 관측과 중간 판정이다.

2026-09-27. T0~T3 실제 작업의 실행기 복구, UI cleanup, 설정 화면 수정 3개를
후향 관측했다. T5는 새 context 격리를 유지한 채 Chromium 수명만 비교한다.
제품 코드, timeout 정책, xdist, 모델·effort 설정은 변경하지 않았다.

## T4: 확보한 증거와 한계

[입력 명세](2026-09-27-superpowers-pilot-inputs.json),
[실행·과제 집계](2026-09-27-superpowers-pilot-results.json),
[요청별 usage 스냅샷](2026-09-27-superpowers-pilot-usage.json)을 보존했다.
원문 프롬프트나 대화는 복사하지 않았다. JSONL의 완결된 1,543,626 bytes와 SHA-256을
고정하고 response ID별 마지막 usage를 취했다. 동일 cutoff를 다시 읽어 해시와 합계가
일치하는 것을 확인했다. Codex 로컬 `token_usage_record`의 input에는 cached input이
포함되므로 두 번 더하지 않는다. cache creation 0은 기록된 값이며 결제 원장 검증이 아니다.

| 실제 수정 과제 | 관측 창(초) | 연관 run wall(초) | 수정 라운드 | 요청 수 | cache read | 미캐시 입력 | 출력 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 감독 사망 후 복구 | 61.08 | 7.81 | 2 | 5 | 283,136 | 5,546 | 1,779 |
| UI cleanup·thread 시작 실패 | 17.34 | 18.50 | 1 | 3 | 269,184 | 6,150 | 2,324 |
| 설정 화면 readonly 회귀 | 100.09 | 13.39 | 1 | 5 | 500,864 | 15,785 | 1,972 |

관측 창은 입력 명세의 UTC 범위다. 특히 cleanup은 최초 쓰기 도구 실행과 마지막 수정
구간만 포함한다. **관측 창을 전체 구현 시간으로 해석하지 않는다.** 연관 run에는 다른
과제의 테스트도 포함되어 있으며 과제 간 공유된다. 표의 시간을 더하면 중복 계산된다.
모델 usage도 그 창에 기록된 요청을 기준으로 하므로 혼합 요청의 인과적 비용 배분이 아니다.
세 과제 모두 별도 리뷰어 없이 같은 controller에서 수행됐다.

당시 구현·리뷰·대기 단계를 따로 기록하지 않아 해당 값은 `null`이다. 0초로 채우거나
생각 시간을 추정하지 않았다. 따라서 **구현·리뷰·대기 시간을 분리 수집한다는
T4 수용 조건은 당시 완전히 충족되지 않았다.** 이 단계에서는 실제 3개 수정의 관측 자료,
재현 가능한 집계기와 앞으로 사용할 단계 기록 기능까지 구현했다.

| usage 범위 | 요청 수 | 전체 입력 | cache read | cache creation | 미캐시 입력 | 출력 | 입력 캐시 비율 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 구현 세션 전체 | 62 | 5,132,556 | 5,016,064 | 0 | 116,492 | 49,815 | 97.73% |
| 세션 첫 기록 | 1 | 13,996 | 12,032 | 0 | 1,964 | 145 | 85.97% |
| 후속 요청 | 61 | 5,118,560 | 5,004,032 | 0 | 114,528 | 49,670 | 97.76% |

관측 모델은 `gpt-6-astra`, effort는 `high`로 동일했다. 세션 첫 기록은 과제별 첫 호출이나
새 subagent 첫 호출이 아니다. 과거 Claude 스냅샷과 공급자·작업량·컨텍스트가 달라 절감률을
비교하지 않는다. 변경 전의 대응 과제 표본이 없어 전체 워크플로우의 인과적 개선율도 산출하지 않는다.

기존 실행기 run 10개에서 동일 입력·환경·pytest 인자·정책의 중복은 0개였다.
finishing 의무 중복도 이 표본에서는 0개이며, 비실행기 호출 전체를 조사했다는 뜻은 아니다.
실패 후 실행은 복구/입력 증거 수정, 실제 120초 정책 수용 검사, WF-TEST-01 수정과 최종 통합으로
구분된다. 예전 run의 비어 있는 rerun reason을 소급해서 수정하지 않았다.
Minor만으로 연 수정 라운드는 0회였다. 뒤늦게 발견한 중첩 pytest 임시 디렉터리 간섭,
시작하지 않은 thread의 join 오류, 기존 readonly 화면과 구형 테스트 불일치는 기록대로 수정했다.

장애 감지는 기존 실제 정책 수용 run `20260927T223027-3c61724e27`의 중첩 실행을 확인했다.
테스트 protocol 시작 후 60.18초에 진단 요청, 60.42초에 스택 확보를 관측했다.
spawn부터 결과 확정까지 120.68초, `TIMED_OUT`, cleanup 성공, 잔류 0개였다.
현재 supervisor는 그 수용 검사 이후 변경하지 않았다. 이번 시범을 위해 120초 멈춤을 반복하지 않았다.

## 측정 명령

```sh
python scripts/workflow_metrics.py capture --session /path/to/session.jsonl --cutoff 1543626 --output usage.json
python scripts/workflow_metrics.py report --spec docs/plans/2026-09-27-superpowers-pilot-inputs.json --output .test-runs/pilot-replay.json
```

앞으로는 단일 controller가 작업 시작/종료 시 단계를 기록한다. `implementation`, `review`,
`waiting`, `test`를 지원하며 동시 단계·불일치 종료·재부팅을 거친 구간은 거부한다.
미종료 단계는 active로 남긴다. 아래 journal을 report 입력의 `phase_journal`에 연결할 수 있다.
이 기록 기능은 기존 과제의 미측정 시간을 소급해서 메우지 않는다.

```sh
python scripts/workflow_metrics.py phase --journal .test-runs/phases.json --task-id NEXT --phase implementation --event start
python scripts/workflow_metrics.py phase --journal .test-runs/phases.json --task-id NEXT --phase implementation --event stop
```

테스트 실행기 wall은 spawn~cleanup, test_seconds는 setup/call/teardown report 합이다.
단계 journal의 test 대기 시간과 합산하지 않는다. 병렬 구간도 합계 대신 interval union을 사용한다.

## T5: 비교 설계

T4에서 확보한 최종 순차 실행과 UI nodeid별 duration을 기준으로 브라우저 수명을 선택했다.
T4의 모델/리뷰 시간 공백은 이 테스트 성능 비교의 입력으로 사용하지 않는다.
브라우저 context 격리는 [Playwright의 격리 모델](https://playwright.dev/python/docs/browser-contexts)을 따른다.

- 변경 요인: `--ui-browser-scope=function` 대 `--ui-browser-scope=module`.
- 두 조건 모두 동일한 fixture와 context 종료 코드 사용. 테스트 assertion·timeout·서버 수명 동일.
- 범위: Gimpo UI 15개, 설정 화면 1개, 실제 브라우저 격리 2개. 총 18개.
- 쓰기를 멈춘 `.test-runs/t4-t5/benchmark/` 복사본에서 순차 F/M, M/F, F/M 실행.
- 사전 채택 기준: 3쌍 모두 빨라지고 wall 중앙값 5% 이상 개선, 실패·skip·cleanup 오류·입력 변경 0개.
- 쿠키, local/session storage, init script, 팝업, viewport, timezone 및 예외 후 context 종료 검사 포함.
- 반복 실행은 `controlled-lifetime-comparison-N` 사유 기록. 절대 마감·상태/결과 작성자는 기존 supervisor.

실험 중 원본 저장소의 문서·집계기 작업은 계속했으나 측정 복사본은 수정하지 않았다.
첫 기준 실행 동안 원본의 집계기 단위 검사 4개(0.04초 pytest)가 실행됐다. 공유 호스트의
자원 사용을 완전히 통제한 실험은 아니며 반복 표본의 변동도 함께 확인한다.

## T5 결과와 채택

[비교 원자료](2026-09-27-superpowers-browser-benchmark.json)의 wall은 기존 supervisor의
spawn~cleanup 결과 확정 시간이다. pytest 자체 출력 시간보다 약간 길다.

| 쌍 | 테스트별 실행 run | wall(초) | 모듈별 실행 run | wall(초) | 감소 |
|---|---|---:|---|---:|---:|
| 1 | `20260927T225823-721fe6b528` | 139.16 | `20260927T230043-5ea54790f2` | 72.32 | 48.0% |
| 2 | `20260927T230315-5297f0bce9` | 174.80 | `20260927T230156-79b0ff1335` | 77.12 | 55.9% |
| 3 | `20260927T230607-a13968d05d` | 131.69 | `20260927T230820-dc872ea7fc` | 62.20 | 52.8% |

기준 중앙값 139.16초, 후보 중앙값 72.32초로 **48.0% 감소**했다. 6회 × 18개 = 108개 실행이
모두 통과했고 skip·실패·cleanup 오류·남은 프로세스·입력/환경 변경은 0개였다.
모든 run의 입력 manifest, 환경, 정책, 수집 nodeid가 동일하고 수명 인자만 다른지 집계기가 검증했다.
표본 변동은 있으나 3쌍 모두 빨라 사전 채택 조건을 충족했다. 0회 실패는 장기 flaky 비율의
통계적 보장이 아니다. 전체 프로젝트 또는 전체 개발 작업이 48% 빨라졌다고 해석하지 않는다.

`tests/ui_browser.py`의 기본값을 module로 채택했다. context는 `with` 종료 시 예외 여부와
관계없이 닫힌다. 모듈 종료 시 browser와 Playwright driver를 닫고 기존 supervisor가 잔류를
확인한다. 서버·DB·runtime은 기존 테스트별 fixture를 그대로 사용한다. xdist는 적용하지 않았다.
기준 수명은 `--ui-browser-scope=function`으로 다시 실행할 수 있다.

```sh
python scripts/workflow_metrics.py compare \
  --runs .test-runs/t4-t5/benchmark/.test-runs \
  --pair 20260927T225823-721fe6b528 20260927T230043-5ea54790f2 \
  --pair 20260927T230315-5297f0bce9 20260927T230156-79b0ff1335 \
  --pair 20260927T230607-a13968d05d 20260927T230820-dc872ea7fc \
  --output .test-runs/browser-comparison-replay.json
```

집계기 회귀 검사 7개가 `20260927T230956-705f3624fa`에서 통과했다.
중복 응답·잘린 로그·기록되지 않은 usage·겹친 wall 구간·단계 불일치·다른 입력/환경·변형 인자·
재사용된 run·실패 실행의 채택 거부를 검증한다. 전체 통합 결과의 정본은
`.test-runs/t4-t5-final-report.json`에 최종 run ID와 fingerprint로 남긴다.
