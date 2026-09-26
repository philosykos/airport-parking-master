import copy

import pytest

from services import config, t2_valet
from services.config import ConfigError, find_legacy_env_keys, load_toml
from services.t2_valet import parse_config


def valid_raw():
    # 커밋된 파일에서 시작해 한 곳만 바꾼다. 검증 규칙이 바뀌어도 입력이 실제 파일과 어긋나지 않는다.
    return copy.deepcopy(load_toml("t2_valet"))


def test_committed_config_parses():
    cfg = parse_config(valid_raw())
    assert cfg.url == "https://api.amanopark.co.kr/api/web/booking/reservation"
    assert cfg.interval_sec == 30
    assert cfg.payload == {
        "carType": "BASIC",
        "type": "BASIC",
        "customerRequest": None,
        "root": "WEB",
        "isUsingCarWash": False,
        "isCrew": False,
        "carWashType": None,
    }


def test_module_config_matches_file():
    assert t2_valet.CONFIG == parse_config(valid_raw())


@pytest.mark.parametrize("section, key, value, expected_key", [
    ("request", "url", None, "request.url"),  # None은 키 삭제
    ("request", "url", "ftp://x", "request.url"),
    ("request", "interval_sec", 5, "request.interval_sec"),
    ("request", "interval_sec", True, "request.interval_sec"),
    ("request", "interval_sec", "30", "request.interval_sec"),
    ("payload", "is_crew", "yes", "payload.is_crew"),
    ("payload", "car_type", "", "payload.car_type"),
    ("payload", "is_using_carwash", False, "payload.is_using_carwash"),
])
def test_invalid_config_names_key(section, key, value, expected_key):
    raw = valid_raw()
    if value is None:
        del raw[section][key]
    else:
        raw[section][key] = value
    with pytest.raises(ConfigError) as exc:
        parse_config(raw)
    message = str(exc.value)
    assert message.startswith("config/t2_valet.toml: ")
    assert expected_key in message


def test_missing_table():
    raw = valid_raw()
    del raw["payload"]
    with pytest.raises(ConfigError, match="payload"):
        parse_config(raw)


def test_unknown_table():
    raw = valid_raw()
    raw["extra"] = {}
    with pytest.raises(ConfigError, match="extra"):
        parse_config(raw)


def test_interval_error_shows_current_value():
    raw = valid_raw()
    raw["request"]["interval_sec"] = 5
    with pytest.raises(ConfigError, match=r"request\.interval_sec — 10 이상이어야 합니다 \(현재 5\)"):
        parse_config(raw)


def test_customer_request_kept_when_set():
    raw = valid_raw()
    raw["payload"]["customer_request"] = "문 앞"
    assert parse_config(raw).payload["customerRequest"] == "문 앞"


def test_load_toml_missing_file():
    with pytest.raises(ConfigError, match="nope.toml"):
        load_toml("nope")


def test_load_toml_syntax_error(tmp_path, monkeypatch):
    (tmp_path / "broken.toml").write_text("[request\nurl = 1\n", encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    with pytest.raises(ConfigError, match="broken.toml"):
        load_toml("broken")


def test_find_legacy_env_keys(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# CAR_TYPE=BASIC\nREQUEST_URL=https://x\n\nOTHER=1\nIS_CREW = false\n", encoding="utf-8")
    assert find_legacy_env_keys(env, t2_valet.LEGACY_ENV_KEYS) == ["REQUEST_URL", "IS_CREW"]


def test_find_legacy_env_keys_without_file(tmp_path):
    assert find_legacy_env_keys(tmp_path / ".env", t2_valet.LEGACY_ENV_KEYS) == []


def test_find_legacy_env_keys_unreadable_file(tmp_path):
    env = tmp_path / ".env"
    env.write_bytes("# 한글 주석\nREQUEST_URL=https://x\n".encode("cp949"))
    assert find_legacy_env_keys(env, t2_valet.LEGACY_ENV_KEYS) == []
