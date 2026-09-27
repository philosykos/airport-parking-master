import asyncio
import json
import time
from dataclasses import replace
from datetime import datetime

import pytest

from services.gimpo.config import CONFIG
from services.gimpo.jobs import GimpoRuntime, ProcessLease, RuntimeUnavailable
from services.gimpo.parking import GimpoService
from services.gimpo.store import Conflict, READY
from services.gimpo.validation import SEOUL, validate
from tests.gimpo_fakes import FakeBrowser, FakeNotifier
from tests.test_gimpo_validation import valid_input


def eventually(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value: return value
        time.sleep(.02)
    raise AssertionError('expected state not reached')


def wait_state(runtime, job_id, state):
    return eventually(lambda: job if (job := runtime.store.get(job_id))['state'] == state else None)


@pytest.fixture
def runtime(tmp_path):
    instance = GimpoRuntime(replace(CONFIG, directory=tmp_path / 'data'), FakeBrowser, FakeNotifier())
    yield instance
    instance.close()


def inputs(mode='watch'):
    return validate(valid_input(datetime.now(SEOUL), mode))


def ready(runtime):
    job = runtime.create(inputs())
    return wait_state(runtime, job['id'], READY)


def test_once_then_manual_prepare_and_proceed(runtime):
    job = runtime.create(inputs('once'))
    wait_state(runtime, job['id'], 'AVAILABLE')
    runtime.prepare(job['id'], job)
    wait_state(runtime, job['id'], 'PREPARED')
    runtime.proceed(job['id'], job, True)
    wait_state(runtime, job['id'], READY)
    assert runtime.clients[job['id']].proceeds == 1


def test_stop_cleans_secrets_then_restart_new_generation(runtime):
    job = ready(runtime)
    old = runtime.clients[job['id']]
    runtime.stop(job['id'], job)
    eventually(lambda: not runtime.store.get(job['id'])['active'])
    assert old.closed and job['id'] not in runtime.inputs
    updated = runtime.restart(job['id'], job, inputs())
    assert updated['generation'] == 2 and updated['inputVersion'] == 2
    wait_state(runtime, job['id'], READY)
    assert runtime.clients[job['id']] is not old


@pytest.mark.parametrize('state',['PAYMENT_DISPATCHING','PAYMENT_IN_PROGRESS','PAYMENT_RESULT_UNKNOWN'])
def test_payment_blocks_stop_edit_new_prepare(runtime, state):
    job = ready(runtime)
    runtime.store.dispatch_payment(job['id'], job)
    if state != 'PAYMENT_DISPATCHING': runtime.store.transition(job['id'], state, 'test')
    browser = runtime.clients[job['id']]
    for action in [lambda:runtime.stop(job['id'], job), lambda:runtime.stop(job['id'], job, inputs()),
                   lambda:runtime.create(inputs()), lambda:runtime.prepare(job['id'], job),
                   lambda:runtime.restart(job['id'], job, inputs()), lambda:runtime.proceed(job['id'], job, True)]:
        with pytest.raises(Conflict): action()
    assert not browser.closed
    runtime.resolve(job['id'], job, 'unknown', True)
    eventually(lambda:not runtime.store.get(job['id'])['active'])
    assert runtime.store.get(job['id'])['state'] == 'CLOSED_BY_USER'


def test_expiration_and_cancellation_release_slot(runtime):
    job = ready(runtime)
    runtime.store.clock = lambda:job['handoffDeadline']
    wait_state(runtime, job['id'], 'HANDOFF_EXPIRED')
    eventually(lambda:not runtime.store.get(job['id'])['active'])
    assert job['id'] not in runtime.clients


def test_modal_close_does_not_cancel_payment(runtime):
    job = ready(runtime)
    runtime.store.dispatch_payment(job['id'], job)
    runtime.clients[job['id']].live = False
    time.sleep(.4)
    assert runtime.store.get(job['id'])['state'] == 'PAYMENT_DISPATCHING'


def test_process_lease(runtime):
    with pytest.raises(RuntimeUnavailable): ProcessLease(runtime.config.directory)


def test_stop_during_inflight_check_cannot_create_ready(runtime):
    class Slow(FakeBrowser):
        async def check(self):
            await asyncio.sleep(30)
            return True
    runtime.client_factory = Slow
    job = runtime.create(inputs())
    runtime.stop(job['id'], job)
    wait_state(runtime, job['id'], 'STOPPED')
    assert runtime.store.events(job['id']) == []


def test_notifications_fail_without_closing_browser(runtime):
    runtime.outbox.notifier.configured = False
    from services.notifications.telegram import Delivery
    runtime.outbox.notifier.results = [Delivery('FAILED', error='configuration')]
    job = ready(runtime)
    eventually(lambda:any(e['status']=='FAILED' for e in runtime.store.events(job['id'])))
    assert not runtime.clients[job['id']].closed
    assert runtime.store.get(job['id'])['state'] == READY


@pytest.fixture
def gimpo_client(client, runtime, monkeypatch):
    from app import app
    service = GimpoService(runtime.config)
    service._runtime = runtime
    monkeypatch.setitem(app.extensions, 'gimpo', service)
    return client


def test_api_input_and_reconnect_without_duplicates(gimpo_client, runtime):
    c = gimpo_client
    assert c.get('/gimpo-parking/').status_code == 200
    assert c.post('/gimpo-parking/api/jobs', json={}).status_code == 400
    raw = valid_input(datetime.now(SEOUL), 'watch')
    response = c.post('/gimpo-parking/api/jobs', json=raw)
    assert response.status_code == 202
    job = response.json['job']; wait_state(runtime, job['id'], READY)
    for _ in range(2):
        assert c.get('/gimpo-parking/api/jobs/active').json['job']['id'] == job['id']
    assert len(runtime.store.events()) == 1
    status = c.get('/gimpo-parking/api/jobs/'+job['id']).get_data(as_text=True)
    assert 'PrivatePass44' not in status and '01012345678' not in status
    assert c.post(f'/gimpo-parking/api/jobs/{job["id"]}/stop',json={'generation':0,'inputVersion':1}).status_code == 409
    runtime.store.dispatch_payment(job['id'], job)
    assert c.patch(f'/gimpo-parking/api/jobs/{job["id"]}',json={**job,'inputs':raw}).status_code == 409


def test_defaults_explicit_consent_and_no_password(gimpo_client, runtime):
    raw = valid_input(datetime.now(SEOUL))
    assert gimpo_client.post('/gimpo-parking/api/defaults',json=raw).status_code == 400
    assert gimpo_client.post('/gimpo-parking/api/defaults',json={**raw,'saveConsent':True}).status_code == 200
    stored = gimpo_client.get('/gimpo-parking/api/defaults').json
    assert stored['carNumber'] == raw['carNumber']
    assert 'reservationPassword' not in stored and 'agreements' not in stored
    assert 'PrivatePass44' not in '\n'.join(runtime.store.db.iterdump())


def test_reloader_rejects_runtime(gimpo_client, monkeypatch):
    monkeypatch.setenv('WERKZEUG_RUN_MAIN','true')
    assert gimpo_client.get('/gimpo-parking/api/jobs/active').status_code == 503


def test_failed_cleanup_keeps_slot_until_retry(runtime):
    class CloseOnceFails(FakeBrowser):
        attempts=0
        async def close(self):
            self.attempts+=1
            if self.attempts==1: raise RuntimeError('private browser detail')
            await super().close()
    runtime.client_factory=CloseOnceFails
    job=ready(runtime)
    runtime.stop(job['id'],job)
    wait_state(runtime,job['id'],'ERROR')
    assert runtime.store.active()['id']==job['id']
    with pytest.raises(Conflict): runtime.restart(job['id'],job,inputs())
    assert 'private browser detail' not in str(runtime.store.get(job['id']))
    runtime.stop(job['id'],job)
    eventually(lambda:runtime.store.active() is None)


def test_restart_cannot_overtake_cleanup(runtime):
    job=ready(runtime)
    runtime.store.transition(job['id'],'HANDOFF_CANCELLED','closing')
    with pytest.raises(Conflict): runtime.restart(job['id'],job,inputs())
    # Explicit retry cleans an interrupted cleanup, keeping ownership until done.
    runtime.stop(job['id'],job)
    eventually(lambda:runtime.store.active() is None)


def test_final_full_watch_discards_document_without_ready(runtime):
    class FinalFull(FakeBrowser):
        final_available=False
    runtime.client_factory=FinalFull
    job=runtime.create(inputs())
    wait_state(runtime,job['id'],'WAITING_AVAILABLE')
    eventually(lambda:job['id'] not in runtime.clients)
    assert runtime.store.get(job['id'])['generation']==2
    assert runtime.store.events()==[]
    runtime.stop(job['id'],runtime.store.get(job['id']))
    wait_state(runtime,job['id'],'STOPPED')


def test_final_full_watch_survives_slow_browser_cleanup(runtime):
    class SlowClose(FakeBrowser):
        final_available = False
        async def close(self):
            self.closed = True
            await asyncio.sleep(.7)  # Let the 250 ms session monitor run during cleanup.
    runtime.client_factory = SlowClose
    job = runtime.create(inputs())
    wait_state(runtime, job['id'], 'WAITING_AVAILABLE')
    eventually(lambda: job['id'] not in runtime.clients)
    current = runtime.store.get(job['id'])
    assert current['active'] and job['id'] in runtime.inputs
    assert current['generation'] == 2 and runtime.store.events() == []
    # Advance the pending interval by cancelling only its sleeper, then resume
    # the real flow to verify the preserved inputs can start a new browser.
    async def resume():
        task = runtime.tasks.get(job['id'])
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        runtime.client_factory = FakeBrowser
        await runtime._flow(job['id'])
    runtime._submit(resume()).result(timeout=5)
    wait_state(runtime, job['id'], READY)
