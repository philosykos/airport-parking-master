# 설정 규칙

설정 항목을 추가·변경할 때 따른다. TOML 전환의 결정 배경은
[서비스별 설정 파일 설계](superpowers/specs/2026-09-27-service-config-design.md)에 있다. 그 설계 뒤에
김포와 공용 알림이 붙으며 달라진 점은 이 문서와 코드가 정본이다.

## 값을 두는 곳

| 종류 | 위치 | 읽는 코드 |
|---|---|---|
| 서비스 동작값(요청 주기, 대기 시간, 저장 경로, 고정 페이로드) | `config/<서비스 모듈 이름>.toml`, 커밋한다 | 서비스별 `parse_config` (`services/t2/valet.py`, `services/gimpo/config.py`, `services/notifications/config.py`) |
| 비밀값(예약 비밀번호, 텔레그램 토큰·수신자)과 알림 켜기 여부 | `.env`, 커밋하지 않는다. 키 목록과 형식은 `.env.example` | `reservation_password()` (`services/gimpo/config.py`), `TelegramSettings.from_environment()` (`services/notifications/config.py`) |
| 화면 폴링·새로고침 주기 | `app.py`의 `app.config` | `templates/base.html`이 `<body>` data 속성으로 내려 주고 JS가 읽는다. 테스트는 `tests/conftest.py`의 `UI_INTERVALS`로 줄인다 |

- 새 조정값은 TOML에 둔다. 비밀값이 아닌 설정을 환경변수로 읽지 않는다.
- `.env`는 `load_dotenv()`로 프로세스 환경에 올리지 않는다. 필요한 로더만 `dotenv_values()`로 읽고,
  같은 키가 프로세스 환경변수에 있으면 그 값이 우선한다.
- 비밀값을 TOML·로그·API 응답·작업 저장소에 넣지 않는다. 비밀값을 담는 dataclass 필드는
  `field(repr=False)`로 둔다.
- 비밀값을 새로 추가하면 `.env.example`에 placeholder와 형식 주석을, README "설정" 절에 안내를 함께
  추가한다.

## TOML 로더

서비스마다 `frozen` dataclass와 `parse_config(raw)`를 두고, 모듈 import 때 한 번 읽는다
(`CONFIG = parse_config(load_toml("<서비스>"))`). 설정을 바꾸면 앱을 다시 시작해야 반영된다.
공용 도우미는 `services/config.py`에 있다.

- 모르는 테이블·키는 `reject_unknown`으로 오류를 낸다. 오타를 조용히 무시하지 않기 위해서다.
- 필수 테이블은 `require_table`로 꺼낸다.
- 위반은 `fail(label, "<테이블>.<키>", "<이유>")`로 알린다. 메시지는
  `config/<서비스>.toml: <키 경로> — <이유>` 형식이다.
- 정수는 `type(value) is int`로 검사해 불리언을 걸러 내고, 허용 범위를 함께 검사한다. 범위의 정본은
  각 `parse_config`다.
- 경로 값은 프로젝트 루트 기준 상대 경로로 받아 절대 경로로 바꾼다(`storage.directory`).
- 설정이 잘못되면 앱은 `[설정 오류] <메시지>` 한 줄을 출력하고 종료 코드 1로 끝난다. 이 처리는
  `app.py`의 서비스 import를 감싼 `try`가 맡으므로, 새 서비스 모듈도 그 안에서 import한다.
- 키를 옮기거나 없앨 때 옛 이름을 받아 주지 않는다. `.env`에서 TOML로 옮긴 키는
  `LEGACY_ENV_KEYS`에 넣어 `python app.py` 시작 때 경고한다.
- 커밋된 TOML이 오류 없이 읽히는지와 위반 사례별 오류 메시지를 테스트한다
  (`tests/web/test_config.py`).
