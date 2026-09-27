import pytest

from services.t2_input import InputError, parse_interval, validate_fields

VALID = {"name": "홍길동", "phone": "01012345678", "carNumber": "12가3456", "carModel": "그랜저",
         "carBrand": "HY", "carColor": "BLACK", "departingAt": "2026-10-01 09:00",
         "arrivedAt": "2026-10-08 18:00", "departingAir": "KE"}


def test_valid_form_passes_through():
    assert validate_fields(VALID, require_contact=True) == VALID


def test_missing_fields_become_empty_strings():
    got = validate_fields({"name": "홍길동", "phone": "01012345678", "carBrand": ""}, require_contact=True)
    assert set(got) == set(VALID)
    assert got["carModel"] == "" and got["carBrand"] == ""


def test_unknown_keys_are_dropped():
    got = validate_fields({**VALID, "carType": "PREMIUM", "isCrew": True}, require_contact=True)
    assert "carType" not in got and "isCrew" not in got


@pytest.mark.parametrize("body", [None, [], "문자열", 3])
def test_non_object_body_is_rejected(body):
    with pytest.raises(InputError, match="^요청 본문은 JSON 객체여야 합니다.$"):
        validate_fields(body, require_contact=True)


@pytest.mark.parametrize("field, value, message", [
    ("name", 123, "예약자명: 문자열이어야 합니다."),
    ("carBrand", {"x": 1}, "제조사: 문자열이어야 합니다."),
    ("name", "가" * 51, "예약자명: 50자 이하의 한 줄로 입력해주세요."),
    ("carModel", "그랜저\n", "차종: 50자 이하의 한 줄로 입력해주세요."),
    ("phone", "010-1234-5678", "휴대폰 번호 형식을 확인해주세요."),
    ("phone", "0101234567", "휴대폰 번호 형식을 확인해주세요."),
    ("phone", "０１０12345678", "휴대폰 번호 형식을 확인해주세요."),
    ("carNumber", "12가 3456", "차량번호 형식을 확인해주세요."),
    ("departingAt", "2026/10/01 09:00", "출발일시 형식을 확인해주세요."),
    ("carBrand", "<script>", "제조사 형식을 확인해주세요."),
    ("departingAir", "ke", "출발 항공사 형식을 확인해주세요."),
])
def test_bad_field_is_rejected(field, value, message):
    with pytest.raises(InputError) as e:
        validate_fields({**VALID, field: value}, require_contact=True)
    assert str(e.value) == message


@pytest.mark.parametrize("body", [{}, {"name": "홍길동"}, {"phone": "01012345678"}])
def test_contact_is_required_for_calls(body):
    with pytest.raises(InputError, match="^이름과 휴대전화는 필수입니다.$"):
        validate_fields(body, require_contact=True)


def test_contact_is_optional_for_saving():
    assert validate_fields({"carModel": "그랜저"}, require_contact=False)["carModel"] == "그랜저"


@pytest.mark.parametrize("value, expected", [(10, 10), ("30", 30), (86400, 86400)])
def test_interval_accepts_integers_and_digit_strings(value, expected):
    assert parse_interval(value) == expected


@pytest.mark.parametrize("value, message", [
    ("abc", "호출 주기는 정수여야 합니다."),
    (True, "호출 주기는 정수여야 합니다."),
    (10.5, "호출 주기는 정수여야 합니다."),
    (None, "호출 주기는 정수여야 합니다."),
    ("", "호출 주기는 정수여야 합니다."),
    ("٣٠", "호출 주기는 정수여야 합니다."),
    ("9" * 5000, "호출 주기는 정수여야 합니다."),
    (9, "호출 주기는 최소 10초 이상이어야 합니다."),
    ("5", "호출 주기는 최소 10초 이상이어야 합니다."),
    (86401, "호출 주기는 86400초 이하여야 합니다."),
    (10 ** 30, "호출 주기는 86400초 이하여야 합니다."),
])
def test_interval_rejects_bad_values(value, message):
    with pytest.raises(InputError) as e:
        parse_interval(value)
    assert str(e.value) == message
