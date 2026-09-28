"""공항 예상 주차요금 조회. requests만 쓰고 Playwright는 쓰지 않는다.

서버는 공항 예약 화면과 같은 두 단계로 부른다: `POST /main/calculateAmt.json`은 항상
무할인 코드(DC001)로 기본 요금을 받고, 선택 할인이 DC001이 아니면 그 기본 요금을 근거로
`GET /reservation/calculateDiscountAmt.json`을 선택 할인 코드로 불러 할인 요금을 받는다
(선택 할인이 DC001이면 이 단계를 생략하고 discountAmt=0). `estimatedAmt = calculateAmt -
discountAmt`. 같은 입력은 `ttl`초 동안 캐시해 공항을 다시 부르지 않는다.
"""
import ssl
import threading
import time

import requests
import truststore
from requests.adapters import HTTPAdapter

from services.gimpo.client import ORIGIN

TIMEOUT_SECONDS = 5
CACHE_TTL_SECONDS = 600
HEADERS = {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"}
CALCULATE_PATH = "/main/calculateAmt.json"
DISCOUNT_PATH = "/reservation/calculateDiscountAmt.json"
BASE_DISCOUNT_CD = "DC001"


class _TruststoreHTTPAdapter(HTTPAdapter):
    """`https://`용 어댑터: OS 인증서 저장소(truststore)로 TLS를 검증한다(`verify=False` 금지)."""

    def init_poolmanager(self, *args, **kwargs):
        kwargs["ssl_context"] = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        return super().init_poolmanager(*args, **kwargs)


def _default_session():
    session = requests.Session()
    session.mount("https://", _TruststoreHTTPAdapter())
    return session


class FeeUnavailable(Exception):
    pass


def _as_amount(value):
    # bool은 int의 하위 타입이라 따로 걸러낸다(JSON true/false가 정수로 보이지 않게).
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


class AirportFeeClient:
    """`quote()`는 스레드 안전하며, 같은 입력은 `ttl`초 동안 새로 조회하지 않는다."""

    def __init__(self, session=None, timeout=TIMEOUT_SECONDS, ttl=CACHE_TTL_SECONDS, clock=time.monotonic):
        self.session = session or _default_session()
        self.timeout = timeout
        self.ttl = ttl
        self.clock = clock
        self._lock = threading.Lock()
        self._cache = {}

    def quote(self, *, parkingId, entryAt, exitAt, discountSelection):
        key = (parkingId, entryAt, exitAt, discountSelection)
        now = self.clock()
        with self._lock:
            cached = self._cache.get(key)
            if cached and cached[0] > now:
                return cached[1]
        result = self._fetch(parkingId, entryAt, exitAt, discountSelection)
        with self._lock:
            self._cache[key] = (now + self.ttl, result)
        return result

    def _fetch(self, parking_id, entry_at, exit_at, discount_selection):
        # 공항 예약 화면과 같은 두 단계로 부른다: 기본 요금은 항상 무할인(DC001)으로 받고,
        # 선택 할인이 있으면 그 기본 요금을 근거로 할인 요금을 따로 받는다. 선택 할인 그대로
        # 기본 요금 호출에 넘기면 이미 할인된 금액이 오고, 거기서 할인을 또 빼게 된다.
        form = {"sectnId": parking_id, "inDttm": entry_at + ":00", "outDttm": exit_at + ":00",
                "discountCd": BASE_DISCOUNT_CD}
        try:
            response = self.session.post(ORIGIN + CALCULATE_PATH, data=form, headers=HEADERS, timeout=self.timeout)
            response.raise_for_status()
            calculate_amt = _as_amount(response.json().get("calculateAmt"))
            if calculate_amt is None:
                raise FeeUnavailable("공항 예상 주차요금 응답을 확인할 수 없습니다.")
            if discount_selection == BASE_DISCOUNT_CD:
                discount_amt = 0
            else:
                params = {**form, "discountCd": discount_selection, "calculateAmt": calculate_amt}
                response = self.session.get(ORIGIN + DISCOUNT_PATH, params=params, headers=HEADERS, timeout=self.timeout)
                response.raise_for_status()
                discount_amt = _as_amount(response.json().get("discountAmt"))
                if discount_amt is None or not 0 <= discount_amt <= calculate_amt:
                    raise FeeUnavailable("공항 할인 요금 응답을 확인할 수 없습니다.")
        except FeeUnavailable:
            raise
        except Exception as error:
            raise FeeUnavailable("공항 요금을 확인할 수 없습니다.") from error
        return {"calculateAmt": calculate_amt, "discountAmt": discount_amt, "estimatedAmt": calculate_amt - discount_amt}
