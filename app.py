import os
import sys

from flask import Flask, redirect, render_template, url_for

from services.config import ConfigError, find_legacy_env_keys
from services import web_security

try:
    from services.t2 import valet as t2_valet
    from services.gimpo import parking as gimpo_parking
except ConfigError as e:
    print(f"[설정 오류] {e}")
    sys.exit(1)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)
# 화면 폴링·새로고침 주기(ms). 템플릿이 <body> data 속성으로 내려 주고 JS가 읽는다.
app.config.update(GIMPO_POLL_MS=1500, T2_POLL_MS=2000, SETTINGS_REFRESH_MS=3000)
web_security.init_app(app)
app.register_blueprint(t2_valet.bp)
app.register_blueprint(gimpo_parking.bp)
app.extensions["gimpo"] = gimpo_parking.GimpoService()


@app.route("/")
def landing():
    return render_template("landing.html")


@app.get("/settings/")
def settings():
    # 설정은 모든 화면의 레이어 팝업이다. 옛 주소는 랜딩에서 팝업을 연다.
    return redirect(url_for("landing", settings=1))


def warn_legacy_env(env_path):
    keys = find_legacy_env_keys(env_path, t2_valet.LEGACY_ENV_KEYS)
    if keys:
        print(f"[경고] .env의 {', '.join(keys)}은 더 이상 읽지 않습니다. config/t2_valet.toml로 옮기세요.")


if __name__ == "__main__":
    warn_legacy_env(os.path.join(BASE_DIR, ".env"))
    with app.app_context():
        app.extensions["gimpo"].start()
    import signal
    def shutdown(signum, frame):
        app.extensions["gimpo"].close()
        t2_valet.NOTIFICATIONS.close()
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, shutdown)
    try:
        app.run(debug=False, use_reloader=False, port=9000)
    finally:
        app.extensions["gimpo"].close()
        t2_valet.NOTIFICATIONS.close()
