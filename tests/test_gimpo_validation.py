from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from services.config import ConfigError, load_toml
from services.gimpo_config import parse_config
from services.gimpo_validation import AGREEMENTS, InputError, SEOUL, policy, validate

NOW = datetime(2026, 9, 27, 10, 0, tzinfo=SEOUL)


def valid_input(now=NOW, mode="once"):
    start = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    return {"airportCode": "PLT-002", "parkingId": "2", "entryAt": start.strftime("%Y-%m-%d %H:%M"),
            "exitAt": (start + timedelta(hours=4)).strftime("%Y-%m-%d %H:%M"), "carNumber": "123가4567", "phone": "01012345678",
            "reservationPassword": "PrivatePass44", "passwordConfirmation": "PrivatePass44", "discountSelection": "DC001",
            "agreements": {k: True for k in AGREEMENTS}, "intervalSeconds": 30, "mode": mode, "autoProceedConsent": True}


def test_normalization_and_timezone():
    data = valid_input()
    data.update(carNumber="123 가 4567", phone="010-1234-5678")
    result = validate(data, now=NOW)
    assert result["carNumber"] == "123가4567" and result["phone"] == "01012345678"
    assert policy(NOW) == policy(NOW.astimezone(timezone.utc))


@pytest.mark.parametrize('key,value', [('airportCode','other'), ('parkingId','14'), ('entryAt','2026-09-29 10:01'),
    ('exitAt','2026-09-28 11:50'), ('entryAt','2026-09-27 11:50'), ('entryAt','2026-02-30 10:00'),
    ('intervalSeconds',True), ('intervalSeconds',29), ('phone','01012345678x'), ('carNumber','bad'),
    ('reservationPassword','!bad'), ('passwordConfirmation','different'), ('agreements',{}), ('discountSelection','DC005')])
def test_invalid_input(key, value):
    data = valid_input(); data[key] = value
    with pytest.raises(InputError): validate(data, now=NOW)


def test_45_day_end_and_30_day_duration():
    data = valid_input()
    limit = NOW + timedelta(days=45)
    data['entryAt'] = (limit - timedelta(days=30)).replace(hour=23, minute=50).strftime('%Y-%m-%d %H:%M')
    data['exitAt'] = limit.replace(hour=23, minute=50).strftime('%Y-%m-%d %H:%M')
    validate(data, now=NOW)
    data['exitAt'] = (limit + timedelta(days=1)).replace(hour=0,minute=0).strftime('%Y-%m-%d %H:%M')
    with pytest.raises(InputError): validate(data, now=NOW)


def test_toml_config_and_strict_types():
    raw = load_toml('gimpo_parking')
    assert parse_config(raw).interval_sec == 30
    for section, key, value in [('request','interval_sec',True), ('request','interval_sec',5), ('storage','unknown',False), ('request','typo',1), ('storage','directory','')]:
        import copy
        changed = copy.deepcopy(raw); changed[section][key] = value
        with pytest.raises(ConfigError, match=f'{section}.{key}'):
            parse_config(changed)
