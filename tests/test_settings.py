import threading
from dataclasses import replace

import pytest
from playwright.sync_api import sync_playwright

from app import app
from services.t2 import valet as t2_valet
from services.notifications.background import BackgroundNotifications
from services.gimpo.config import CONFIG
from services.gimpo.parking import GimpoService
from services.notifications.config import TelegramSettings
from services.notifications.messages import ReservationMessages
from services.notifications.telegram import Delivery
from tests.conftest import open_page, run_app_server
from tests.test_gimpo_jobs import eventually
from tests.test_notifications import TransportNotifier

ENDPOINTS = ('/t2-valet/api/notifications', '/gimpo-parking/api/notifications')


@pytest.fixture
def settings_services(client, tmp_path, monkeypatch):
    senders = []

    def configure(enabled=False, credentials=True, results=None):
        def no_runtime(*args, **kwargs):
            pytest.fail('Opening settings or testing an alarm must not start reservations')

        service = GimpoService(replace(CONFIG, directory=tmp_path / 'unused'), no_runtime)
        notifiers = [TransportNotifier(enabled, results) for _ in ENDPOINTS]
        for notifier in notifiers:
            notifier.settings = TelegramSettings(enabled, 'PRIVATE_TOKEN' if credentials else '', 'PRIVATE_CHAT')
        managers = [BackgroundNotifications(notifier) for notifier in notifiers]
        senders.extend(managers)
        monkeypatch.setattr(t2_valet, 'NOTIFICATIONS', managers[0])
        service.notifications = managers[1]
        monkeypatch.setitem(app.extensions, 'gimpo', service)
        return service, managers, notifiers

    yield configure
    for sender in senders:
        sender.close()


@pytest.mark.parametrize('enabled', [False, True])
def test_settings_reads_never_start_workers_or_expose_credentials(client, settings_services, enabled):
    service, senders, notifiers = settings_services(enabled)
    landing = client.get('/')
    assert landing.status_code == 200 and 'PRIVATE_' not in landing.get_data(as_text=True)
    for path in (prefix + '/status' for prefix in ENDPOINTS):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers['Cache-Control'] == 'no-store'
        assert 'PRIVATE_' not in response.get_data(as_text=True)
    for prefix in ENDPOINTS:
        data = client.get(prefix + '/status').json
        assert data['enabled'] is enabled
        assert data['credentialsConfigured'] is True
        assert data['configured'] is enabled
    assert service._runtime is None and not service.config.directory.exists()
    assert all(sender.thread is None for sender in senders)
    assert all(not notifier.calls for notifier in notifiers)


@pytest.mark.parametrize('enabled,credentials,expected', [(False, True, 'DISABLED'), (True, False, 'FAILED')])
def test_unavailable_alarm_never_sends(client, settings_services, enabled, credentials, expected):
    service, senders, notifiers = settings_services(enabled, credentials)
    for prefix in ENDPOINTS:
        response = client.post(prefix + '/test', json={})
        assert response.status_code == 202 and response.json['status'] == expected
    assert all(not notifier.calls for notifier in notifiers)
    assert all(sender.thread is None for sender in senders)
    assert service._runtime is None


@pytest.mark.parametrize('result', ['SENT', 'FAILED', 'UNKNOWN'])
def test_each_service_reports_test_result_without_reservation_runtime(client, settings_services, result):
    service, _, notifiers = settings_services(True, results=[Delivery(result)])
    for prefix in ENDPOINTS:
        assert client.post(prefix + '/test', json={}).status_code == 202
        eventually(lambda: client.get(prefix + '/status').json['lastTest']['status'] == result)
        data = client.get(prefix + '/status').json
        assert data['testPending'] is False and data['lastDelivery'] is None
    assert all(len(notifier.calls) == 1 for notifier in notifiers)
    assert '인천공항 T2 발렛' in notifiers[0].calls[0]
    assert '김포공항 국내선 주차' in notifiers[1].calls[0]
    assert service._runtime is None


def test_test_alarm_rejects_foreign_origin_and_non_json(client, settings_services):
    _, _, notifiers = settings_services(True)
    for prefix in ENDPOINTS:
        assert client.post(prefix + '/test', json={}, headers={'Origin': 'https://elsewhere.invalid'}).status_code == 403
        assert client.post(prefix + '/test', data='{}').status_code == 400
    assert all(not notifier.calls for notifier in notifiers)


def test_pending_test_is_not_queued_twice():
    release = threading.Event()
    started = threading.Event()

    class SlowNotifier(TransportNotifier):
        def send(self, message):
            started.set()
            release.wait(5)
            return super().send(message)

    notifier = SlowNotifier()
    sender = BackgroundNotifications(notifier)
    try:
        message = ReservationMessages.test('테스트')
        first = sender.publish_test(message)
        assert started.wait(2)
        second = sender.publish_test(message)
        assert first['eventId'] == second['eventId']
        assert sender.status()['testPending'] is True
        release.set()
        eventually(lambda: sender.status()['lastTest']['status'] == 'SENT')
        assert len(notifier.calls) == 1
    finally:
        release.set()
        sender.close()


def test_gimpo_notification_settings_require_restart(monkeypatch, tmp_path):
    monkeypatch.setenv('TELEGRAM_ALARM_ENABLED', 'false')
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'PRIVATE_TOKEN')
    monkeypatch.setenv('TELEGRAM_CHAT_ID', 'PRIVATE_CHAT')
    service = GimpoService(replace(CONFIG, directory=tmp_path / 'unused'))
    try:
        monkeypatch.setenv('TELEGRAM_ALARM_ENABLED', 'true')
        assert service.notification_status()['enabled'] is False
        assert service.notification_status()['credentialsConfigured'] is True
    finally:
        service.close()


def test_settings_route_opens_dialog_on_landing(client):
    response = client.get('/settings/')
    assert response.status_code == 302
    assert response.headers['Location'] == '/?settings=1'


@pytest.mark.parametrize('path', ['/', '/t2-valet/', '/gimpo-parking/'])
def test_every_page_offers_settings_dialog(client, path):
    html = client.get(path).get_data(as_text=True)
    assert 'id="open-settings"' in html
    assert '<dialog class="settings-dialog" id="settings-dialog"' in html
    assert 'href="/settings/"' not in html


@pytest.fixture
def live_server(settings_services):
    # settings_services는 client(T2 격리)와 tmp_path 김포 서비스에 의존하므로, 이를 통해서만
    # live_server를 만들 수 있게 해 이 서버가 격리 없이 뜨는 일이 없게 한다.
    with run_app_server(app) as base:
        yield base


def wait_tests_enabled(page):
    page.wait_for_function("[...document.querySelectorAll('#settings-dialog [data-test]')].every(button => !button.disabled)")


def test_settings_dialog_states_and_mobile(settings_services, live_server):
    service, _, notifiers = settings_services(True)
    base = live_server
    with sync_playwright() as p:
        browser, page, errors = open_page(p, base)
        page.goto(base + '/')
        dialog = page.locator('#settings-dialog')
        assert not dialog.is_visible()
        page.click('#open-settings')
        wait_tests_enabled(page)
        assert page.url == base + '/'
        assert all(not notifier.calls for notifier in notifiers)
        assert page.locator('#telegram-state').inner_text() == '켜짐'
        page.locator('[data-service=t2] [data-test]').click()
        page.locator('[data-service=t2] [data-test-result]').filter(has_text='전송 완료').wait_for()
        assert len(notifiers[0].calls) == 1 and not notifiers[1].calls
        page.set_viewport_size({'width': 390, 'height': 844})
        page.get_by_text('봇·수신자 설정', exact=True).click()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert dialog.bounding_box()['width'] >= 389
        assert 'PRIVATE_' not in page.locator('body').inner_text()
        page.keyboard.press('Escape')
        assert not dialog.is_visible()
        assert page.evaluate('document.activeElement.id') == 'open-settings'
        polled = []
        page.on('request', lambda request: polled.append(request.url) if '/notifications/status' in request.url else None)
        page.wait_for_timeout(3500)
        assert polled == []

        for notifier in notifiers:
            notifier.settings = replace(notifier.settings, enabled=False)
        page.goto(base + '/?settings=1')
        page.wait_for_function("[...document.querySelectorAll('[data-connection]')].every(node => node.textContent === '설정 완료')")
        assert dialog.is_visible()
        assert page.url == base + '/'
        assert page.locator('#telegram-state').inner_text() == '꺼짐'
        assert page.locator('[data-service=t2] [data-test]').is_disabled()
        assert page.locator('[data-service=gimpo] [data-test]').is_disabled()

        for notifier in notifiers:
            notifier.settings = replace(notifier.settings, enabled=True, token='')
        page.goto(base + '/?settings=1')
        page.wait_for_function("[...document.querySelectorAll('[data-connection]')].every(node => node.textContent === '미설정')")
        assert page.locator('[data-service=t2] [data-test]').is_disabled()

        page.route('**/t2-valet/api/notifications/status', lambda route: route.fulfill(status=503, json={'error': 'unavailable'}))
        page.goto(base + '/?settings=1')
        page.locator('[data-service=t2] [data-connection]').filter(has_text='확인 불가').wait_for()
        assert page.locator('[data-service=t2] [data-test]').is_disabled()
        assert len(notifiers[0].calls) == 1 and not notifiers[1].calls
        assert not errors and service._runtime is None
        browser.close()


def test_telegram_state_is_written_once_per_refresh_wave(settings_services, live_server):
    settings_services(True)
    base = live_server
    with sync_playwright() as p:
        browser, page, errors = open_page(p, base)
        page.goto(base + '/')
        page.evaluate("""() => {
            window.telegramWrites = 0;
            new MutationObserver(() => { window.telegramWrites += 1; })
                .observe(document.getElementById('telegram-state'), {childList: true, attributes: true});
        }""")
        page.click('#open-settings')
        wait_tests_enabled(page)
        assert page.locator('#telegram-state').inner_text() == '켜짐'
        assert page.evaluate('window.telegramWrites') == 1
        # 상태가 바뀌지 않으면 두 번(3초 간격) 이상의 새로고침 주기 동안에도 다시 쓰지 않는다.
        page.wait_for_timeout(6500)
        assert page.evaluate('window.telegramWrites') == 1
        assert page.locator('#telegram-state').inner_text() == '켜짐'
        # 한 서비스의 상태가 실제로 바뀌면 집계도 바뀌어 정확히 한 번 더 쓴다.
        page.route('**/t2-valet/api/notifications/status', lambda route: route.fulfill(
            status=200, content_type='application/json',
            json={'enabled': False, 'configured': False, 'credentialsConfigured': True,
                  'testPending': False, 'lastTest': None, 'lastDelivery': None}))
        page.wait_for_function('() => window.telegramWrites === 2')
        assert page.locator('#telegram-state').inner_text() == '꺼짐'
        assert not errors
        browser.close()


def test_telegram_state_reflects_reporting_service_when_one_fails(settings_services, live_server):
    # t2 상태 조회가 계속 실패해도, 성공하는 김포 서비스의 값으로 집계가 나와야 한다("확인 중"에 갇히지 않는다).
    settings_services(True)
    base = live_server
    with sync_playwright() as p:
        browser, page, errors = open_page(p, base)
        page.route('**/t2-valet/api/notifications/status', lambda route: route.fulfill(status=503, json={'error': 'unavailable'}))
        page.goto(base + '/?settings=1')
        page.locator('[data-service=t2] [data-connection]').filter(has_text='확인 불가').wait_for()
        page.wait_for_function("!document.querySelector('[data-service=gimpo] [data-test]').disabled")
        assert page.locator('#telegram-state').inner_text() == '켜짐'
        assert not errors
        browser.close()


def test_send_test_failure_reenables_button_before_next_refresh(settings_services, live_server):
    settings_services(True)
    base = live_server
    with sync_playwright() as p:
        browser, page, errors = open_page(p, base)
        page.route('**/t2-valet/api/notifications/test', lambda route: route.fulfill(status=503, json={'error': 'unavailable'}))
        page.goto(base + '/?settings=1')
        wait_tests_enabled(page)
        page.locator('[data-service=t2] [data-test]').click()
        page.locator('[data-service=t2] [data-error]').filter(has_text='수신 여부').wait_for()
        assert not page.locator('[data-service=t2] [data-test]').is_disabled()
        assert not errors
        browser.close()


def test_closing_dialog_during_test_send_stops_status_requests(settings_services, live_server):
    settings_services(True)
    base = live_server
    with sync_playwright() as p:
        browser, page, errors = open_page(p, base)
        held = []
        page.route('**/t2-valet/api/notifications/test', lambda route: held.append(route))
        page.goto(base + '/?settings=1')
        wait_tests_enabled(page)
        page.locator('[data-service=t2] [data-test]').click()
        for _ in range(50):
            if held:
                break
            page.wait_for_timeout(50)
        assert held
        page.keyboard.press('Escape')
        polled = []
        page.on('request', lambda request: polled.append(request.url) if '/notifications/status' in request.url else None)
        held[0].continue_()
        page.wait_for_timeout(1500)
        assert polled == []
        assert not errors
        browser.close()
