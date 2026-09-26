import json
import os
import sys
import threading
from dataclasses import dataclass
from datetime import datetime

import urllib3
import requests as http_requests
from flask import Blueprint, jsonify, render_template, request

from services.config import config_label, fail, load_toml, reject_unknown, require_table

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# REQUEST_URL 필수 검증
REQUEST_URL = os.environ.get("REQUEST_URL", "").strip()
if not REQUEST_URL:
    print("[ERROR] REQUEST_URL 환경변수가 설정되지 않았습니다.")
    print("  .env 파일에 REQUEST_URL=https://... 형식으로 설정해주세요.")
    sys.exit(1)

# 파일 경로는 services/ 가 아니라 프로젝트 루트 기준
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USER_DATA_FILE = os.path.join(BASE_DIR, "user_data.json")

bp = Blueprint("t2_valet", __name__, url_prefix="/t2-valet")

# ── 설정 (config/t2_valet.toml) ──
CONFIG_LABEL = config_label("t2_valet")
MIN_INTERVAL_SEC = 10

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

# 로그 파일
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "api_call.log")

# 상태 관리
stop_event = threading.Event()
worker_thread = None
is_running = False

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
}

def _bool_env(key, default=False):
    val = os.environ.get(key, "").strip().lower()
    if not val:
        return default
    return val in ("true", "1", "yes")


def _str_env(key, default=None):
    val = os.environ.get(key, "").strip()
    return val if val else default


def _load_user_data():
    try:
        with open(USER_DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


FIXED_PAYLOAD = {
    "carType": _str_env("CAR_TYPE", "BASIC"),
    "type": _str_env("BOOKING_TYPE", "BASIC"),
    "customerRequest": _str_env("CUSTOMER_REQUEST", None),
    "root": _str_env("ROOT", "WEB"),
    "isUsingCarWash": _bool_env("IS_USING_CAR_WASH", False),
    "isCrew": _bool_env("IS_CREW", False),
    "carWashType": _str_env("CAR_WASH_TYPE", None),
}


def add_log(entry):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load_logs_from_file():
    """로그 파일에서 전체 로그를 읽어 반환"""
    logs = []
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    logs.append(json.loads(line))
    except FileNotFoundError:
        pass
    return logs


def build_payload(data):
    """프론트엔드 개별 필드를 API payload JSON으로 병합"""
    payload = dict(FIXED_PAYLOAD)
    payload["name"] = data.get("name", "")
    payload["phone"] = data.get("phone", "")
    payload["carNumber"] = data.get("carNumber", "")
    payload["carModel"] = data.get("carModel", "")
    payload["carBrand"] = data.get("carBrand", "")
    payload["carColor"] = data.get("carColor", "")
    payload["departingAt"] = data.get("departingAt", "")
    payload["arrivedAt"] = data.get("arrivedAt", "")
    payload["departingAir"] = data.get("departingAir", "")
    return payload


def do_single_call(url, payload):
    """1회 HTTP POST 호출 후 로그 엔트리 반환"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        resp = http_requests.post(url, json=payload, headers=DEFAULT_HEADERS, timeout=10, verify=False)
        return {
            "time": timestamp,
            "type": "call",
            "status": resp.status_code,
            "body": resp.text[:2000],
            "url": url,
            "payload": payload,
        }
    except Exception as e:
        return {
            "time": timestamp,
            "type": "call",
            "status": "ERROR",
            "body": str(e)[:2000],
            "url": url,
            "payload": payload,
        }


def call_worker(url, payload, interval_sec):
    global is_running
    while not stop_event.is_set():
        entry = do_single_call(url, payload)
        entry["type"] = "schedule"
        add_log(entry)

        if entry["status"] == 200:
            add_log({
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "type": "event",
                "status": "SUCCESS",
                "body": "예약 성공! 스케줄러를 자동 종료합니다.",
            })
            stop_event.set()
            break

        stop_event.wait(interval_sec)
    is_running = False


@bp.route("/")
def index():
    return render_template("t2_valet.html")


@bp.route("/api/defaults")
def defaults():
    result = {"interval": os.environ.get("REQUEST_INTERVAL", "30")}
    user_data = _load_user_data()
    if user_data:
        fields = ["name", "phone", "carNumber", "carModel", "carBrand",
                  "carColor", "departingAt", "arrivedAt", "departingAir"]
        for f in fields:
            result[f] = user_data.get(f, "")
        if "interval" in user_data:
            result["interval"] = user_data["interval"]
        result["hasSavedData"] = True
    else:
        payload_str = os.environ.get("REQUEST_PAYLOAD", "").strip()
        if payload_str:
            try:
                p = json.loads(payload_str)
                for f in ["name", "phone", "carNumber", "carModel", "carBrand",
                          "carColor", "departingAt", "arrivedAt", "departingAir"]:
                    result[f] = p.get(f, "")
            except json.JSONDecodeError:
                pass
        result["hasSavedData"] = False
    return jsonify(result)


@bp.route("/api/save-defaults", methods=["POST"])
def save_defaults():
    data = request.get_json()
    if not data:
        return jsonify({"error": "데이터가 없습니다."}), 400
    allowed = ["name", "phone", "carNumber", "carModel", "carBrand",
               "carColor", "departingAt", "arrivedAt", "departingAir", "interval"]
    save_data = {k: data[k] for k in allowed if k in data}
    with open(USER_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(save_data, f, ensure_ascii=False, indent=2)
    return jsonify({"message": "저장되었습니다."})


@bp.route("/api/test", methods=["POST"])
def test_call():
    """테스트 1회 호출"""
    data = request.get_json()
    if not data.get("name") or not data.get("phone"):
        return jsonify({"error": "이름과 휴대전화는 필수입니다."}), 400

    payload = build_payload(data)
    entry = do_single_call(REQUEST_URL, payload)
    entry["type"] = "test"
    add_log(entry)

    return jsonify({"result": entry})


@bp.route("/api/start", methods=["POST"])
def start():
    global worker_thread, is_running

    if is_running:
        return jsonify({"error": "이미 실행 중입니다."}), 400

    data = request.get_json()
    if not data.get("name") or not data.get("phone"):
        return jsonify({"error": "이름과 휴대전화는 필수입니다."}), 400

    payload = build_payload(data)
    interval_sec = int(data.get("interval", 30))

    if interval_sec < 10:
        return jsonify({"error": "호출 주기는 최소 10초 이상이어야 합니다."}), 400

    add_log({
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "type": "event",
        "status": "START",
        "body": f"스케줄 시작 (주기: {interval_sec}초)",
    })

    stop_event.clear()
    is_running = True
    worker_thread = threading.Thread(target=call_worker, args=(REQUEST_URL, payload, interval_sec), daemon=True)
    worker_thread.start()

    return jsonify({"message": "호출을 시작합니다."})


@bp.route("/api/stop", methods=["POST"])
def stop():
    global is_running

    if not is_running:
        return jsonify({"error": "실행 중이 아닙니다."}), 400

    stop_event.set()
    is_running = False

    add_log({
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "type": "event",
        "status": "STOP",
        "body": "스케줄 중지",
    })

    return jsonify({"message": "호출을 중지합니다."})


@bp.route("/api/logs")
def logs():
    return jsonify({"logs": load_logs_from_file(), "running": is_running})


@bp.route("/api/logs/clear", methods=["POST"])
def clear_logs():
    open(LOG_FILE, "w").close()
    return jsonify({"message": "로그를 초기화했습니다."})
