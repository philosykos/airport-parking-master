# 워크플로우 개선 구현 기록

[설계 r3](2026-09-27-superpowers-workflow-improvement.md)의 초기 지원 범위를 구현했다.
실행 명령과 증거 계약은 [사용 문서](../test-runner.md)에 있다.

| 과제 | 적용 범위 |
|---|---|
| T0 | 전역 작업 배분을 선택된 워크플로우가 없는 경우의 기본값으로 한정. 직접 재실행 의무를 원본 검증 증거 확인으로 교체. SDD general-purpose의 명시 모델을 훅에서 보존. effort 캐시 단정 축소 |
| T1 | CLI/독립 감독/관측 plugin, 단조 마감, 취소/회수/중단 복구, OS 잠금, 입력·환경 증거, 리뷰 스냅샷, 장애 주입 검사 |
| T2 | UI fixture의 부분 setup 실패·shutdown 예외에서도 전체 정리를 시도. 유한 thread join과 남은 thread의 명시적 실패 |
| T3 | 실행·상태 조회·리뷰 패키지 진입 문서와 CLAUDE.md 링크 |
| T4 | [추가 실측](2026-09-28-superpowers-t4-completion.md)에서 실제 개발 도구 과제 3개의 구현/테스트/리뷰/대기·worker·캐시를 기록하고 수용 조건 충족. 기존 후향 관측의 미측정 값은 보존 |
| T5 | 18개 UI·격리 테스트 3쌍 비교에서 wall 중앙값 48.0% 감소, 108개 실행 전부 통과. 모듈별 Chromium 수명을 채택하고 테스트별 context 격리 유지. xdist 미도입 |

전역 변경 대상은 `~/.claude/CLAUDE.md`와 `~/.claude/hooks/agent-model-guard.sh`다.
저장소 밖에 적용한 정확한 차이는 [패치](2026-09-27-superpowers-global-instructions.patch)에 보존한다.
고정 역할 사용자 에이전트의 제한과 설치된 플러그인 캐시는 수정하지 않았다.
합성 Agent 입력으로 general-purpose의 명시 모델 유지와 sonnet-impl의 고정 모델 정책을 확인했다.
실제 공급자 API 모델/effort 전달·캐시 usage를 검증한 결과로 해석하지 않는다.
verification/finishing의 정본 수정은 설계대로 플러그인 관리 소스의 후속 과제로 남긴다.

검증 결과의 정본은 `.test-runs/<run_id>/result.json`이다. 각 결과에는 실행 명령, 입력 manifest,
수집 nodeid, 원래 pytest 코드, 시간, 진단, cleanup, 재사용 제한이 있다. 구현 중의 실패 재현과
수정 범위 검증은 task_id로 구분한다. 최초 복구 검사에서 프로세스 신원 확인 직후 소멸하는
경합을 발견해 정상 소멸로 처리했으며, 중첩 pytest 임시 디렉터리도 실행별로 격리했다.

기본 장애 검사는 축소 정책으로 수행하고, 실제 120초 정책은 명시적 opt-in 검사로 수행한다.
일반 전체 스위트에서 그 검사가 skip되어도 기본 정책의 실행 증거를 대신하지 않는다.
최종 검증은 파일 쓰기를 멈춘 상태에서 수행한다. T0~T3 완료 후 T4에서 로컬 세션의
요청별 usage를 확보했다. [T4·T5 시범 기록](2026-09-27-superpowers-pilot.md)에
재현 경계·모델/캐시 수치·미측정 시간·브라우저 수명 실험을 구분한다.

최초 전체 검증에서 WF-TEST-01을 확인했다. 기존 설정 화면 브라우저 검사는 이미 읽기 전용인
예약 비밀번호 칸에 fill을 시도하고, 예약 runtime을 금지한 fixture에서 defaults API를 호출했다.
현재 DOM 계약(환경값 로딩·readonly)을 기다리고 defaults 응답을 격리하도록 테스트를 수정했다.
설정 화면 서버에도 같은 유한 cleanup을 적용했다. 제품 UI나 API 동작은 변경하지 않았다.
이 수정은 실패한 전체 수용 검증을 복구하기 위해 범위에 포함했다.
