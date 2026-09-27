"""판정은 순수 함수이고, COMPLETION_SCRIPT는 fixture를 브라우저로 읽는다."""
import json
from pathlib import Path

import pytest

from services.gimpo.client import BrowserFault, COMPLETION_SCRIPT, OfficialContract
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


FIXTURES = Path(__file__).parent / 'fixtures'
INPUTS = {'entryAt': '2026-10-28 10:00', 'exitAt': '2026-10-28 18:00', 'carNumber': '123가4567'}


def complete_html(**overrides):
    values = {'__ENTRY_AT__': INPUTS['entryAt'], '__EXIT_AT__': INPUTS['exitAt'],
              '__CAR_NUMBER__': INPUTS['carNumber'], '__RESERVATION_NO__': '1234AB5678', **overrides}
    html = (FIXTURES / 'step3_complete.html').read_text(encoding='utf-8')
    for key, value in values.items():
        html = html.replace(key, value)
    return html


def read_page(ui_context, html):
    with ui_context() as context:
        page = context.new_page()
        page.set_content(html)
        return page.evaluate(COMPLETION_SCRIPT)


def test_completion_reads_real_page_structure(ui_context):
    data = read_page(ui_context, complete_html())
    assert data['message'] == '주차 예약이 완료되었습니다.'
    assert data['fields']['주차장'] == '김포공항 국내선 제2주차장 주차타워 2, 3층'
    assert OfficialContract.completion(data, INPUTS) == '1234AB5678'


@pytest.mark.parametrize('change', [
    lambda d: d.update(message='예약 내역'),
    lambda d: d['fields'].update({'예약상태': '결제대기'}),
    lambda d: d['fields'].update({'예약번호': '1234ab'}),
    lambda d: d['fields'].update({'예약번호': 'AB12'}),
    lambda d: d['fields'].update({'차량입차': '2026-10-28 11:00:00'}),
    lambda d: d['fields'].update({'차량출차': '2026-10-28 19:00:00'}),
    lambda d: d['fields'].update({'주차장': '김포공항 국내선 제1주차장'}),
    lambda d: d['fields'].update({'차량번호': '999가9999'}),
    lambda d: d['fields'].pop('예약번호'),
])
def test_completion_rejects_any_mismatch(ui_context, change):
    data = read_page(ui_context, complete_html())
    change(data)
    with pytest.raises(BrowserFault, match='예약확인 화면을 확인하지 못했습니다.'):
        OfficialContract.completion(data, INPUTS)


def test_contract_lists_only_pg_failure_as_unverified():
    unverified = json.loads((FIXTURES / 'contract.json').read_text())['unverified']
    assert 'reservation completion' not in unverified and 'PG failure response' in unverified


def test_fixture_has_no_private_capture_values():
    text = (FIXTURES / 'step3_complete.html').read_text(encoding='utf-8')
    assert '4950' not in text and '010717' not in text
