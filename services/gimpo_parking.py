"""HTTP controller for the local Gimpo reservation coordinator."""
import os
import threading
from urllib.parse import urlparse

from flask import Blueprint, current_app, jsonify, render_template, request

from services.gimpo_config import CONFIG
from services.gimpo_jobs import GimpoRuntime, RuntimeUnavailable
from services.gimpo_store import Conflict
from services.gimpo_validation import AIRPORT, DEFAULT_FIELDS, PARKING, PARKING_NAME, InputError, policy, validate
from services.background_notifications import BackgroundNotifications
from services.notification_config import CONFIG as NOTIFICATION_CONFIG, TelegramSettings
from services.notification_messages import ReservationMessages
from services.telegram_notifier import TelegramNotifier

bp = Blueprint("gimpo_parking", __name__, url_prefix="/gimpo-parking")


class GimpoService:
    """Lazy composition root: no workers, browsers or DB writes during import."""
    def __init__(self, config=CONFIG, runtime_factory=GimpoRuntime):
        self.config, self.runtime_factory = config, runtime_factory
        self._runtime = None
        self._lock = threading.Lock()
        self.notification_settings = TelegramSettings.from_environment()
        self.notifications = BackgroundNotifications(TelegramNotifier(self.notification_settings),
                                                     NOTIFICATION_CONFIG.max_attempts)

    def runtime(self):
        if current_app.debug or os.environ.get("WERKZEUG_RUN_MAIN"):
            raise RuntimeUnavailable("김포 작업은 debug/reloader를 끄고 python app.py로 실행해주세요.")
        with self._lock:
            if self._runtime is None:
                self._runtime = self.runtime_factory(self.config, notifier=TelegramNotifier(self.notification_settings))
            return self._runtime

    def notification_status(self):
        status = self.notifications.status()
        with self._lock:
            if self._runtime:
                status["lastDelivery"] = self._runtime.outbox.status()["lastDelivery"]
        return status

    def test_notification(self):
        return self.notifications.publish_test(ReservationMessages.test("김포공항 국내선 주차"))

    def close(self):
        with self._lock:
            if self._runtime:
                self._runtime.close()
                self._runtime = None
            self.notifications.close()


def service():
    return current_app.extensions["gimpo"]


def body():
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise InputError("JSON 객체가 필요합니다.")
    return value


def accepted(job):
    return jsonify({"job": job, "commandId": job.get("commandId")}), 202


@bp.before_request
def guard_request():
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("Origin")
        if origin and urlparse(origin).netloc != request.host:
            return jsonify(error="현재 서비스 화면에서 요청해주세요."), 403
        if not request.is_json:
            return jsonify(error="JSON 요청이 필요합니다."), 400


@bp.after_request
def private_response(response):
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.errorhandler(InputError)
def invalid(error):
    return jsonify(error=str(error)), 400


@bp.errorhandler(Conflict)
def conflict(error):
    return jsonify(error=str(error)), 409


@bp.errorhandler(RuntimeUnavailable)
def unavailable(error):
    return jsonify(error=str(error)), 503


@bp.errorhandler(KeyError)
def missing(error):
    return jsonify(error="작업을 찾을 수 없습니다."), 404


@bp.route("/")
def index():
    return render_template("gimpo_parking.html")


@bp.get("/api/options")
def options():
    return jsonify(airports=[{"value": AIRPORT, "label": "김포공항"}],
                   parkingLots=[{"value": PARKING, "label": PARKING_NAME, "airportCode": AIRPORT}],
                   discounts=[{"value": "DC001", "label": "일반 (할인 자격 조회 미지원)"}],
                   policy=policy(), intervalSeconds=service().config.interval_sec,
                   handoffMaxAgeSeconds=service().config.handoff_max_age_sec)


@bp.route("/api/defaults", methods=["GET", "POST"])
def defaults():
    runtime = service().runtime()
    if request.method == "GET":
        return jsonify(runtime.store.get_defaults())
    data = body()
    if data.get("saveConsent") is not True:
        raise InputError("차량번호·연락처를 이 PC에 저장하는 데 동의해주세요.")
    saved = {k: data[k] for k in DEFAULT_FIELDS if k in data}
    for key, value in saved.items():
        if key == "intervalSeconds":
            if type(value) is not int or not 30 <= value <= 3600:
                raise InputError("조회 주기를 확인해주세요.")
        elif not isinstance(value, str) or len(value) > 100:
            raise InputError("저장할 입력값을 확인해주세요.")
    runtime.store.save_defaults(saved)
    return jsonify(message="예약 정보를 저장했습니다.")


@bp.post("/api/jobs")
def create():
    inputs = validate(body(), service().config.interval_sec)
    return accepted(service().runtime().create(inputs))


@bp.get("/api/jobs/active")
def active():
    runtime = service().runtime()
    return jsonify(job=runtime.store.active(), recent=runtime.store.recent())


@bp.get("/api/jobs/<job_id>")
def status(job_id):
    runtime = service().runtime()
    job = runtime.store.get(job_id)
    return jsonify(job=job, notifications=runtime.store.events(job_id), browserAvailable=job_id in runtime.clients)


@bp.get("/api/jobs/<job_id>/logs")
def logs(job_id):
    return jsonify(logs=service().runtime().store.get(job_id)["logs"])


@bp.patch("/api/jobs/<job_id>")
def update(job_id):
    data = body()
    inputs = validate(data.get("inputs"), service().config.interval_sec)
    return accepted(service().runtime().stop(job_id, data, replacement=inputs))


@bp.post("/api/jobs/<job_id>/<action>")
def command(job_id, action):
    data, runtime = body(), service().runtime()
    if action == "stop":
        job = runtime.stop(job_id, data)
    elif action == "prepare":
        job = runtime.prepare(job_id, data)
    elif action == "proceed":
        job = runtime.proceed(job_id, data, data.get("autoProceedConsent"))
    elif action == "reprepare":
        inputs = validate(data.get("inputs"), service().config.interval_sec)
        job = runtime.restart(job_id, data, inputs)
    elif action == "show-browser":
        job = runtime.show(job_id, data)
    elif action == "resolve":
        job = runtime.resolve(job_id, data, data.get("outcome"), data.get("acknowledged"))
    else:
        return jsonify(error="지원하지 않는 명령입니다."), 404
    return accepted(job)


@bp.get("/api/notifications/status")
def notification_status():
    return jsonify(service().notification_status())


@bp.post("/api/notifications/test")
def notification_test():
    body()
    return jsonify(service().test_notification()), 202


@bp.post("/api/jobs/<job_id>/notifications/resend")
def resend(job_id):
    data = body()
    runtime = service().runtime()
    event = runtime.store.resend(job_id, data, data.get("eventId"), data.get("round"))
    return jsonify(event=event, message="알림 재전송을 요청했습니다. 중복으로 도착할 수 있습니다."), 202
