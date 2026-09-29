import pytest
import requests

from services.gimpo.store import CORRECTION_CAUSES, JobStore, READY
from services.gimpo.validation import validate
from services.notifications.outbox import NotificationOutbox
from services.notifications.telegram import Delivery, TelegramNotifier
from services.notifications.config import TelegramSettings
from tests.gimpo.fakes import FakeNotifier
from tests.gimpo.helpers import NOW, ready_job, valid_input


@pytest.fixture
def store(tmp_path):
    store = JobStore(tmp_path / 'db', clock=lambda:1000)
    yield store
    store.close()


@pytest.mark.parametrize('state',['HANDOFF_CANCELLED','HANDOFF_EXPIRED','PAYMENT_DISPATCHING','STOPPING','SESSION_EXPIRED'])
def test_retry_cancels_old_ready(store, state):
    job = ready_job(store)
    notifier = FakeNotifier([Delivery('RETRYING',retry_after=30)])
    outbox = NotificationOutbox(store,notifier,lambda e:True)
    outbox.deliver_one()
    store.transition(job['id'],state,'changed')
    store.clock=lambda:1100
    outbox.deliver_one()
    assert len(notifier.sent) == 1
    assert store.events()[0]['status'] == 'CANCELLED'


def test_timeout_unknown_manual_resend_deduplicates(store):
    job = ready_job(store)
    notifier = FakeNotifier([Delivery('UNKNOWN',error='timeout')])
    outbox = NotificationOutbox(store,notifier,lambda e:True)
    outbox.deliver_one();outbox.deliver_one()
    assert len(notifier.sent)==1
    event = store.events()[0]
    store.resend(job['id'],job,event['id'],event['round'])
    from services.gimpo.store import Conflict
    with pytest.raises(Conflict): store.resend(job['id'],job,event['id'],event['round'])
    outbox.deliver_one()
    assert len(notifier.sent)==2


def sent_ready(store):
    job = ready_job(store)
    event = store.claim_event(store.events()[0]['id'])
    store.finish_event(event['id'], 'SENT', message_id=7)
    return job


@pytest.mark.parametrize('state,cause', [('HANDOFF_EXPIRED', 'EXPIRED'), ('SESSION_EXPIRED', 'INTERRUPTED'),
    ('ERROR', 'INTERRUPTED'), ('REVIEW_REQUIRED', 'INTERRUPTED'), ('INTERRUPTED', 'INTERRUPTED'),
    ('PAYMENT_DISPATCHING', None), ('HANDOFF_CANCELLED', None), ('STOPPING', None)])
def test_correction_follows_cause_table(store, state, cause):
    job = sent_ready(store)
    if state == 'PAYMENT_DISPATCHING':
        store.dispatch_payment(job['id'], job)
    else:
        store.transition(job['id'], state, 'changed')
    corrections = [e for e in store.events() if e['kind'] == 'CORRECTION']
    assert CORRECTION_CAUSES.get(state) == cause
    assert [(e['cause'], e['replyTo']) for e in corrections] == ([(cause, 7)] if cause else [])


def test_correction_once_per_ready_even_after_more_changes(store):
    job = sent_ready(store)
    store.transition(job['id'], 'HANDOFF_EXPIRED', 'expired')
    store.release(job['id'], 'HANDOFF_EXPIRED', 'expired', {'HANDOFF_EXPIRED'})
    corrections = [e for e in store.events() if e['kind'] == 'CORRECTION']
    assert len(corrections) == 1 and corrections[0]['status'] == 'PENDING'
    notifier = FakeNotifier()
    NotificationOutbox(store, notifier, lambda e: True).deliver_one()
    assert notifier.sent[0][0].startswith('[김포공항 국내선 주차] 결제 대기 시간 초과')


def test_correction_only_for_current_handoff(store):
    job = sent_ready(store)
    store.transition(job['id'], 'HANDOFF_CANCELLED', 'cancelled')
    store.release(job['id'], 'HANDOFF_CANCELLED', 'cancelled', {'HANDOFF_CANCELLED'})
    job = store.restart(job['id'], store.get(job['id']), validate(valid_input(), now=NOW), 'run1')
    store.transition(job['id'], 'RECHECKING', 'test')
    job = store.ready(job['id'], job['generation'], job['summary'] or {'parkingName': 'p', 'entryAt': 'a', 'exitAt': 'b',
                      'calculateAmt': 1, 'depositAmt': 1}, 1000, 120)
    second = [e for e in store.events() if e['kind'] == 'READY' and e['generation'] == 2][0]
    store.finish_event(store.claim_event(second['id'])['id'], 'SENT', message_id=8)
    store.transition(job['id'], 'HANDOFF_EXPIRED', 'expired')
    assert [e['parentId'] for e in store.events() if e['kind'] == 'CORRECTION'] == [second['id']]


@pytest.mark.parametrize('state,expected', [('HANDOFF_CANCELLED', 1), ('HANDOFF_EXPIRED', 2)])
def test_state_change_while_sending_uses_cause(store, state, expected):
    job = ready_job(store)
    class DuringSend(FakeNotifier):
        def send(self, text, reply_to=None):
            if not self.sent: store.transition(job['id'], state, 'changed')
            return super().send(text, reply_to)
    notifier = DuringSend()
    outbox = NotificationOutbox(store, notifier, lambda e: True)
    outbox.deliver_one(); outbox.deliver_one(); outbox.deliver_one()
    assert len(notifier.sent) == expected
    if expected == 2:
        assert notifier.sent[1][1] == 1 and '결제 대기 시간 초과' in notifier.sent[1][0]


def test_sent_restart_correction_without_browser(store):
    ready_job(store)
    notifier=FakeNotifier()
    outbox=NotificationOutbox(store,notifier,lambda e:True)
    outbox.deliver_one();store.recover('new-run')
    outbox.validate_ready=lambda e:False
    outbox.deliver_one();store.recover('third-run');outbox.deliver_one()
    assert len(notifier.sent)==2
    assert '결제 대기 중단' in notifier.sent[1][0]


def test_retry_exhaustion_and_no_browser_probe_for_corrections(store):
    ready_job(store)
    notifier=FakeNotifier([Delivery('RETRYING',retry_after=1)]*3)
    outbox=NotificationOutbox(store,notifier,lambda e:True)
    for now in [1000,1010,1030]:
        store.clock=lambda:now
        outbox.deliver_one()
    assert store.events()[0]['status']=='FAILED'


class Transport:
    def __init__(self,status=200,data=None,error=None):
        self.status,self.data,self.error=status,data,error
        self.payload=None
    def post(self,url,**kwargs):
        self.payload=kwargs['json']
        if self.error: raise self.error
        class Response:
            status_code=self.status
            def json(inner): return self.data
        return Response()


@pytest.mark.parametrize('status,data,expected',[(200,{'ok':True,'result':{'message_id':4}},'SENT'),
    (429,{'error_code':429,'parameters':{'retry_after':50}},'RETRYING'),(401,{'error_code':401},'FAILED'),
    (500,{'error_code':500},'RETRYING'),(200,{},'FAILED')])
def test_telegram_response_classification(status,data,expected):
    transport=Transport(status,data)
    notifier=TelegramNotifier(TelegramSettings(True,'private-token','private-chat'),transport)
    result=notifier.send('message',3)
    assert result.status==expected
    assert transport.payload['reply_parameters']['allow_sending_without_reply'] is True
    assert 'private-token' not in str(result) and 'private-chat' not in str(result)


def test_telegram_timeout_does_not_leak_token():
    notifier=TelegramNotifier(TelegramSettings(True,'private-token','chat'),Transport(error=requests.Timeout('https://api.telegram.org/private-token')))
    result=notifier.send('message')
    assert result.status=='UNKNOWN' and 'private-token' not in str(result)


def test_environment_naming(monkeypatch):
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN','token')
    monkeypatch.setenv('TELEGRAM_CHAT_ID','chat')
    monkeypatch.setenv('TELEGRAM_ALARM_ENABLED','true')
    assert TelegramNotifier(TelegramSettings.from_environment()).configured


@pytest.mark.parametrize('initial', ['SENT', 'UNKNOWN'])
@pytest.mark.parametrize('resend_status', ['PENDING', 'RETRYING', 'FAILED', 'SENDING'])
def test_resend_preserves_history_for_correction(store, initial, resend_status):
    job = ready_job(store)
    message_id = 42 if initial == 'SENT' else None
    event = store.claim_event(store.events()[0]['id'])
    store.finish_event(event['id'], initial, message_id=message_id)
    store.resend(job['id'], job, event['id'], 1)
    if resend_status != 'PENDING':
        store.claim_event(event['id'])
        if resend_status != 'SENDING':
            store.finish_event(event['id'], resend_status)
    store.transition(job['id'], 'HANDOFF_EXPIRED', 'expired')
    if resend_status == 'SENDING':
        store.finish_event(event['id'], 'FAILED')
    corrections = [e for e in store.events() if e['kind'] == 'CORRECTION']
    assert len(corrections) == 1
    assert corrections[0]['replyTo'] == message_id
    notifier = FakeNotifier()
    outbox = NotificationOutbox(store, notifier, lambda e: False)
    outbox.deliver_one()
    assert len(notifier.sent) == 1
    assert '결제 대기 시간 초과' in notifier.sent[0][0]


def test_resend_history_survives_recovery(store):
    job = ready_job(store)
    event = store.claim_event(store.events()[0]['id'])
    store.finish_event(event['id'], 'SENT', message_id=42)
    store.resend(job['id'], job, event['id'], 1)
    store.claim_event(event['id'])
    store.finish_event(event['id'], 'FAILED')
    store.recover('new-run')
    corrections = [e for e in store.events() if e['kind'] == 'CORRECTION']
    assert len(corrections) == 1 and corrections[0]['replyTo'] == 42
    store.recover('another-run')
    assert len([e for e in store.events() if e['kind'] == 'CORRECTION']) == 1


def test_discounted_parking_price_in_notification():
    from services.notifications.messages import ReservationMessages
    job = {'id': 'GMP-test', 'availabilityCheckedAt': 1000,
           'summary': {'parkingName': '김포', 'entryAt': '2026-10-03 11:00',
                       'exitAt': '2026-10-06 18:00', 'calculateAmt': 104000,
                       'discountAmt': 52000, 'depositAmt': 10000}}
    message = ReservationMessages.gimpo({'kind': 'READY', 'round': 1}, job)
    assert ('예상 주차요금', '52,000원') in message.fields


def test_ready_message_notes_earlier_exit():
    from services.notifications.messages import ReservationMessages
    job = {'id': 'GMP-test', 'availabilityCheckedAt': 1000,
           'summary': {'parkingName': '김포', 'entryAt': '2026-10-03 11:00', 'exitAt': '2026-10-05 18:00',
                       'requestedExitAt': '2026-10-06 18:00', 'exitNote': '원하는 출차(10/06 18:00)보다 1일 이릅니다',
                       'calculateAmt': 80000, 'discountAmt': 0, 'depositAmt': 10000}}
    message = ReservationMessages.gimpo({'kind': 'READY', 'round': 1}, job)
    assert ('출차', '2026-10-05 18:00 — 원하는 출차(10/06 18:00)보다 1일 이릅니다') in message.fields
    job['summary'].update(exitAt='2026-10-06 18:00', exitNote=None)
    assert ('출차', '2026-10-06 18:00') in ReservationMessages.gimpo({'kind': 'READY', 'round': 1}, job).fields
