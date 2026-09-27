import threading
from dataclasses import replace

import pytest

from app import app
from services.t2 import valet as t2_valet
from services.notifications.background import BackgroundNotifications
from services.gimpo.config import CONFIG
from services.gimpo.parking import GimpoService
from services.notifications.config import TelegramSettings
from services.notifications.messages import ReservationMessages
from services.notifications.telegram import Delivery
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


@pytest.mark.parametrize('path', ['/'])
def test_every_page_offers_settings_dialog(client, path):
    html = client.get(path).get_data(as_text=True)
    assert 'id="open-settings"' in html
    assert '<dialog class="settings-dialog" id="settings-dialog"' in html
    assert 'href="/settings/"' not in html
