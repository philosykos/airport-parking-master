"""서울 시간 입력 정책. 브라우저와 서버가 같은 제한을 사용한다."""
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

SEOUL = ZoneInfo("Asia/Seoul")
AIRPORT = "PLT-002"
PARKING = "2"
PARKING_NAME = "국내선 제2주차장 주차타워 2, 3층"
AGREEMENTS = ("agree01", "agree03", "agree04", "agree05")
PUBLIC_INPUT = ("airportCode", "parkingId", "entryAt", "exitAt", "discountSelection", "intervalSeconds", "mode")
DEFAULT_FIELDS = PUBLIC_INPUT + ("carNumber", "phone")


class InputError(ValueError):
    pass


def policy(now=None):
    now = now or datetime.now(SEOUL)
    now = now.astimezone(SEOUL)
    minimum = now + timedelta(hours=2)
    rounded = minimum.replace(minute=(minimum.minute // 10) * 10, second=0, microsecond=0)
    minimum = rounded if rounded == minimum else rounded + timedelta(minutes=10)
    maximum = (now + timedelta(days=45)).replace(hour=23, minute=50, second=0, microsecond=0)
    return {"version": "gmp-2026-09-27", "timeZone": "Asia/Seoul", "now": now.isoformat(),
            "entryMin": minimum.strftime("%Y-%m-%d %H:%M"), "exitMax": maximum.strftime("%Y-%m-%d %H:%M"),
            "minuteStep": 10, "minimumMinutes": 120, "maximumMinutes": 30 * 24 * 60}


def parse_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}", value):
        raise InputError("일시는 YYYY-MM-DD HH:mm 형식으로 입력해주세요.")
    try:
        return datetime.strptime(value.replace("T", " "), "%Y-%m-%d %H:%M").replace(tzinfo=SEOUL)
    except ValueError:
        raise InputError("올바른 날짜와 시간을 입력해주세요.") from None


def validate(data, interval_default=30, now=None):
    if not isinstance(data, dict):
        raise InputError("입력 정보가 필요합니다.")
    result = {}
    if data.get("airportCode") != AIRPORT or str(data.get("parkingId")) != PARKING:
        raise InputError("지원하는 김포공항 예약주차장을 선택해주세요.")
    result.update(airportCode=AIRPORT, parkingId=PARKING)
    limits = policy(now)
    entry, end = parse_date(data.get("entryAt")), parse_date(data.get("exitAt"))
    if entry.minute % 10 or end.minute % 10:
        raise InputError("입출차 시간을 10분 단위로 입력해주세요.")
    if entry < parse_date(limits["entryMin"]) or end > parse_date(limits["exitMax"]):
        raise InputError("입차는 현재부터 2시간 이후, 출차는 오늘부터 45일 이내로 선택해주세요.")
    if not timedelta(hours=2) <= end - entry <= timedelta(days=30):
        raise InputError("예약 기간은 2시간 이상, 30일 이하여야 합니다.")
    result.update(entryAt=entry.strftime("%Y-%m-%d %H:%M"), exitAt=end.strftime("%Y-%m-%d %H:%M"))
    car = re.sub(r"\s", "", str(data.get("carNumber", "")))
    patterns = (r"(?:[가-힣]{2})?[0-9]{1,3}[가-힣][0-9]{4}", r"[가-힣]{1,2}[0-9]{4,6}", r"[가-힣]{0,2}[0-9]{2,4}-[0-9]{2,4}")
    if not any(re.fullmatch(p, car) for p in patterns):
        raise InputError("차량번호를 확인해주세요.")
    phone = re.sub(r"[\s()-]", "", str(data.get("phone", "")))
    if not re.fullmatch(r"[0-9]{10,11}", phone):
        raise InputError("휴대전화 번호는 숫자 10~11자리로 입력해주세요.")
    password = data.get("reservationPassword")
    if not isinstance(password, str) or not re.fullmatch(r"[A-Za-z0-9]{4,128}", password):
        raise InputError("예약 비밀번호는 영문·숫자 4~128자리로 입력해주세요.")
    if password != data.get("passwordConfirmation"):
        raise InputError("비밀번호가 일치하지 않습니다.")
    agreements = data.get("agreements", {})
    if not isinstance(agreements, dict) or any(agreements.get(k) is not True for k in AGREEMENTS):
        raise InputError("공식 이용안내·개인정보·취소수수료·주차존을 확인하고 모두 동의해주세요.")
    if data.get("discountSelection", "DC001") != "DC001":
        raise InputError("현재 일반 요금만 지원합니다.")
    interval = data.get("intervalSeconds", interval_default)
    if type(interval) is not int or not 30 <= interval <= 3600:
        raise InputError("조회 간격은 30~3600초로 입력해주세요.")
    mode = data.get("mode", "once")
    if mode not in ("once", "watch"):
        raise InputError("조회 모드를 확인해주세요.")
    if mode == "watch" and data.get("autoProceedConsent") is not True:
        raise InputError("결제 대기까지 자동 진행하는 데 동의해주세요.")
    result.update(carNumber=car, phone=phone, reservationPassword=password,
                  agreements={k: True for k in AGREEMENTS}, discountSelection="DC001",
                  intervalSeconds=interval, mode=mode, autoProceedConsent=data.get("autoProceedConsent") is True)
    return result
