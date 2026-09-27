# Superpowers 지침 충돌 및 모델·effort 캐시 분석

검토일: 2026-09-27. 대상: t2-valet-master와 관련 Claude 세션.
상태: 읽기 전용 감사와 설계 수정안. 전역 설정·에이전트·훅·CLAUDE.md는 변경하지 않았다.
연결 설계: [워크플로우 개선안 r3](2026-09-27-superpowers-workflow-improvement.md).

독립 서브에이전트 재리뷰에서 AR-01(Important: finishing의 잔여 중복 검증)과
AR-02(Minor: 캐시 표본 재현 경계 누락)를 확인해 아래 I-06과 캐시 집계에 반영했다.
실행기 리뷰의 DR-01/DR-02는 연결 설계의 타이머 소유권·선택적 진단 판정에 반영했다.

## 판단

불필요한 규칙이 충돌한다는 가설은 일부 확인됐다. 가장 직접적인 후보는 프로젝트의 짧은
CLAUDE.md보다 **전역 CLAUDE.md → 사용자 에이전트 정의 → Agent 호출 훅**의 조합이다.
테스트 직접 재실행, 커밋 금지, 모델 인자 제거가 SDD의 역할 분담과 맞지 않는다.
Superpowers 내부에도 일반 검증 스킬과 SDD 전용 템플릿 사이에 재실행을 유발할 문구가 있다.

다만 모든 지연을 지침 충돌로 설명할 수는 없다. 테스트 정리의 hang과 종료 감시 부재는 별도
실행 문제다. 또한 모델을 명시하거나 새 에이전트를 시작했다는 이유만으로 캐시가 모두 사라지는
것은 아니다. 실제 usage에서 캐시 재사용은 높았고, 초기 호출의 재생성 부담은 별도로 관측됐다.

## 1. 적용 경로와 조사 범위

| 위치 | 확인 결과 | 해석 |
|---|---|---|
| 프로젝트 CLAUDE.md | 6행. 구현 계획은 항상 SDD로 수행한다는 사용자 선택만 있음 | 보존할 프로젝트 선호. 일반 절차를 장황하게 복제한 파일이 아님 |
| 프로젝트 및 확인한 상위 경로의 AGENTS.md/AGENTS.override.md | 없음 | 현재 AGENTS.md 중복을 원인으로 지목할 근거 없음 |
| ~/.codex/AGENTS.md, AGENTS.override.md | 없음 | Codex 전역 파일과의 충돌은 관측되지 않음 |
| ~/.codex/config.toml | instruction 파일 지정·fallback filename 설정 없음 | CLAUDE.md가 Codex에 자동 적용된다고 가정할 수 없음 |
| ~/.claude/CLAUDE.md | 50행, 작업 배분·직접 검증·effort·문서·푸시 규칙 | Claude 작업에 공통 적용될 주요 충돌 후보 |
| 프로젝트 .claude, 사용자/프로젝트 rules, CLAUDE.local.md | 추가 지침 발견되지 않음 | 현재 체크아웃 기준. 제거된 과거 worktree의 모든 파일까지 복원하지는 않음 |
| ~/.claude/agents | sonnet-impl, sonnet-scan, fable-high, haiku-extract | SDD의 기본 역할 템플릿과 함께 읽을 때 별도 제약이 생김 |
| ~/.claude/settings.json | Superpowers 활성화, Agent PreToolUse 등 사용자 훅 등록 | 문서 밖에서 호출 인자도 변경될 수 있음 |
| 로컬 Superpowers 6.4.1 | SDD 본문·구현자/리뷰어 템플릿·검증/마무리 스킬 확인 | 버전 번호만으로 내부 지침이 모두 일관된다고 판단하면 안 됨 |

Codex의 기본 검색 순서는 전역 지침과 프로젝트 경로의 AGENTS.override.md/AGENTS.md이며,
다른 파일명은 fallback 설정이 필요하다. 이 세션에서 CLAUDE.md를 **감사 자료로 읽은 것**과
시작 시 자동 주입된 것은 구분한다. [Codex 공식 문서](https://learn.chatgpt.com/docs/agent-configuration/agents-md)

Claude Code는 전역·프로젝트 CLAUDE.md 내용을 함께 읽는다. 가까운 파일이 있다고 전역 파일의
나머지 지침이 없어지는 것은 아니다. 실제 활성 파일은 실행 세션의 context로 확인해야 한다.
[Claude Code 공식 문서](https://code.claude.com/docs/en/memory#how-claude-md-files-load)

현재 Codex에 노출된 스킬 목록과 확인한 기본 스킬 경로에서는 Superpowers가 발견되지 않았다.
Claude의 설치 경로를 읽을 수 있다는 사실은 Codex에도 설치·활성화됐다는 증거가 아니다.
따라서 Claude의 훅 충돌을 현재 Codex 호출에도 그대로 적용해 설명하지 않는다.

## 2. 확인된 충돌과 조건부 충돌

### I-01. 메인의 직접 테스트 의무가 SDD의 증거 재사용과 충돌 — Important

- 전역 [CLAUDE.md:34](</Users/a08523/.claude/CLAUDE.md:34>)는 구현 결과를 받은 뒤 diff를 보고
  테스트를 직접 실행한 다음 채택하도록 요구한다.
- [SDD:337](</Users/a08523/.claude/plugins/cache/claude-plugins-official/superpowers/6.4.1/skills/subagent-driven-development/SKILL.md:337>)과
  [리뷰 템플릿:73](</Users/a08523/.claude/plugins/cache/claude-plugins-official/superpowers/6.4.1/skills/subagent-driven-development/task-reviewer-prompt.md:73>)은
  구현자가 같은 코드에서 실행한 테스트를 리뷰어가 다시 실행하지 않도록 한다.
- 엄밀히는 메인과 리뷰어가 다른 역할이므로 두 문장의 대상이 완전히 같지는 않다.
  하지만 메인에게 무조건 재실행 의무를 부과하면 SDD가 줄인 중복이 컨트롤러 단계에서 다시 생긴다.
  플러그인의 using-superpowers도 사용자 지침을 스킬보다 우선한다고 적고 있어 전역 문구가 살아남기 쉽다.

**관측:** 보안 강화 세션의 Task 1은 구현자가 커밋 전 50개 통과, 커밋 후 같은 50개 통과를 기록했다.
메인은 이어서 “테스트를 직접 돌린 뒤”라고 설명하고 전체 스위트를 다시 실행했다.
ledger를 읽은 최종 리뷰 로그에도 `controller re-ran`이 남았다. 반복 검증 행동은 확인됐지만,
매번 전역 지침 때문이었다는 모델 내부 인과관계까지 증명한 것은 아니다.
이 사례는 빠른 테스트였으므로 이 자체를 장시간 지연 사례로 계산하지 않는다.

**최소 수정 제안:** 전역의 무조건 직접 실행 문장을 “diff와 해당 코드의 검증 증거를 확인한다.
증거가 없거나 무효이거나 새로운 구체적 의심이 있을 때 필요한 검증을 실행한다”로 대체한다.
보고서의 성공 한 줄을 믿는 것과 원본 증거를 직접 읽는 것을 구분한다.

### I-02. 사용자 구현 에이전트의 커밋 금지와 SDD 완료 계약 충돌 — Important

- [sonnet-impl:10](</Users/a08523/.claude/agents/sonnet-impl.md:10>)은 커밋·설치를 금지한다.
- [SDD 구현자 템플릿:38](</Users/a08523/.claude/plugins/cache/claude-plugins-official/superpowers/6.4.1/skills/subagent-driven-development/implementer-prompt.md:38>)은
  구현자가 커밋하고 commit SHA와 보고서를 반환하도록 한다.
- SDD의 BASE..HEAD 리뷰 패키지는 이 커밋을 전제로 한다. 금지를 따르면 diff가 비거나
  메인이 커밋하는 추가 단계가 필요하고, 템플릿을 따르면 사용자 에이전트의 역할 제한과 어긋난다.

**관측:** Task 1 dispatch는 `sonnet-impl`을 선택하면서 커밋을 명시적으로 지시했고 실제 커밋도 생겼다.
커밋 금지 때문에 당시 멈췄다는 증거는 없지만 양립하지 않는 계약을 한 호출에 전달한 것은 확인됐다.

**최소 수정 제안:** SDD 구현자는 기존 SDD general-purpose 템플릿으로 실행한다.
제한된 작업용 sonnet-impl의 금지를 전역적으로 제거하지 않는다. 꼭 사용자 정의 에이전트를
사용해야 한다면 SDD와 맞는 역할 계약을 별도로 설계하되 템플릿 전문을 복제하지 않는다.

### I-03. 모델 지정 훅이 SDD의 모델 선택·승격을 무효화 — Important, 조건부

- [전역 CLAUDE.md:30](</Users/a08523/.claude/CLAUDE.md:30>)은 맞는 사용자 에이전트가 있으면 이를 사용하도록 한다.
- [SDD:204](</Users/a08523/.claude/plugins/cache/claude-plugins-official/superpowers/6.4.1/skills/subagent-driven-development/SKILL.md:204>)는
  dispatch마다 모델을 명시하고, 4~5차 수정에서 더 강한 모델로 승격하도록 한다.
- [agent-model-guard.sh:19](</Users/a08523/.claude/hooks/agent-model-guard.sh:19>)는 선택한 사용자 정의에
  model이 있으면 호출의 model 필드를 지운다. 모델 선택이 요청값과 달라질 수 있다.

**재현:** 훅에 실제 에이전트를 생성하지 않는 합성 JSON을 입력했다.
`subagent_type=sonnet-impl, model=opus`에서 model 필드가 제거됐고 sonnet으로 실행된다는 문구가 반환됐다.
`general-purpose, model=opus`에서는 override가 없었다. 이는 훅 변환의 재현이며 실제 API 모델 실행 시험은 아니다.
과거 로그에서 이 훅이 승격을 막아 품질 문제를 만들었다는 증거까지는 확인하지 못했다.

**최소 수정 제안:** SDD는 general-purpose와 명시적 모델을 사용하고, 기존 훅은 고정 역할 에이전트에만 둔다.
또는 훅을 바꾸려면 명시적 호출값 우선 여부를 하나의 정책으로 결정한다.
모델 인자를 조용히 제거하는 행위를 캐시 최적화로 정당화하지 않는다.

### I-04. 전역 작업 배분 기준이 SDD 실행 경계와 다름 — Important, 조건부

전역 [CLAUDE.md:24](</Users/a08523/.claude/CLAUDE.md:24>)는 계약 파일과 핵심 경로 수정을 메인 직접 작업으로 분류한다.
프로젝트 [CLAUDE.md:5](</Users/a08523/Edu/t2-valet-master/CLAUDE.md:5>)와 SDD는 구현 계획의 과제를 구현자에게 맡긴다.
또 전역 지침은 파일 분리/worktree 격리를 한 병렬 구현을 허용하지만 [SDD:282](</Users/a08523/.claude/plugins/cache/claude-plugins-official/superpowers/6.4.1/skills/subagent-driven-development/SKILL.md:282>)는
여러 구현자를 동시에 dispatch하지 말라고 한다.

허용과 금지가 같은 상황에서 합쳐지는 것이 문제다. 전역 문구가 병렬화를 반드시 시키는 것은 아니다.
관련 후속 작업 로그에는 파일이 겹치지 않는다는 이유로 병렬 수행을 결정한 Ruling도 있었다.
따라서 모든 병렬 작업을 무의식적인 충돌로 판단할 수는 없다.

**최소 수정 제안:** 전역 배분 기준의 범위를 “명시적으로 선택된 워크플로우가 없는 작업”으로 한정한다.
프로젝트가 이미 선택한 SDD를 다시 선정하는 표나 병렬화 규칙을 프로젝트 파일에 추가하지 않는다.

### I-05. 조사 전용 에이전트를 과제 리뷰어로 사용 — 조건부 불일치

[sonnet-scan:9](</Users/a08523/.claude/agents/sonnet-scan.md:9>)은 Bash를 조회에만 사용하도록 한다.
SDD 리뷰어는 원칙적으로 읽기 전용이지만 새로 발견한 의심에 한해 작은 테스트 실행을 허용한다.
pytest는 파일·캐시·fixture를 만들 수 있어 조사 전용 에이전트의 권한과 맞지 않을 수 있다.
동시에 전역 배분 표를 자식이 다시 적용하면 재위임 충동도 생길 수 있으나,
실제 SDD 구현자/리뷰어 템플릿은 재위임을 명시적으로 금지하므로 이것은 확인된 행동으로 분류하지 않는다.

**최소 수정 제안:** SDD 전용 리뷰 템플릿을 그대로 사용한다. 조사 에이전트의 읽기 전용 제한을
완화하는 대신 역할을 맞춘다. 필요 검증을 컨트롤러에게 요청하는 것도 허용되는 경로다.

### I-06. Superpowers 내부의 fresh verification 문구 — Important

[verification-before-completion:20](</Users/a08523/.claude/plugins/cache/claude-plugins-official/superpowers/6.4.1/skills/verification-before-completion/SKILL.md:20>)는
현재 메시지에서 명령을 실행하지 않았다면 통과했다고 말할 수 없다고 하고, 28행은 full command 실행을 요구한다.
이를 SDD 리뷰어와 컨트롤러에게 매번 적용하면 같은 코드의 증거 재사용과 충돌한다.
여기서 full command를 언제나 전체 프로젝트 suite로 해석하는 것은 또 다른 과잉 해석이다.

**최소 수정 제안:** 최종 완료 검증의 담당자와 시점을 finishing 단계에 하나로 배치한다.
리뷰어는 같은 스냅샷의 증거 검토, 구현자는 변경 후 실행을 담당하도록 스킬 정본에서 범위를 명확히 한다.
기존 evidence 재사용을 허용하는 명시적 예외가 없다면 완전히 호환된다고 주장하지 않는다.
수정은 플러그인 관리 소스에서 처리할 후속안이며 설치 캐시는 직접 고치지 않는다.

**AR-01 정정:** 시점 조정만으로 중복이 없어지는 것은 아니다. 현재 구현자 템플릿은 과제 커밋 전,
[finishing 스킬:16](</Users/a08523/.claude/plugins/cache/claude-plugins-official/superpowers/6.4.1/skills/finishing-a-development-branch/SKILL.md:16>)은
마무리 시 전체 suite 실행을 각각 요구한다. 마지막 과제 후 코드 변경이 없으면 두 요구를 지킬 때
중복이 남는다. 현재는 `required-by-finishing`으로 따로 집계한다. 동일 최종 fingerprint의 전체 검증
증거를 finishing에서 재사용하는 것은 **스킬 계약을 수정한 뒤의 목표**이며 현행 준수만의 효과가 아니다.

### I-07. 초기 개선안도 새 충돌을 만들 수 있었음 — Important, 설계 반영

[implementer-prompt:47](</Users/a08523/.claude/plugins/cache/claude-plugins-official/superpowers/6.4.1/skills/subagent-driven-development/implementer-prompt.md:47>)은
과제 커밋 전 전체 suite 1회를 요구한다. 초기 개선안의 “과제는 관련 테스트만, 전체는 최종 1회”는
이 요구를 바꾸는 안이지 기존 SDD 준수만으로 달성되는 최적화가 아니다.
또 Minor, 재리뷰, 모델, loop 규칙을 CLAUDE.md/AGENTS.md에 복사하면 플러그인 업데이트 후 정본이 갈린다.

**반영:** 설계의 과제 완료 검증을 현행 템플릿에 맞췄다. 테스트 범위 축소는 별도 변경 제안으로 남긴다.
프로젝트 지침에 SDD 절차를 복제하는 방침은 철회하고, 구현된 테스트 명령과 프로젝트 사실만 연결한다.

### I-08. 전역 effort 경고가 지나치게 일반적임 — 문구 축소 필요

[CLAUDE.md:43](</Users/a08523/.claude/CLAUDE.md:43>)은 세션 중 effort 변경이 캐시를 무효화한다고 단정하고
새 세션 또는 에이전트 frontmatter 변경을 대안으로 제시한다.
설정 변경의 위험을 경고하는 취지는 타당하지만 공급자·모델·변경 경로에 따른 차이를 생략했다.
새 세션을 열거나 frontmatter를 바꾸는 것 자체가 캐시를 보존한다는 보장도 없다.

**최소 수정 제안:** “동일 에이전트의 모델·effort는 가능한 유지한다. 변경 시 사용 도구의 전달 방식과
실제 캐시 지표를 확인한다”로 범위를 줄인다. 사용자 요청을 무조건 새 세션 권유로 돌리지 않는다.

## 3. 삭제 대상으로 볼 근거가 약한 항목

- 프로젝트의 SDD 선택 1문장은 사용자가 명시적으로 정한 선호이며 유지한다.
- 범위 밖 diff 확인, 확인 못 한 사실 명시, 푸시 전 의존 점검은 SDD와 병존 가능하다.
- 문서 수치 관리와 propagate 규칙은 문서 작업 비용을 늘릴 수 있지만 테스트 재실행·hang의
  직접 원인이라는 증거는 없다. 문서 작업용 스킬로 이동하는 것은 별도 정리 후보다.
- claude-md-guard의 수정 후 기록 요구는 관리 대상 경로에만 발동한다. 현재 t2-valet-master의
  CLAUDE.md는 해당 목록에서 발견되지 않았다. artifact-theme-guard도 해당 스킬 호출에만 발동한다.
  이 훅들을 프로젝트 테스트 지연의 원인으로 묶지 않는다.

## 4. 모델·effort와 캐시

### 원리와 적용 한계

모델·effort를 **명시하는 행위**, 값이 **달라지는 행위**, 새 에이전트의 **문맥이 달라지는 행위**를 구분한다.
Claude 공식 문서는 기본 effort의 명시와 생략을 동등하게 취급한다. 요청 수준 effort 변경은
메시지 캐시를 무효화하며 상위 범위의 영향은 모델마다 다르다. 지원 모델의 메시지 단위 변경은
기존 prefix를 보존할 수 있다. [Claude 캐시 문서](https://platform.claude.com/docs/en/build-with-claude/prompt-caching#what-invalidates-the-cache)

OpenAI도 모델·도구·reasoning.effort 등이 캐시 prefix에 영향을 줄 수 있다고 설명하며,
지원 모델의 configuration_update를 별도 경로로 제공한다.
[OpenAI 캐시 문서](https://developers.openai.com/api/docs/guides/prompt-caching)
이는 API 기능이다. 현재 Claude Code/Codex Agent 도구가 그 경로를 사용한다고 확인하지는 않았다.

이 원리를 현재 구성에 적용한 판단은 다음과 같다.

| 선택 | 캐시/비용 측면의 판단 |
|---|---|
| 같은 모델·effort를 매 호출 명시 | 값과 실제 prefix가 같다면 그 명시 자체를 손실 원인으로 볼 수 없음 |
| 같은 에이전트를 수정 때마다 다른 모델·effort로 전환 | 기존 모델/설정의 캐시를 그대로 쓸 것으로 기대하지 않음. 품질상 필요할 때만 변경 |
| 구현/리뷰 역할마다 고정된 설정 | 각 역할에서 반복 prefix를 형성할 수 있음. 역할 간 전체 캐시 공유와는 다름 |
| 매 과제 새 구현자 | 문맥 오염을 줄이는 SDD의 장점 유지. 첫 호출 비용은 측정 대상이며 매번 완전 cold라고 단정하지 않음 |
| 동일 수정 담당 재개 | 문맥 재구성 비용을 줄일 수 있고 SDD의 1~3차 수정 지침과 일치. TTL 등으로 실제 hit는 보장 못 함 |
| 캐시를 위해 모든 역할을 같은 장기 에이전트로 통합 | 독립 리뷰·문맥 격리를 훼손하므로 채택하지 않음 |
| 사용자 에이전트가 고정하는 tools/system/effort | 안정성에는 이점이 있지만 SDD와 다른 계약을 함께 고정할 수 있음. 고정 자체가 적합성의 증거는 아님 |

캐시 적중률을 높이려고 불필요한 문맥을 오래 유지하거나 강한 모델을 모든 일에 고정하지 않는다.
입력 재사용으로 줄어드는 비용과 출력/추론·도구 대기·재작업 비용을 합쳐 판단해야 한다.

### 실제 기록에서 확인한 캐시 재사용

대상은 shared-ui-part1 Claude 세션 `92c77e6a-5c54-4ad0-ad77-1b4a9ea9d1fa`의
부모 JSONL 1개와 하위 agent JSONL 40개다. 재리뷰에서 원본 로그가 증가하고 있음을 확인했으므로,
기존 35개 표본 수치를 아래 고정된 새 표본으로 대체했다. 기존 수치가 틀렸다는 판정은 아니다.

수집 구간은 **2026-09-27 13:00:30.758944~13:00:31.029540 UTC**다.
[집계 스냅샷](2026-09-27-superpowers-cache-snapshot.json)에 파일 목록·파일별 `included_bytes`·
해당 prefix의 SHA-256·집계값·첫 assistant 기록 usage를 보존했다. 파일마다 한 번 읽고 마지막 완전한
JSONL 줄까지 사용했다. 여러 파일을 순서대로 읽었으므로 단일 시점의 원자적 세션 스냅샷은 아니다.
원문 대화는 복사하지 않았다.

각 파일의 assistant `message.id`별로 중복을 제거하고, 같은 ID가 여러 번 나오면 마지막 usage를
채택했다. 첫 기록은 해당 파일에서 가장 먼저 등장한 ID의 최종 usage다. 이는 로컬 기록 집계이지
결제 원장 대조가 아니다.

```text
입력 캐시 비율 = cache_read_input_tokens /
  (input_tokens + cache_creation_input_tokens + cache_read_input_tokens)
```

| 기록 그룹 | 메시지 수 | cache read | cache creation | 미캐시 input | 전체 입력 캐시 비율 | agent 파일별 첫 기록의 입력 캐시 비율 |
|---|---:|---:|---:|---:|---:|---:|
| 부모 Opus 5.5 | 217 | 48,469,132 | 357,730 | 434 | 99.27% | 비교 대상 제외 |
| 하위 Sonnet 5 | 892 | 96,619,802 | 3,334,707 | 1,784 | 96.66% | 56.90% (30개 파일) |
| 하위 Opus 5.5 | 170 | 12,656,204 | 823,972 | 342 | 93.89% | 33.96% (9개 파일) |
| 하위 Fable 5.1 | 13 | 1,308,236 | 217,585 | 386 | 85.72% | 0% (1개 파일) |

분모는 그룹 합산 입력이며 요청별 비율의 단순 평균이 아니다. 첫 기록에서 cache read가 있었던
agent 파일은 Sonnet 25/30, Opus 5/9, Fable 0/1이다.
전체 비율에는 같은 에이전트 안에서의 후속 호출 재사용이 크게 반영된다.

원본 로그에 접근할 수 있을 때 다음 절차로 고정 범위를 재집계한다. 새로 추가된 파일이나
cutoff 이후의 줄은 읽지 않는다. prefix hash가 다르면 동일 표본의 재현으로 취급하지 않는다.

```python
import hashlib, json
from collections import Counter, defaultdict
from pathlib import Path

snapshot = json.loads(Path(
    "docs/plans/2026-09-27-superpowers-cache-snapshot.json"
).read_text())
root = Path(snapshot["source_root"]).expanduser()
totals = defaultdict(Counter)
for source in snapshot["files"]:
    with (root / source["source_relative_path"]).open("rb") as stream:
        raw = stream.read(source["included_bytes"])
    assert hashlib.sha256(raw).hexdigest() == source["prefix_sha256"]
    messages = {}
    for line in raw.splitlines():
        event = json.loads(line)
        message = event.get("message", {})
        if event.get("type") == "assistant" and message.get("id") and message.get("usage"):
            messages[message["id"]] = message
    for message in messages.values():
        total = totals[(source["kind"], message["model"])]
        total["messages"] += 1
        for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
            total[key] += message["usage"].get(key, 0)
for group, total in sorted(totals.items()):
    print(group, dict(total))
```

이 표로 판단할 수 있는 것은 **현재 역할 분리에서도 캐시는 상당히 작동했고 초기 호출 부담은 더 컸다**는 점이다.
모델을 혼용해서 얼마를 손해 봤는지, effort를 바꿔서 miss가 발생했는지, 어느 이전 요청과 공유했는지는
이 usage만으로 알 수 없다. 첫 기록의 hit도 부모 문맥 공유의 증명은 아니다.
모델별 과제 난도·턴 수·문맥 길이가 달라 비율을 모델의 우열이나 비용 절감률로 비교하지 않는다.

### 설계 결정과 후속 측정

1. 역할 시작 시 유효 모델·effort를 고정하고 같은 수정 담당의 재개에서는 유지한다.
   매번 최저 단가 모델을 고르는 정책보다 소수의 안정된 역할 구성을 먼저 측정한다.
2. SDD의 새 과제/독립 리뷰 원칙과 난항 시 승격은 유지한다. 캐시를 위해 품질 경계를 제거하지 않는다.
3. 재사용 가능한 역할 템플릿과 도구 구성을 안정화한다. 과제 ID·시간·경로·finding은 가능한 뒤에 둔다.
   실제 도구가 제어할 수 없는 API cache 설정은 프로젝트 문서만으로 적용됐다고 주장하지 않는다.
4. 다음 시범 과제에서는 requested model/effort, 훅 적용 후 값, 실제 usage 모델을 구분한다.
   effective effort가 기록되지 않으면 미확인으로 남긴다. 현재 로컬 정의를 과거 실행값으로 소급하지 않는다.
5. 첫 호출/후속 호출/수정 재개의 캐시 지표, 총 입력/출력, 응답 시작 지연(관측 가능한 경우),
   전체 시간, 수정 라운드, 최종 결함을 함께 비교한다. 안정된 설정과 역할별 설정의 비교는
   같은 종류의 과제를 반복해 수행하고 여러 요인을 한 번에 바꾸지 않는다.

## 5. 실행기 설계에 별도로 반영할 관측

shared-ui 세션에는 pytest가 25분째 멈춰 있다는 당시 보고가 있다. 지침 정리만으로는 이 대기를 끊을 수 없다.
여러 검증 명령이 `pytest ... | tail` 형태이며, 해당 명령 문자열에는 pipefail이 없었다.
이 경우 셸 설정에 따라 마지막 tail의 성공이 pytest 실패를 가릴 수 있다. 당시 실패가 실제로
가려졌다고 단정하지 않지만 새 실행기는 pytest 자식의 종료 코드를 직접 회수해야 한다.

지침 정리와 실행 감독은 서로 대체하지 않는다. 첫째는 중복 작업과 상충하는 의사결정을 줄이고,
둘째는 잘못된 성공 판정과 끝나지 않는 프로세스를 제한한다.

## 6. 최소 변경 순서

1. 전역 작업 배분을 기본값으로 한정하고 직접 테스트 의무를 증거 확인으로 좁히는 수정안을 적용한다.
2. SDD dispatch에서 역할 계약이 다른 사용자 에이전트를 섞지 않는다. 모델 정본을 한 곳에 둔다.
3. verification/finishing/SDD 사이의 검증 담당·시점을 플러그인 관리 소스에서 조정한다.
4. 실행기를 구현하고 실제 사용할 명령만 프로젝트 진입 문서에 연결한다.
5. 그 이후 캐시·병렬화 최적화를 측정한다. AGENTS.md 생성이나 규칙 증식으로 기존 충돌을 덮지 않는다.

이번 변경은 이 분석 문서와 설계안에만 반영했다. 위 전역 지침·훅 수정안은 적용하지 않았다.

## 로컬 실행 증거

- 보안 강화 부모: `~/.claude/projects/-Users-a08523-Edu-t2-valet-master/4372870d-3f44-4ba6-b541-8b61870e092b.jsonl`
  900행(dispatch), 937~940행(직접 재검증 설명·명령·결과).
- 같은 세션 `subagents/agent-af773ac73010bba79.jsonl`: 150행(커밋 전 통과),
  181행(f3a6c82 커밋), 188행(커밋 후 통과).
- 같은 세션 `subagents/agent-a877c3183c67d75bc.jsonl`: 22행(ledger의 controller re-ran).
- shared-ui 부모 및 캐시 표본 경로:
  `~/.claude/projects/-Users-a08523-Edu-t2-valet-master--claude-worktrees-shared-ui-part1/92c77e6a-5c54-4ad0-ad77-1b4a9ea9d1fa.jsonl`
  및 같은 이름의 디렉터리 아래 agent JSONL. 부모 662행(hang 보고), 2872행(병렬 수행 Ruling 생성).
- 로그 행 번호는 검토 시점 기준이다. 원본 로그·비밀 설정·전체 환경은 저장소에 복사하지 않았다.
