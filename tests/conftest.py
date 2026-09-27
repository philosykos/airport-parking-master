import dataclasses
import threading
from contextlib import contextmanager
from dataclasses import replace

import pytest
from werkzeug.serving import make_server

from app import app as flask_app
from services.gimpo.config import CONFIG
from services.gimpo.parking import GimpoService
from services.t2 import valet as t2_valet
from services.t2.scheduler import Scheduler
from services.t2.storage import LogStore, UserDataStore

TEST_URL = "https://example.invalid/reserve"


def _block_network(*args, **kwargs):
    raise RuntimeError("테스트에서 외부 호출 차단")


@pytest.fixture
def client(tmp_path, monkeypatch):
    # 설정 파일의 실제 URL 대신 테스트용 URL을 쓰고, 외부 호출 자체도 막는다
    monkeypatch.setattr(t2_valet, "CONFIG", dataclasses.replace(t2_valet.CONFIG, url=TEST_URL))
    monkeypatch.setattr(t2_valet.http_requests, "post", _block_network)
    monkeypatch.setattr(t2_valet, "log_store", LogStore(str(tmp_path / "api_call.log")))
    monkeypatch.setattr(t2_valet, "user_store", UserDataStore(str(tmp_path / "user_data.json")))
    monkeypatch.setattr(t2_valet, "scheduler", Scheduler())
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c
    # 워커가 끝나기 전에 monkeypatch가 풀리면 실제 logs/에 쓸 수 있으므로 기다린다
    t2_valet.scheduler.stop()
    if t2_valet.scheduler.thread is not None:
        t2_valet.scheduler.thread.join(timeout=2)


@pytest.fixture(autouse=True)
def no_external_http(monkeypatch, tmp_path):
    # Global default: every Python HTTP transport is blocked, not only the T2 client.
    import requests
    from services.notifications import config as notification_config
    from services.notifications.background import BackgroundNotifications
    from services.notifications.config import TelegramSettings
    from services.notifications.telegram import TelegramNotifier
    monkeypatch.setenv("TELEGRAM_ALARM_ENABLED", "false")
    monkeypatch.setattr(notification_config, 'ENV_PATH', tmp_path / '.env')
    monkeypatch.delenv('TELEGRAM_BOT_TOKEN', raising=False)
    monkeypatch.delenv('TELEGRAM_CHAT_ID', raising=False)
    notifications = BackgroundNotifications(TelegramNotifier(TelegramSettings()))
    monkeypatch.setattr(t2_valet, "NOTIFICATIONS", notifications)
    monkeypatch.setattr(requests.sessions.Session, "request", _block_network)
    yield
    notifications.close()


@contextmanager
def run_app_server(app=flask_app):
    # 화면 테스트가 되풀이해 쓰던 서버 기동·종료 절차(임의 포트, 스레드, shutdown+join)를 한 곳에 모은다.
    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        thread.join()


def open_page(p, base, width=1280, height=1000, init_script=None):
    # 화면 테스트가 되풀이해 쓰던 브라우저 기동 절차(뷰포트, 외부 요청 차단, pageerror 수집)를 한 곳에 모은다.
    browser = p.chromium.launch()
    page = browser.new_page(viewport={'width': width, 'height': height})
    page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
    if init_script:
        page.add_init_script(init_script)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    return browser, page, errors


@pytest.fixture
def t2_server(client, tmp_path, monkeypatch):
    # client 픽스처가 T2 저장소·스케줄러·외부 호출을 tmp_path와 가짜로 바꿔 둔다.
    # 설정 팝업이 김포 알림 상태도 읽으므로 김포 서비스도 임시 경로로 바꾼다.
    service = GimpoService(replace(CONFIG, directory=tmp_path / 'gimpo'))
    monkeypatch.setitem(flask_app.extensions, 'gimpo', service)
    with run_app_server(flask_app) as base:
        yield base
    service.close()
