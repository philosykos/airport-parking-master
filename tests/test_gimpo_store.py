from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from services.gimpo_store import Conflict, JobStore, READY
from services.gimpo_validation import validate
from tests.test_gimpo_validation import NOW, valid_input


@pytest.fixture
def store(tmp_path):
    result = JobStore(tmp_path / 'jobs.sqlite3', clock=lambda: 1000)
    yield result
    result.close()


def ready_job(store):
    job = store.create(validate(valid_input(), now=NOW), 'run1')
    store.transition(job['id'], 'RECHECKING', 'test')
    return store.ready(job['id'], 1, {'parkingName':'fixture', 'entryAt':'date', 'exitAt':'date', 'calculateAmt':8000, 'depositAmt':10000}, 1000, 120)


def test_only_one_job_and_payment_attempt(store):
    job = ready_job(store)
    with pytest.raises(Conflict): store.create(validate(valid_input(), now=NOW), 'run1')
    store.dispatch_payment(job['id'], job)
    with pytest.raises(Conflict): store.dispatch_payment(job['id'], job)
    assert store.get(job['id'])['paymentMayHaveBeenSent']
    assert store.events()[0]['status'] == 'CANCELLED'


def test_stop_races_payment_under_one_transaction(store):
    job = ready_job(store)
    barrier = Barrier(2)
    def race(payment):
        barrier.wait()
        try:
            if payment: store.dispatch_payment(job['id'], job)
            else: store.command(job['id'], job, {READY}, 'STOPPING', 'stop')
            return True
        except Conflict: return False
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(race, [True, False]))
    assert sum(results) == 1
    assert store.get(job['id'])['state'] in {'STOPPING', 'PAYMENT_DISPATCHING'}


def test_atomic_ready_and_outbox_rollback(store, monkeypatch):
    job = store.create(validate(valid_input(), now=NOW), 'run1')
    store.transition(job['id'], 'RECHECKING', 'test')
    monkeypatch.setattr(store, '_event', lambda *args: (_ for _ in ()).throw(RuntimeError('disk failure')))
    with pytest.raises(RuntimeError): store.ready(job['id'], 1, {}, 1000, 120)
    assert store.get(job['id'])['state'] == 'RECHECKING'
    assert store.events() == []


def test_payment_record_failure_prevents_send(store, monkeypatch):
    job = ready_job(store)
    monkeypatch.setattr(store, '_save', lambda *args: (_ for _ in ()).throw(RuntimeError('disk failure')))
    with pytest.raises(RuntimeError): store.dispatch_payment(job['id'], job)
    assert not store.get(job['id'])['paymentMayHaveBeenSent']


def test_recovery_sending_unknown_correction_idempotence(store):
    job = ready_job(store)
    event = store.claim_event(store.events()[0]['id'])
    assert event['status'] == 'SENDING'
    store.recover('run2')
    assert store.get(job['id'])['state'] == 'INTERRUPTED'
    assert store.active() is None
    events = store.events()
    assert [e['status'] for e in events] == ['UNKNOWN', 'PENDING']
    assert events[1]['kind'] == 'CORRECTION'
    store.recover('run3')
    assert store.events() == events


def test_recovery_retains_payment_unknown_slot(store):
    job = ready_job(store)
    store.dispatch_payment(job['id'], job)
    store.recover('run2')
    assert store.active()['state'] == 'PAYMENT_RESULT_UNKNOWN'
    with pytest.raises(Conflict): store.restart(job['id'], job, validate(valid_input(), now=NOW), 'run2')


def test_stale_generation_and_deadline(store):
    job = ready_job(store)
    with pytest.raises(Conflict): store.dispatch_payment(job['id'], {**job, 'generation':0})
    store.clock = lambda:1120
    with pytest.raises(Conflict): store.dispatch_payment(job['id'], job)


def test_no_sensitive_data_persisted(store):
    job = ready_job(store)
    text = '\n'.join(store.db.iterdump())
    for secret in ['PrivatePass44','01012345678','123가4567','reservationPassword','passwordConfirmation']:
        assert secret not in text


def test_repeated_unknown_recovery_does_not_duplicate_correction(store):
    job=ready_job(store)
    event=store.events()[0]
    store.claim_event(event['id'])
    store.finish_event(event['id'],'SENT',message_id=123)
    store.dispatch_payment(job['id'],job)
    store.recover('run2')
    before=store.events()
    version=store.active()['stateVersion']
    store.recover('run3');store.recover('run4')
    assert store.events()==before
    assert store.active()['stateVersion']==version
