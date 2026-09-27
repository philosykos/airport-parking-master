"""OfficialContract 순수 판정. 실제 브라우저 테스트(test_browser.py)는 분기마다 대표 한 건만 돈다."""
import json
from pathlib import Path

import pytest

from services.gimpo.client import BrowserFault, OfficialContract
from services.gimpo.validation import PARKING_NAME
from tests.gimpo.helpers import inputs

CONTRACT = json.loads((Path(__file__).parent / 'fixtures/contract.json').read_text())


@pytest.mark.parametrize('data,duplicate,expected', [
    (CONTRACT['availability']['available'], False, '00'),
    (CONTRACT['availability']['full'], False, '10'),
    (CONTRACT['duplicate']['clear'], True, '00'),
    (CONTRACT['duplicate']['duplicate'], True, '10'),
    (CONTRACT['duplicate']['noShow'], True, '20'),
])
def test_known_codes_are_accepted(data, duplicate, expected):
    assert OfficialContract.code(data, duplicate) == expected


@pytest.mark.parametrize('data,duplicate', [
    ({'result': {'code': '20'}}, False),  # 예약부도 코드는 중복 검사 응답에서만 뜻이 있다
    ({'code': '30'}, True),
    ({'result': {}}, False),
    ({'code': '00'}, False),
    (None, True),
])
def test_unknown_codes_or_shapes_fail_closed(data, duplicate):
    with pytest.raises(BrowserFault):
        OfficialContract.code(data, duplicate)


def fields(raw, **amounts):
    values = {**CONTRACT['amounts'], **amounts}
    return {'resInDttm': [raw['entryAt'] + ':00'], 'resOutDttm': [raw['exitAt'] + ':00'],
            'parkingDivCd': [raw['airportCode']], 'sectnId': [raw['parkingId']],
            'discountCd': [raw['discountSelection']], 'parkingName': [PARKING_NAME], 'airportNm': ['김포공항'],
            **{name: [value] for name, value in values.items()}}


def test_captured_amounts_pass():
    raw = {**inputs(), 'discountSelection': 'DC001'}
    summary = OfficialContract.summary(fields(raw), raw)
    assert summary['paymentAmt'] == 10000 and summary['receiptAmt'] == -2000


@pytest.mark.parametrize('amounts', [
    {'paymentAmt': '0', 'depositAmt': '0'},
    {'paymentAmt': '-1', 'depositAmt': '-1'},
    {'depositAmt': '9000'},
    {'discountAmt': '9000'},
    {'calculateAmt': '-1'},
    {'discountAmt': '1'},  # 할인 없음(DC001)인데 할인 금액이 있다
    {'paymentAmt': '1e4'},
])
def test_unexpected_amounts_fail_closed(amounts):
    raw = {**inputs(), 'discountSelection': 'DC001'}
    with pytest.raises(BrowserFault):
        OfficialContract.summary(fields(raw, **amounts), raw)


def test_discount_within_calculated_amount_passes_for_discount_codes():
    raw = {**inputs(), 'discountSelection': 'DC007'}
    assert OfficialContract.summary(fields(raw, discountAmt='1600'), raw)['discountAmt'] == 1600


def test_mismatched_identity_fails_closed():
    raw = {**inputs(), 'discountSelection': 'DC001'}
    with pytest.raises(BrowserFault):
        OfficialContract.summary(fields({**raw, 'discountSelection': 'DC005'}), raw)
