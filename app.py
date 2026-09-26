import os
import sys

from flask import Flask, render_template

from services.config import ConfigError, find_legacy_env_keys

try:
    from services import t2_valet
except ConfigError as e:
    print(f"[설정 오류] {e}")
    sys.exit(1)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)
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
    app.run(debug=True, port=8080)
