import os
import sys

from flask import Flask, render_template

from services.config import ConfigError, find_legacy_env_keys
from services import web_security

try:
    from services import t2_valet
except ConfigError as e:
    print(f"[설정 오류] {e}")
    sys.exit(1)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)
web_security.init_app(app)
app.register_blueprint(t2_valet.bp)


@app.route("/")
def landing():
    return render_template("landing.html")


def warn_legacy_env(env_path):
    keys = find_legacy_env_keys(env_path, t2_valet.LEGACY_ENV_KEYS)
    if keys:
        print(f"[경고] .env의 {', '.join(keys)}은 더 이상 읽지 않습니다. config/t2_valet.toml로 옮기세요.")


if __name__ == "__main__":
    warn_legacy_env(os.path.join(BASE_DIR, ".env"))
    # 디버그 모드는 브라우저에서 코드를 실행하는 Werkzeug 디버거를 켜므로 끈다
    app.run(debug=False, port=8080)
