"""T2 예약 화면 입력 검사.

화면(static/js/app.js의 FORM_FIELDS·FORMAT_RULES)과 같은 형식 규칙을 서버에서 다시 확인한다.
화면 검사는 편의이고, 예약 API로 나가는 값의 마지막 관문은 여기다.
"""
import re

MIN_INTERVAL_SEC = 10
MAX_INTERVAL_SEC = 86400
MAX_TEXT_LEN = 50

_CODE = re.compile(r"[A-Z0-9]{1,10}")
_DATETIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

# 필드 → (화면 라벨, 형식). 형식이 None이면 길이·한 줄만 본다. 빈 값은 형식 검사를 하지 않는다.
FIELDS = {
    "name": ("예약자명", None),
    "phone": ("휴대폰 번호", re.compile(r"010[0-9]{8}")),
    "carNumber": ("차량번호", re.compile(r"[0-9]{2,3}[가-하][0-9]{4}")),
    "carModel": ("차종", None),
    "carBrand": ("제조사", _CODE),
    "carColor": ("색상", _CODE),
    "departingAt": ("출발일시", _DATETIME),
    "arrivedAt": ("도착일시", _DATETIME),
    "departingAir": ("출발 항공사", _CODE),
}


class InputError(ValueError):
    """사용자에게 그대로 보여 줄 입력 오류."""


def validate_fields(data, *, require_contact):
    """화면 입력을 검사해 FIELDS의 키만 담은 사전을 돌려준다. 빠진 필드는 빈 문자열이다."""
    if not isinstance(data, dict):
        raise InputError("요청 본문은 JSON 객체여야 합니다.")
    fields = {}
    for key, (label, pattern) in FIELDS.items():
        value = data.get(key, "")
        if not isinstance(value, str):
            raise InputError(f"{label}: 문자열이어야 합니다.")
        if pattern is None and (len(value) > MAX_TEXT_LEN or _CONTROL_CHARS.search(value)):
            raise InputError(f"{label}: {MAX_TEXT_LEN}자 이하의 한 줄로 입력해주세요.")
        if value and pattern is not None and not pattern.fullmatch(value):
            raise InputError(f"{label} 형식을 확인해주세요.")
        fields[key] = value
    if require_contact and not (fields["name"] and fields["phone"]):
        raise InputError("이름과 휴대전화는 필수입니다.")
    return fields


def parse_interval(value):
    """호출 주기(초). 정수 또는 숫자 문자열만 받는다. bool은 int의 하위 타입이라 따로 막는다."""
    if type(value) is int:
        seconds = value
    elif isinstance(value, str) and re.fullmatch(r"[0-9]{1,9}", value):
        seconds = int(value)
    else:
        raise InputError("호출 주기는 정수여야 합니다.")
    if seconds < MIN_INTERVAL_SEC:
        raise InputError(f"호출 주기는 최소 {MIN_INTERVAL_SEC}초 이상이어야 합니다.")
    if seconds > MAX_INTERVAL_SEC:
        raise InputError(f"호출 주기는 {MAX_INTERVAL_SEC}초 이하여야 합니다.")
    return seconds
