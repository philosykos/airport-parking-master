import dataclasses

import pytest

from app import app as flask_app
from services import t2_valet
from services.t2_scheduler import Scheduler

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
    monkeypatch.setattr(t2_valet, "scheduler", Scheduler())
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c
    # 워커가 끝나기 전에 monkeypatch가 풀리면 실제 logs/에 쓸 수 있으므로 기다린다
    t2_valet.scheduler.stop()
    if t2_valet.scheduler.thread is not None:
        t2_valet.scheduler.thread.join(timeout=2)
