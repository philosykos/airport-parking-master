# 공항 주차 자동 예약 서비스

첫 화면에서 공항 서비스를 고른다. 현재 인천공항 제2터미널 발렛파킹을 지원하고, 김포공항 국내선 예약주차장은 준비 중이다.

## 설정

Python 3.11 이상이 필요하다. 설정 파일을 표준 라이브러리 `tomllib`로 읽는다.

서비스 설정은 `config/<서비스>.toml`에 있다. 인천 T2 발렛은 `config/t2_valet.toml`이다.

| 키 | 뜻 | 규칙 |
|---|---|---|
| `request.url` | 예약 API 주소 | `http://` 또는 `https://`로 시작 |
| `request.interval_sec` | 폴링 간격 기본값(초) | 10 이상 정수 |
| `payload.car_type`, `payload.booking_type`, `payload.root` | 예약 API의 `carType`, `type`, `root` 값 | 빈 값이 아닌 문자열 |
| `payload.is_using_car_wash`, `payload.is_crew` | 예약 API의 `isUsingCarWash`, `isCrew` 값 | `true` 또는 `false` |
| `payload.customer_request`, `payload.car_wash_type` | 특별 요청, 세차 종류 | 문자열. 빈 문자열이면 보내지 않는다(null) |

설정이 잘못되면 `python app.py`가 `[설정 오류] config/t2_valet.toml: <키> — <이유>`를 출력하고 종료한다. 표에 없는 키도 오류다. 파일이 없거나 TOML 문법이 틀리면 그 파일의 전체 경로를 담은 오류가 나온다.

예전처럼 `.env`에 `REQUEST_URL`, `CAR_TYPE` 등을 적어 두었다면 더 이상 읽지 않는다. 값을 `config/t2_valet.toml`로 옮기고 `.env`에서 지운다. 옛 키가 남아 있으면 앱을 시작할 때 경고가 나온다.

## 실행

```bash
pip install -r requirements.txt
python app.py
```

`http://localhost:8080`을 열고 "인천공항 제2터미널 발렛파킹" 카드를 누른다. 예약 화면(`/t2-valet/`)에서 예약 정보를 입력한 뒤 시작 버튼을 누른다.
예약이 성공하면 스케줄러가 자동으로 종료됩니다.

## 보안

- 이 PC의 브라우저에서만 쓰는 로컬 서비스다. Host가 `localhost`·`127.0.0.1`이 아닌 요청은 400, 다른 사이트에서 보낸 POST는 403으로 거절한다. 다른 기기에서 접속하게 하려면 인증부터 붙여야 한다.
- `user_data.json`과 `logs/api_call.log`는 소유자만 읽고 쓴다(0600). 이전 버전이 만든 파일도 읽거나 쓸 때 권한을 좁힌다.
- 로그와 화면에는 예약자명·휴대폰 번호·차량번호를 가려서 남긴다. 예약 API에는 원본을 보낸다. 이전 버전이 남긴 로그는 가려져 있지 않으니 화면의 로그 초기화로 지운다. 화면에는 최근 500건을 보이고, 로그 파일이 1,000,000바이트를 넘으면 최근 500건만 남긴다.
- T2 화면은 CSP로 같은 출처의 스크립트만 실행한다. 버튼 동작은 인라인 `onclick` 대신 `static/js/app.js`에서 연결한다.
- 의존성 취약점 점검: `pip install pip-audit && pip-audit -r requirements.txt`

## 코드 구조

| 파일 | 맡는 일 |
|---|---|
| `services/t2_valet.py` | T2 설정 읽기, 예약 API 호출, 라우트 |
| `services/t2_input.py` | 화면 입력 검사(필드 목록과 형식 규칙) |
| `services/t2_storage.py` | 기본값·로그 파일 저장(소유자 전용 권한, 로그 보존 한도) |
| `services/t2_scheduler.py` | 반복 호출 워커의 시작·중지 |
| `services/web_security.py` | 앱 전체 Host 제한, 다른 출처 POST 거부, 공통 보안 헤더 |

## 테스트

```bash
pytest
```
