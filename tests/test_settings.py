import threading
from dataclasses import replace

import pytest
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

from app import app
from services import t2_valet
from services.background_notifications import BackgroundNotifications
from services.gimpo_config import CONFIG
from services.gimpo_parking import GimpoService
from services.notification_config import TelegramSettings
from services.notification_messages import ReservationMessages
from services.telegram_notifier import Delivery
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
    for path in ('/settings/', *(prefix + '/status' for prefix in ENDPOINTS)):
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


@pytest.mark.parametrize('path,new_tab', [('/', False), ('/t2-valet/', True), ('/gimpo-parking/', True)])
def test_settings_navigation_preserves_reservation_inputs(client, path, new_tab):
    from html.parser import HTMLParser
    class Links(HTMLParser):
        settings = None
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == 'a' and attrs.get('href') == '/settings/':
                self.settings = attrs
    parser = Links()
    parser.feed(client.get(path).get_data(as_text=True))
    assert parser.settings is not None
    assert (parser.settings.get('target') == '_blank') is new_tab


def test_settings_browser_states_and_mobile(settings_services, tmp_path):
    service, _, notifiers = settings_services(True)
    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={'width':1280, 'height':1000})
            page.context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(base + '/settings/')
            page.wait_for_function("[...document.querySelectorAll('[data-test]')].every(button => !button.disabled)")
            assert all(not notifier.calls for notifier in notifiers)
            assert page.locator('#telegram-state').inner_text() == '켜짐'
            page.locator('[data-service=t2] [data-test]').click()
            page.locator('[data-service=t2] [data-test-result]').filter(has_text='전송 완료').wait_for()
            assert len(notifiers[0].calls) == 1 and not notifiers[1].calls
            page.screenshot(path=str(tmp_path / 'settings-desktop.png'), full_page=True)
            page.set_viewport_size({'width':390, 'height':844})
            page.get_by_text('봇·수신자 설정', exact=True).click()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert 'PRIVATE_' not in page.locator('body').inner_text()
            page.screenshot(path=str(tmp_path / 'settings-mobile.png'), full_page=True)
            for notifier in notifiers:
                notifier.settings = replace(notifier.settings, enabled=False)
            page.reload()
            page.wait_for_function("[...document.querySelectorAll('[data-connection]')].every(node => node.textContent === '설정 완료')")
            assert page.locator('#telegram-state').inner_text() == '꺼짐'
            assert page.locator('[data-service=t2] [data-test]').is_disabled()
            assert page.locator('[data-service=gimpo] [data-test]').is_disabled()
            for notifier in notifiers:
                notifier.settings = replace(notifier.settings, enabled=True, token='')
            page.reload()
            page.wait_for_function("[...document.querySelectorAll('[data-connection]')].every(node => node.textContent === '미설정')")
            assert page.locator('[data-service=t2] [data-test]').is_disabled()
            assert page.locator('[data-service=gimpo] [data-test]').is_disabled()
            page.route('**/t2-valet/api/notifications/status', lambda route: route.fulfill(status=503, json={'error':'unavailable'}))
            page.reload()
            page.locator('[data-service=t2] [data-connection]').filter(has_text='확인 불가').wait_for()
            assert page.locator('[data-service=t2] [data-test]').is_disabled()
            assert len(notifiers[0].calls) == 1 and not notifiers[1].calls
            assert not errors and service._runtime is None

            # Click the real navigation: opening settings must preserve unsaved inputs.
            page.route('**/gimpo-parking/api/jobs/active', lambda route: route.fulfill(json={'job': None, 'recent': None}))
            for path in ('/t2-valet/', '/gimpo-parking/'):
                page.goto(base + path)
                page.wait_for_load_state('networkidle')
                page.fill('#carNumber', '123가4567')
                page.fill('#phone', '01012345678')
                if path == '/gimpo-parking/':
                    page.fill('#reservationPassword', 'PrivatePass44')
                with page.expect_popup() as opened:
                    page.locator('.header-settings').click()
                settings_page = opened.value
                settings_page.wait_for_load_state('networkidle')
                assert settings_page.url == base + '/settings/'
                assert settings_page.evaluate('window.opener === null')
                settings_page.close()
                assert page.url == base + path
                assert page.input_value('#carNumber') == '123가4567'
                assert page.input_value('#phone') == '01012345678'
                if path == '/gimpo-parking/':
                    assert page.input_value('#reservationPassword') == 'PrivatePass44'
                for width in (320, 390, 1280):
                    page.set_viewport_size({'width': width, 'height': 900})
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (path, width)
                page.screenshot(path=str(tmp_path / (path.strip('/') + '-navigation.png')), full_page=True)
            page.goto(base + '/')
            page.locator('.header-settings').click()
            page.wait_for_url(base + '/settings/')
            assert len(page.context.pages) == 1
            assert not errors and service._runtime is None
            browser.close()
    finally:
        server.shutdown()
        thread.join()
