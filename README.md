# 공항 주차 자동 예약 서비스

첫 화면에서 공항 서비스를 고른다. 현재 인천공항 제2터미널 발렛파킹을 지원하고, 김포공항 국내선 예약주차장은 준비 중이다.

## 설정

`.env` 파일을 생성하고 값을 입력하세요:

```env
# 필수
REQUEST_URL=https://api.amanopark.co.kr/api/web/booking/reservation

# 선택 (기본값 표시)
REQUEST_INTERVAL=30            # 폴링 간격 (초 단위, 최소: 10)
CAR_TYPE=BASIC
BOOKING_TYPE=BASIC
ROOT=WEB
IS_USING_CAR_WASH=false
IS_CREW=false
CUSTOMER_REQUEST=              # 특별 요청 사항 (선택)
CAR_WASH_TYPE=                 # 세차 종류 (선택)
```

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
