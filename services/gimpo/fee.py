"""공항 예상 주차요금 조회. requests만 쓰고 Playwright는 쓰지 않는다.

`POST /main/calculateAmt.json`으로 기본 요금을, `GET /reservation/calculateDiscountAmt.json`으로
할인 요금을 차례로 받아 `estimatedAmt = calculateAmt - discountAmt`를 계산한다. 같은 입력은
`ttl`초 동안 캐시해 공항을 다시 부르지 않는다.
"""
import threading
import time

import requests

from services.gimpo.client import ORIGIN

TIMEOUT_SECONDS = 5
CACHE_TTL_SECONDS = 600
HEADERS = {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"}
CALCULATE_PATH = "/main/calculateAmt.json"
DISCOUNT_PATH = "/reservation/calculateDiscountAmt.json"


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
        self.session = session or requests.Session()
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
        form = {"sectnId": parking_id, "inDttm": entry_at + ":00", "outDttm": exit_at + ":00",
                "discountCd": discount_selection}
        try:
            response = self.session.post(ORIGIN + CALCULATE_PATH, data=form, headers=HEADERS, timeout=self.timeout)
            response.raise_for_status()
            calculate_amt = _as_amount(response.json().get("calculateAmt"))
            if calculate_amt is None:
                raise FeeUnavailable("공항 예상 주차요금 응답을 확인할 수 없습니다.")
            params = {**form, "calculateAmt": calculate_amt}
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
