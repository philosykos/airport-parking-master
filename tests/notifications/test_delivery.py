import functools
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from services.t2 import valet as t2_valet
from services.notifications.background import BackgroundNotifications
from services.config import ConfigError
from services.notifications.config import NotificationConfig, TelegramSettings
from services.notifications.messages import ReservationMessages
from services.notifications.telegram import Delivery, TelegramNotifier
from tests.support.waiting import eventually
from tests.notifications.fakes import TransportNotifier


def test_global_flag_defaults_false_and_controls_both_services(monkeypatch):
    monkeypatch.delenv('TELEGRAM_ALARM_ENABLED', raising=False)
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN','secret-token')
    monkeypatch.setenv('TELEGRAM_CHAT_ID','secret-chat')
    settings=TelegramSettings.from_environment()
    assert not settings.enabled and not settings.configured
    assert settings.credentials_configured
    assert 'secret' not in repr(settings)
    monkeypatch.setenv('TELEGRAM_ALARM_ENABLED','true')
    assert TelegramSettings.from_environment().configured
    monkeypatch.setenv('TELEGRAM_ALARM_ENABLED','false')
    assert not TelegramSettings.from_environment().enabled


@pytest.mark.parametrize('value',['','0','yes','typo','false'])
def test_unrecognized_flags_never_enable(value,monkeypatch):
    monkeypatch.setenv('TELEGRAM_ALARM_ENABLED',value)
    assert not TelegramSettings.from_environment().enabled


def test_service_credentials_are_ignored(monkeypatch):
    monkeypatch.setenv('TELEGRAM_ALARM_ENABLED', 'true')
    for prefix in ('ICN_VALET', 'GMP_PARK'):
        monkeypatch.setenv(prefix + '_TELEGRAM_BOT_TOKEN', 'old-token')
        monkeypatch.setenv(prefix + '_TELEGRAM_CHAT_ID', 'old-chat')
    assert not TelegramSettings.from_environment().configured
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'common-token')
    monkeypatch.setenv('TELEGRAM_CHAT_ID', 'common-chat')
    assert TelegramSettings.from_environment() == TelegramSettings(True, 'common-token', 'common-chat')


def test_dotenv_common_settings_with_environment_precedence(monkeypatch):
    import os
    from services.notifications import config as notification_config
    monkeypatch.delenv('TELEGRAM_ALARM_ENABLED')
    notification_config.ENV_PATH.write_text(
        'export TELEGRAM_ALARM_ENABLED=true\nTELEGRAM_BOT_TOKEN="file-token"\n'
        "TELEGRAM_CHAT_ID='-1001234567890' # channel\nREQUEST_URL=ignored\n", encoding='utf-8')
    before = dict(os.environ)
    assert TelegramSettings.from_environment() == TelegramSettings(True, 'file-token', '-1001234567890')
    assert dict(os.environ) == before
    monkeypatch.setenv('TELEGRAM_ALARM_ENABLED', 'false')
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', '')
    assert TelegramSettings.from_environment() == TelegramSettings(False, '', '-1001234567890')


def test_unreadable_dotenv_keeps_default_off(monkeypatch):
    from services.notifications import config as notification_config
    monkeypatch.delenv('TELEGRAM_ALARM_ENABLED')
    notification_config.ENV_PATH.mkdir()
    assert TelegramSettings.from_environment() == TelegramSettings()


def test_disabled_transport_never_calls_network():
    class Never:
        def post(self,*args,**kwargs): pytest.fail('disabled notification reached network')
    assert TelegramNotifier(TelegramSettings(False,'token','chat'),Never()).send('hello').status=='DISABLED'


def test_shared_config_validation():
    assert NotificationConfig.parse({'telegram':{'max_attempts':3}}).max_attempts==3
    for value in [True,0,4,'3']:
        with pytest.raises(ConfigError): NotificationConfig.parse({'telegram':{'max_attempts':value}})
    with pytest.raises(ConfigError): NotificationConfig.parse({'telegram':{'max_attempts':3,'enabled':True}})


def test_common_t2_message_is_allowlisted():
    payload={'departingAt':'2026-10-03 11:00:00','arrivedAt':'2026-10-06 18:00:00',
             'name':'PRIVATE_NAME','phone':'PRIVATE_PHONE','carNumber':'PRIVATE_CAR','token':'PRIVATE_TOKEN','password':'PRIVATE_PASSWORD'}
    message=ReservationMessages.t2_completed(payload,'ICN-T2-fixture',datetime(2026,10,1,9,tzinfo=ZoneInfo('Asia/Seoul'))).render()
    assert '[인천공항 T2 발렛] 예약 완료' in message
    assert '입차: 2026-10-03 11:00' in message
    assert '출차: 2026-10-06 18:00' in message
    assert 'PRIVATE' not in message
    assert '작업: ICN-T2-fixture' in message


def test_background_sender_dedupes_without_rebooking():
    notifier=TransportNotifier()
    sender=BackgroundNotifications(notifier)
    try:
        message=ReservationMessages.test('T2')
        sender.publish('same',message);sender.publish('same',message)
        eventually(lambda:sender.status()['lastDelivery']['status']=='SENT')
        sender.publish('same',message)
        assert len(notifier.calls)==1
    finally: sender.close()


def test_timeout_unknown_not_retried():
    notifier=TransportNotifier(results=[Delivery('UNKNOWN',error='timeout')])
    sender=BackgroundNotifications(notifier)
    try:
        sender.publish('one',ReservationMessages.test('T2'))
        eventually(lambda:sender.status()['lastDelivery']['status']=='UNKNOWN')
        assert len(notifier.calls)==1
    finally: sender.close()


def test_disabled_queue_does_not_start_worker():
    notifier=TransportNotifier(enabled=False)
    sender=BackgroundNotifications(notifier)
    for i in range(110): sender.publish(str(i),ReservationMessages.test('T2'))
    assert sender.thread is None and notifier.calls==[]
    assert len(sender.results)==100


def test_t2_scheduler_publishes_completion_once(client,monkeypatch):
    notifier=TransportNotifier()
    sender=BackgroundNotifications(notifier)
    monkeypatch.setattr(t2_valet,'NOTIFICATIONS',sender)
    monkeypatch.setattr(t2_valet,'do_single_call',lambda url,payload:{'time':'t','status':200,'body':'ok'})
    payload={'departingAt':'2026-10-03 11:00','arrivedAt':'2026-10-06 18:00'}
    try:
        assert t2_valet.scheduler.start(functools.partial(t2_valet.poll_once,'https://example.invalid',payload,'ICN-T2-test'),30)
        t2_valet.scheduler.thread.join(timeout=2)
        assert not t2_valet.scheduler.running
        eventually(lambda:len(notifier.calls)==1)
        assert '예약 완료' in notifier.calls[0]
    finally: sender.close()


def test_t2_single_call_success_also_notifies(client,monkeypatch):
    notifier=TransportNotifier()
    sender=BackgroundNotifications(notifier)
    monkeypatch.setattr(t2_valet,'NOTIFICATIONS',sender)
    monkeypatch.setattr(t2_valet,'do_single_call',lambda url,payload:{'time':'t','status':200,'body':'ok'})
    try:
        assert client.post('/t2-valet/api/test',json={'name':'name','phone':'01012345678'}).status_code==200
        eventually(lambda:len(notifier.calls)==1)
    finally: sender.close()


def test_notification_failure_does_not_change_t2_success(client,monkeypatch):
    class Broken:
        def publish(self,*args): raise RuntimeError('secret-token')
    monkeypatch.setattr(t2_valet,'NOTIFICATIONS',Broken())
    monkeypatch.setattr(t2_valet,'do_single_call',lambda url,payload:{'time':'t','status':200,'body':'ok'})
    response=client.post('/t2-valet/api/test',json={'name':'name','phone':'01012345678'})
    assert response.status_code==200
    logs=client.get('/t2-valet/api/logs').get_data(as_text=True)
    assert 'secret-token' not in logs


def test_t2_non_success_has_no_completion_notification(client,monkeypatch):
    notifier=TransportNotifier()
    sender=BackgroundNotifications(notifier)
    monkeypatch.setattr(t2_valet,'NOTIFICATIONS',sender)
    monkeypatch.setattr(t2_valet,'do_single_call',lambda url,payload:{'time':'t','status':400,'body':'full'})
    client.post('/t2-valet/api/test',json={'name':'name','phone':'01012345678'})
    assert notifier.calls==[] and sender.thread is None


def test_t2_notification_status_and_disabled_test(client):
    assert client.get('/t2-valet/api/notifications/status').json['enabled'] is False
    assert client.post('/t2-valet/api/notifications/test',json={}).json['status']=='DISABLED'
