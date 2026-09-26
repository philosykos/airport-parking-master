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

설정이 잘못되면 `python app.py`가 `[설정 오류] config/t2_valet.toml: <키> — <이유>`를 출력하고 종료한다. 표에 없는 키도 오류다.

예전처럼 `.env`에 `REQUEST_URL`, `CAR_TYPE` 등을 적어 두었다면 더 이상 읽지 않는다. 값을 `config/t2_valet.toml`로 옮기고 `.env`에서 지운다. 옛 키가 남아 있으면 앱을 시작할 때 경고가 나온다.

## 실행

```bash
pip install -r requirements.txt
python app.py
```

`http://localhost:8080`을 열고 "인천공항 제2터미널 발렛파킹" 카드를 누른다. 예약 화면(`/t2-valet/`)에서 예약 정보를 입력한 뒤 시작 버튼을 누른다.
예약이 성공하면 스케줄러가 자동으로 종료됩니다.

## 테스트

```bash
pytest
```
