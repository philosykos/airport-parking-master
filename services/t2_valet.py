import functools
import os
import re
from dataclasses import dataclass
from datetime import datetime

import urllib3
import requests as http_requests
from flask import Blueprint, jsonify, render_template, request

from services.config import config_label, fail, load_toml, reject_unknown, require_table
from services.t2_input import FIELDS, MIN_INTERVAL_SEC, InputError, parse_interval, validate_fields
from services.t2_scheduler import Scheduler
from services.t2_storage import LogStore, UserDataStore

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 파일 경로는 services/ 가 아니라 프로젝트 루트 기준
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

bp = Blueprint("t2_valet", __name__, url_prefix="/t2-valet")

# T2 화면 CSP. 스크립트는 같은 출처 파일만 실행한다(인라인 onclick 금지).
# 스타일은 템플릿과 JS가 style 속성을 써서 'unsafe-inline'을 둔다. 외부 CSS·폰트는 jsDelivr(Pretendard)와 Google Fonts만 받는다.
CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com",
    "font-src 'self' data: https://cdn.jsdelivr.net https://fonts.gstatic.com",
    "img-src 'self' data:",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])


@bp.after_request
def set_csp(response):
    response.headers.setdefault("Content-Security-Policy", CSP)
    return response

# ── 설정 (config/t2_valet.toml) ──
CONFIG_LABEL = config_label("t2_valet")

# TOML 키: (예약 API 필드, 타입, 빈 문자열 허용 — 허용하면 None으로 보낸다)
PAYLOAD_FIELDS = {
    "car_type": ("carType", str, False),
    "booking_type": ("type", str, False),
    "root": ("root", str, False),
    "is_using_car_wash": ("isUsingCarWash", bool, False),
    "is_crew": ("isCrew", bool, False),
    "customer_request": ("customerRequest", str, True),
    "car_wash_type": ("carWashType", str, True),
}

# 예전 .env에서 쓰던 키. 남아 있으면 app.py가 옮기라고 경고한다.
LEGACY_ENV_KEYS = (
    "REQUEST_URL", "REQUEST_INTERVAL", "REQUEST_PAYLOAD", "CAR_TYPE", "BOOKING_TYPE",
    "ROOT", "IS_USING_CAR_WASH", "IS_CREW", "CUSTOMER_REQUEST", "CAR_WASH_TYPE",
)


@dataclass(frozen=True)
class T2Config:
    url: str
    interval_sec: int
    payload: dict  # 예약 API 필드 이름으로 매핑을 마친 고정 페이로드


def parse_config(raw):
    reject_unknown(raw, {"request", "payload"}, "", CONFIG_LABEL)
    request_cfg = require_table(raw, "request", CONFIG_LABEL)
    payload_cfg = require_table(raw, "payload", CONFIG_LABEL)
    reject_unknown(request_cfg, {"url", "interval_sec"}, "request.", CONFIG_LABEL)
    reject_unknown(payload_cfg, set(PAYLOAD_FIELDS), "payload.", CONFIG_LABEL)

    url = request_cfg.get("url")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        fail(CONFIG_LABEL, "request.url", "http:// 또는 https://로 시작하는 문자열이어야 합니다")

    interval = request_cfg.get("interval_sec")
    if type(interval) is not int:  # bool은 int의 하위 타입이라 isinstance로는 걸러지지 않는다
        fail(CONFIG_LABEL, "request.interval_sec", "정수여야 합니다")
    if interval < MIN_INTERVAL_SEC:
        fail(CONFIG_LABEL, "request.interval_sec",
             f"{MIN_INTERVAL_SEC} 이상이어야 합니다 (현재 {interval})")

    payload = {}
    for key, (field, kind, allow_empty) in PAYLOAD_FIELDS.items():
        path = f"payload.{key}"
        if key not in payload_cfg:
            fail(CONFIG_LABEL, path, "값이 없습니다")
        value = payload_cfg[key]
        if type(value) is not kind:
            fail(CONFIG_LABEL, path, "문자열이어야 합니다" if kind is str else "true 또는 false여야 합니다")
        if kind is str and value == "":
            if not allow_empty:
                fail(CONFIG_LABEL, path, "빈 문자열일 수 없습니다")
            value = None
        payload[field] = value

    return T2Config(url=url, interval_sec=interval, payload=payload)


CONFIG = parse_config(load_toml("t2_valet"))

# 개인정보 파일(소유자 전용). 경로는 services/ 가 아니라 프로젝트 루트 기준
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, mode=0o700, exist_ok=True)
log_store = LogStore(os.path.join(LOG_DIR, "api_call.log"))
user_store = UserDataStore(os.path.join(BASE_DIR, "user_data.json"))

# 워커 수명 관리
scheduler = Scheduler()

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
}

PHONE_IN_TEXT = re.compile(r"01[016789][-\s]?[0-9]{3,4}[-\s]?[0-9]{4}")


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def event(status, body):
    return {"time": now_text(), "type": "event", "status": status, "body": body}


def mask(value, keep_head, keep_tail=0):
    """앞 keep_head자와 뒤 keep_tail자만 남기고 가운데를 *로 가린다. 남길 것보다 짧으면 전부 가린다."""
    if len(value) <= keep_head + keep_tail:
        return "*" * len(value)
    return value[:keep_head] + "*" * (len(value) - keep_head - keep_tail) + value[len(value) - keep_tail:]


def redact_payload(payload):
    """로그·화면용 페이로드 사본. 예약자명·휴대폰·차량번호를 가린다. 예약 API에는 원본을 보낸다."""
    return {**payload,
            "name": mask(payload.get("name", ""), 1),
            "phone": mask(payload.get("phone", ""), 3, 4),
            "carNumber": mask(payload.get("carNumber", ""), 3)}


def redact_text(text, payload):
    """응답 본문·오류 문구에 섞인 요청 자신의 예약자명·휴대폰·차량번호와, 그 밖의 휴대폰 번호를 가린다."""
    fields = [
        (payload.get("name", ""), 1, 0),
        (payload.get("phone", ""), 3, 4),
        (payload.get("carNumber", ""), 3, 0),
    ]
    # 한 값이 다른 값의 일부일 수 있으므로 긴 값부터 바꾼다
    for value, keep_head, keep_tail in sorted(fields, key=lambda f: len(f[0]), reverse=True):
        if value:
            text = text.replace(value, mask(value, keep_head, keep_tail))
    return PHONE_IN_TEXT.sub(lambda m: mask(m.group(0), 3, 4), text)


def build_payload(fields):
    """검사를 마친 화면 필드를 설정의 고정 값에 합쳐 예약 API 페이로드를 만든다."""
    return {**CONFIG.payload, **fields}


def do_single_call(url, payload):
    """1회 HTTP POST 호출 후 로그 엔트리 반환. 엔트리에는 개인정보를 가린 사본만 담는다."""
    entry = {"time": now_text(), "type": "call", "url": url, "payload": redact_payload(payload)}
    try:
        resp = http_requests.post(url, json=payload, headers=DEFAULT_HEADERS, timeout=10, verify=False)
        entry.update(status=resp.status_code, body=redact_text(resp.text, payload)[:2000])
    except Exception as e:
        entry.update(status="ERROR", body=redact_text(str(e), payload)[:2000])
    return entry


def poll_once(url, payload):
    """예약 API를 한 번 호출해 기록한다. 예약에 성공(HTTP 200)하면 True를 돌려 스케줄을 끝낸다."""
    entry = do_single_call(url, payload)
    entry["type"] = "schedule"
    log_store.append(entry)
    if entry["status"] != 200:
        return False
    log_store.append(event("SUCCESS", "예약 성공! 스케줄러를 자동 종료합니다."))
    return True


@bp.route("/")
def index():
    return render_template("t2_valet.html")


@bp.errorhandler(InputError)
def input_error(e):
    return jsonify({"error": str(e)}), 400


@bp.route("/api/defaults")
def defaults():
    result = {"interval": str(CONFIG.interval_sec), "hasSavedData": False}
    saved = user_store.load()
    if saved:
        result.update({key: saved.get(key, "") for key in FIELDS})
        if "interval" in saved:
            result["interval"] = saved["interval"]
        result["hasSavedData"] = True
    return jsonify(result)


@bp.route("/api/save-defaults", methods=["POST"])
def save_defaults():
    data = request.get_json(silent=True)
    if not data:
        raise InputError("데이터가 없습니다.")
    fields = validate_fields(data, require_contact=False)
    saved = {key: value for key, value in fields.items() if key in data}
    if "interval" in data:
        saved["interval"] = str(parse_interval(data["interval"]))
    user_store.save(saved)
    return jsonify({"message": "저장되었습니다."})


@bp.route("/api/test", methods=["POST"])
def test_call():
    """테스트 1회 호출"""
    fields = validate_fields(request.get_json(silent=True), require_contact=True)
    entry = do_single_call(CONFIG.url, build_payload(fields))
    entry["type"] = "test"
    log_store.append(entry)
    return jsonify({"result": entry})


@bp.route("/api/start", methods=["POST"])
def start():
    if scheduler.running:
        return jsonify({"error": "이미 실행 중입니다."}), 400

    data = request.get_json(silent=True)
    fields = validate_fields(data, require_contact=True)
    interval_sec = parse_interval(data.get("interval", CONFIG.interval_sec))
    payload = build_payload(fields)

    def log_start():
        log_store.append(event("START", f"스케줄 시작 (주기: {interval_sec}초)"))

    if not scheduler.start(functools.partial(poll_once, CONFIG.url, payload), interval_sec, on_start=log_start):
        return jsonify({"error": "이미 실행 중입니다."}), 400
    return jsonify({"message": "호출을 시작합니다."})


@bp.route("/api/stop", methods=["POST"])
def stop():
    if not scheduler.stop():
        return jsonify({"error": "실행 중이 아닙니다."}), 400

    log_store.append(event("STOP", "스케줄 중지"))
    return jsonify({"message": "호출을 중지합니다."})


@bp.route("/api/logs")
def logs():
    return jsonify({"logs": log_store.recent(), "running": scheduler.running})


@bp.route("/api/logs/clear", methods=["POST"])
def clear_logs():
    log_store.clear()
    return jsonify({"message": "로그를 초기화했습니다."})
