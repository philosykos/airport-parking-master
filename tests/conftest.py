import dataclasses

import pytest

from app import app as flask_app
from services import t2_valet

TEST_URL = "https://example.invalid/reserve"


def _block_network(*args, **kwargs):
    raise RuntimeError("테스트에서 외부 호출 차단")


@pytest.fixture
def client(tmp_path, monkeypatch):
    # 설정 파일의 실제 URL 대신 테스트용 URL을 쓰고, 외부 호출 자체도 막는다
    monkeypatch.setattr(t2_valet, "CONFIG", dataclasses.replace(t2_valet.CONFIG, url=TEST_URL))
    monkeypatch.setattr(t2_valet.http_requests, "post", _block_network)
    monkeypatch.setattr(t2_valet, "LOG_FILE", str(tmp_path / "api_call.log"))
    monkeypatch.setattr(t2_valet, "USER_DATA_FILE", str(tmp_path / "user_data.json"))
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c
    t2_valet.stop_event.set()
    t2_valet.is_running = False
