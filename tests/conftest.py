import os

# REQUEST_URL 검사는 import 시점에 돌므로 app import 전에 넣는다.
# load_dotenv()는 기존 환경변수를 덮어쓰지 않는다.
os.environ.setdefault("REQUEST_URL", "https://example.invalid/reserve")

import pytest

from app import app as flask_app
from services import t2_valet


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(t2_valet, "LOG_FILE", str(tmp_path / "api_call.log"))
    monkeypatch.setattr(t2_valet, "USER_DATA_FILE", str(tmp_path / "user_data.json"))
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c
    t2_valet.stop_event.set()
    t2_valet.is_running = False
