import ssl
from datetime import datetime, timedelta

import pytest
import requests
import truststore

from services import web_security
from services.gimpo import validation as gimpo_validation
from services.gimpo.client import ORIGIN
from services.gimpo.fee import AirportFeeClient, FeeUnavailable
from services.gimpo.validation import parse_date, policy
from tests.gimpo.fakes import FakeFeeClient
from tests.gimpo.helpers import NOW


class FakeResponse:
    def __init__(self, json_data=None, ok=True):
        self._json_data = json_data
        self.ok = ok

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError("bad status")

    def json(self):
        if self._json_data is None:
            raise ValueError("no json body")
        return self._json_data


class FakeSession:
    def __init__(self, calculate_amt=8000, discount_amt=1600, post_error=None, get_error=None,
                 calculate_json=None, discount_json=None):
        self.calls = []
        self.calculate_amt = calculate_amt
        self.discount_amt = discount_amt
        self.post_error = post_error
        self.get_error = get_error
        self.calculate_json = calculate_json
        self.discount_json = discount_json

    def post(self, url, data=None, headers=None, timeout=None):
        self.calls.append(("POST", url, data, headers, timeout))
        if self.post_error:
            raise self.post_error
        return FakeResponse(self.calculate_json if self.calculate_json is not None else {"calculateAmt": self.calculate_amt})

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(("GET", url, params, headers, timeout))
        if self.get_error:
            raise self.get_error
        return FakeResponse(self.discount_json if self.discount_json is not None else {"discountAmt": self.discount_amt})


INPUTS = dict(parkingId="2", entryAt="2026-10-10 10:00", exitAt="2026-10-10 14:00", discountSelection="DC005")


def test_quote_calls_calculate_amt_with_dc001_regardless_of_selection():
    session = FakeSession(calculate_amt=8000, discount_amt=1600)
    client = AirportFeeClient(session=session)
    result = client.quote(**INPUTS)
    assert result == {"calculateAmt": 8000, "discountAmt": 1600, "estimatedAmt": 6400}
    method, url, data, headers, timeout = session.calls[0]
    assert method == "POST" and url == ORIGIN + "/main/calculateAmt.json"
    assert data == {"sectnId": "2", "inDttm": "2026-10-10 10:00:00", "outDttm": "2026-10-10 14:00:00", "discountCd": "DC001"}
    assert headers["X-Requested-With"] == "XMLHttpRequest" and timeout == 5


def test_quote_calls_discount_with_selected_code_and_received_calculate_amt():
    session = FakeSession(calculate_amt=8000, discount_amt=1600)
    client = AirportFeeClient(session=session)
    client.quote(**INPUTS)
    method, url, params, headers, timeout = session.calls[1]
    assert method == "GET" and url == ORIGIN + "/reservation/calculateDiscountAmt.json"
    assert params["discountCd"] == "DC005"
    assert params["calculateAmt"] == 8000


def test_quote_skips_discount_call_when_selection_is_dc001():
    session = FakeSession(calculate_amt=8000, discount_amt=1600)
    client = AirportFeeClient(session=session)
    result = client.quote(**{**INPUTS, "discountSelection": "DC001"})
    assert result == {"calculateAmt": 8000, "discountAmt": 0, "estimatedAmt": 8000}
    assert len(session.calls) == 1


def test_quote_caches_second_call_for_same_input():
    clock = iter([0.0, 1.0, 2.0]).__next__
    session = FakeSession()
    client = AirportFeeClient(session=session, clock=clock)
    client.quote(**INPUTS)
    client.quote(**INPUTS)
    assert len(session.calls) == 2  # 두 번째 호출은 공항을 다시 부르지 않는다(한 번의 POST+GET만)


def test_quote_treats_different_input_as_new_cache_key():
    session = FakeSession()
    client = AirportFeeClient(session=session)
    client.quote(**INPUTS)
    client.quote(**{**INPUTS, "exitAt": "2026-10-10 16:00"})
    assert len(session.calls) == 4


def test_quote_purges_expired_entries_when_caching_a_new_result():
    # 키 A를 넣고 ttl을 넘긴 뒤 키 B를 조회하면 새 결과를 캐시에 넣는 같은 잠금 안에서
    # 만료된 A가 지워지고 B만 남는다.
    clock = iter([0.0, 100.0]).__next__
    session = FakeSession()
    client = AirportFeeClient(session=session, ttl=10, clock=clock)
    key_a = (INPUTS["parkingId"], INPUTS["entryAt"], INPUTS["exitAt"], INPUTS["discountSelection"])
    other = {**INPUTS, "exitAt": "2026-10-10 16:00"}
    key_b = (other["parkingId"], other["entryAt"], other["exitAt"], other["discountSelection"])
    client.quote(**INPUTS)
    client.quote(**other)
    assert key_a not in client._cache
    assert key_b in client._cache
    assert len(client._cache) == 1


def test_quote_fails_on_non_integer_calculate_amount():
    session = FakeSession(calculate_json={"calculateAmt": "8000"})
    client = AirportFeeClient(session=session)
    try:
        client.quote(**INPUTS)
        assert False, "should have raised"
    except FeeUnavailable:
        pass


def test_quote_fails_when_discount_exceeds_calculate_amount():
    session = FakeSession(calculate_amt=8000, discount_amt=9000)
    client = AirportFeeClient(session=session)
    try:
        client.quote(**INPUTS)
        assert False, "should have raised"
    except FeeUnavailable:
        pass


def test_quote_fails_on_negative_discount():
    session = FakeSession(calculate_amt=8000, discount_amt=-1)
    client = AirportFeeClient(session=session)
    try:
        client.quote(**INPUTS)
        assert False, "should have raised"
    except FeeUnavailable:
        pass


def test_quote_fails_on_timeout():
    session = FakeSession(post_error=requests.Timeout("timed out"))
    client = AirportFeeClient(session=session)
    try:
        client.quote(**INPUTS)
        assert False, "should have raised"
    except FeeUnavailable:
        pass


def test_quote_fails_on_bad_http_status():
    session = FakeSession()
    session.post = lambda *a, **k: FakeResponse({"calculateAmt": 8000}, ok=False)
    client = AirportFeeClient(session=session)
    try:
        client.quote(**INPUTS)
        assert False, "should have raised"
    except FeeUnavailable:
        pass


def test_default_session_mounts_truststore_context_for_https():
    client = AirportFeeClient()
    adapter = client.session.get_adapter("https://example.com")
    ssl_context = adapter.poolmanager.connection_pool_kw.get("ssl_context")
    assert isinstance(ssl_context, truststore.SSLContext)


def test_route_rejects_invalid_input_with_400(client):
    response = client.get("/gimpo-parking/api/fee", query_string={
        "parkingId": "999", "entryAt": "2026-10-10 10:00", "exitAt": "2026-10-10 14:00", "discountSelection": "DC001"})
    assert response.status_code == 400
    assert "error" in response.get_json()


def test_route_rejects_missing_period_with_400(client):
    response = client.get("/gimpo-parking/api/fee", query_string={"parkingId": "2"})
    assert response.status_code == 400


@pytest.fixture
def fee_route(client, monkeypatch):
    # 실제 앱 싱글턴(app.extensions['gimpo'])의 공항 요금 클라이언트를 가짜로 바꿔 호출 여부를 셀 수 있게 한다.
    from app import app
    fake = FakeFeeClient()
    monkeypatch.setattr(app.extensions["gimpo"], "fee", fake)
    return client, fake


class _FixedNow(datetime):
    """`services.gimpo.validation`의 `datetime.now(...)`를 고정 시각으로 바꾼다."""
    @classmethod
    def now(cls, tz=None):
        return NOW


@pytest.fixture
def frozen_policy_limits(monkeypatch):
    # 라우트가 내부에서 부르는 policy()도 이 고정 시각을 쓰게 해, 테스트가 계산한 경계값과
    # 서버가 실제로 적용하는 경계값이 항상 일치하게 한다(실행 시각의 벽시계에 기대지 않는다).
    monkeypatch.setattr(gimpo_validation, "datetime", _FixedNow)
    limits = policy(NOW)
    return parse_date(limits["entryMin"]), parse_date(limits["exitMax"])


def _fmt(dt):
    return dt.strftime("%Y-%m-%d %H:%M")


def _policy_limits():
    limits = policy()
    return parse_date(limits["entryMin"]), parse_date(limits["exitMax"])


def _valid_query(**overrides):
    entry_min, _ = _policy_limits()
    query = {"parkingId": "2", "entryAt": _fmt(entry_min), "exitAt": _fmt(entry_min + timedelta(hours=2)),
             "discountSelection": "DC001"}
    query.update(overrides)
    return query


def test_route_rejects_cross_site_without_calling_fee_client(fee_route):
    client, fake = fee_route
    response = client.get("/gimpo-parking/api/fee", query_string=_valid_query(),
                          headers={"Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 403
    assert response.get_json() == {"error": web_security.CROSS_SITE_ERROR}
    assert fake.calls == []


@pytest.mark.parametrize("headers", [{"Sec-Fetch-Site": "same-origin"}, {}])
def test_route_allows_same_origin_and_no_header(fee_route, headers):
    client, fake = fee_route
    response = client.get("/gimpo-parking/api/fee", query_string=_valid_query(), headers=headers)
    assert response.status_code == 200


def test_route_rejects_entry_before_policy_minimum(fee_route, frozen_policy_limits):
    client, fake = fee_route
    entry_min, _ = frozen_policy_limits
    entry = entry_min - timedelta(minutes=10)
    response = client.get("/gimpo-parking/api/fee",
                          query_string=_valid_query(entryAt=_fmt(entry), exitAt=_fmt(entry + timedelta(hours=2))))
    assert response.status_code == 400
    assert fake.calls == []


def test_route_rejects_exit_after_policy_maximum(fee_route, frozen_policy_limits):
    client, fake = fee_route
    entry_min, exit_max = frozen_policy_limits
    exit_at = exit_max + timedelta(minutes=10)
    response = client.get("/gimpo-parking/api/fee",
                          query_string=_valid_query(entryAt=_fmt(entry_min), exitAt=_fmt(exit_at)))
    assert response.status_code == 400
    assert fake.calls == []


def test_route_rejects_time_not_on_ten_minute_step(fee_route, frozen_policy_limits):
    client, fake = fee_route
    entry_min, _ = frozen_policy_limits
    entry = entry_min + timedelta(minutes=5)
    response = client.get("/gimpo-parking/api/fee",
                          query_string=_valid_query(entryAt=_fmt(entry), exitAt=_fmt(entry + timedelta(hours=2))))
    assert response.status_code == 400
    assert fake.calls == []


def test_route_rejects_period_under_two_hours(fee_route, frozen_policy_limits):
    client, fake = fee_route
    entry_min, _ = frozen_policy_limits
    response = client.get("/gimpo-parking/api/fee",
                          query_string=_valid_query(entryAt=_fmt(entry_min), exitAt=_fmt(entry_min + timedelta(minutes=90))))
    assert response.status_code == 400
    assert fake.calls == []


def test_route_rejects_period_over_thirty_days(fee_route, frozen_policy_limits):
    client, fake = fee_route
    entry_min, _ = frozen_policy_limits
    response = client.get("/gimpo-parking/api/fee",
                          query_string=_valid_query(entryAt=_fmt(entry_min), exitAt=_fmt(entry_min + timedelta(days=31))))
    assert response.status_code == 400
    assert fake.calls == []


def test_route_rejects_unknown_discount_code(fee_route, frozen_policy_limits):
    client, fake = fee_route
    entry_min, _ = frozen_policy_limits
    response = client.get("/gimpo-parking/api/fee",
                          query_string=_valid_query(entryAt=_fmt(entry_min), exitAt=_fmt(entry_min + timedelta(hours=2)),
                                                     discountSelection="DC999"))
    assert response.status_code == 400
    assert fake.calls == []
