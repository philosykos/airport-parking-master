import asyncio
import json
import time
from dataclasses import replace
from datetime import datetime

import pytest
from playwright.async_api import Error as PlaywrightError

from services.gimpo.config import CONFIG
from services.gimpo.jobs import GimpoRuntime, ProcessLease, RuntimeUnavailable
from services.gimpo.parking import GimpoService
from services.gimpo.store import Conflict, JobStore, READY
from services.gimpo.validation import SEOUL, validate
from tests.gimpo.fakes import FakeBrowser, FakeNotifier
from tests.gimpo.helpers import inputs, valid_input, wait_state
from tests.support.waiting import eventually


@pytest.fixture
def runtime(tmp_path):
    instance = GimpoRuntime(replace(CONFIG, directory=tmp_path / 'data'), FakeBrowser, notifier=FakeNotifier())
    yield instance
    instance.close()


def ready(runtime):
    job = runtime.create(inputs())
    return wait_state(runtime, job['id'], READY)


REASON = '공항 예약확인 화면에서 예약 완료를 확인했습니다(예약번호 1234AB5678).'


def paying(runtime, state='PAYMENT_IN_PROGRESS'):
    job = ready(runtime)
    runtime.store.dispatch_payment(job['id'], job)
    if state != 'PAYMENT_DISPATCHING': runtime.store.transition(job['id'], state, 'test')
    return job


@pytest.mark.parametrize('state', ['PAYMENT_DISPATCHING', 'PAYMENT_IN_PROGRESS', 'PAYMENT_RESULT_UNKNOWN'])
def test_reserved_holds_window_then_releases(tmp_path, state):
    runtime = GimpoRuntime(replace(CONFIG, directory=tmp_path / 'data'), FakeBrowser, notifier=FakeNotifier(), completion_hold_sec=30)
    try:
        job = paying(runtime, state)
        browser = runtime.clients[job['id']]
        runtime.loop.call_soon_threadsafe(runtime.reserved, job['id'], '1234AB5678')
        eventually(lambda: runtime.store.get(job['id'])['state'] == 'RESERVED')
        held = runtime.store.get(job['id'])
        assert held['active'] and held['reservationNo'] == '1234AB5678' and held['reason'] == REASON and not browser.closed
        with pytest.raises(Conflict): runtime.stop(job['id'], held)
        with pytest.raises(Conflict): runtime.create(inputs())
        assert runtime.store.get(job['id'])['active'] and not browser.closed  # 창이 살아 있는 동안은 대기가 끝나야 닫는다
        runtime.completion_hold_sec = 0
        eventually(lambda: not runtime.store.get(job['id'])['active'])
        final = runtime.store.get(job['id'])
        assert browser.closed and final['state'] == 'RESERVED' and final['reason'] == REASON
        assert [log['message'] for log in final['logs']].count(REASON) == 1
    finally:
        runtime.close()


def test_reserved_ends_early_when_user_closes_window(tmp_path):
    runtime = GimpoRuntime(replace(CONFIG, directory=tmp_path / 'data'), FakeBrowser, notifier=FakeNotifier(), completion_hold_sec=30)
    try:
        job = paying(runtime)
        runtime.loop.call_soon_threadsafe(runtime.reserved, job['id'], '1234AB5678')
        eventually(lambda: runtime.store.get(job['id'])['state'] == 'RESERVED')
        runtime._submit(runtime.clients[job['id']].close()).result(timeout=3)
        eventually(lambda: not runtime.store.get(job['id'])['active'], timeout=3)
    finally:
        runtime.close()


def test_reserved_close_failure_still_finishes(tmp_path):
    class Stuck(FakeBrowser):
        async def close(self): raise RuntimeError('close failed')
    runtime = GimpoRuntime(replace(CONFIG, directory=tmp_path / 'data'), Stuck, notifier=FakeNotifier(), completion_hold_sec=0)
    try:
        job = paying(runtime)
        runtime.loop.call_soon_threadsafe(runtime.reserved, job['id'], '1234AB5678')
        eventually(lambda: runtime.store.get(job['id'])['state'] == 'RESERVED' and not runtime.store.get(job['id'])['active'])
    finally:
        Stuck.close = FakeBrowser.close
        runtime.close()


def test_shutdown_during_hold_keeps_reserved(tmp_path):
    runtime = GimpoRuntime(replace(CONFIG, directory=tmp_path / 'data'), FakeBrowser, notifier=FakeNotifier(), completion_hold_sec=30)
    job = paying(runtime)
    browser = runtime.clients[job['id']]
    runtime.loop.call_soon_threadsafe(runtime.reserved, job['id'], '1234AB5678')
    eventually(lambda: runtime.store.get(job['id'])['state'] == 'RESERVED')
    runtime.close()
    store = JobStore(tmp_path / 'data' / 'jobs.sqlite3')
    try:
        final = store.get(job['id'])
        assert browser.closed and (final['state'], final['active']) == ('RESERVED', False)
    finally:
        store.close()


def test_shutdown_while_closing_reserved_window_still_closes_it(tmp_path):
    class SlowClose(FakeBrowser):
        calls = 0
        async def close(self):
            self.calls += 1
            if self.calls == 1:
                await asyncio.sleep(30)  # 첫 닫기가 끝나기 전에 종료가 이 태스크를 취소한다
            await super().close()
    runtime = GimpoRuntime(replace(CONFIG, directory=tmp_path / 'data'), SlowClose, notifier=FakeNotifier(), completion_hold_sec=0)
    job = paying(runtime)
    browser = runtime.clients[job['id']]
    runtime.loop.call_soon_threadsafe(runtime.reserved, job['id'], '1234AB5678')
    eventually(lambda: browser.calls == 1)
    runtime.close()
    store = JobStore(tmp_path / 'data' / 'jobs.sqlite3')
    try:
        final = store.get(job['id'])
        assert browser.closed and (final['state'], final['active']) == ('RESERVED', False)
    finally:
        store.close()


def test_unverified_completion_keeps_state_and_logs_once(runtime):
    job = paying(runtime)
    runtime.loop.call_soon_threadsafe(runtime.completion_unverified, job['id'])
    eventually(lambda: runtime.store.get(job['id'])['reason'] == '예약확인 화면을 확인하지 못했습니다.')
    current = runtime.store.get(job['id'])
    assert current['state'] == 'PAYMENT_IN_PROGRESS' and current['active']
    assert [log['message'] for log in current['logs']].count('예약확인 화면을 확인하지 못했습니다.') == 1


def test_close_payment_on_released_job_does_not_overwrite_reason(runtime):
    # Task 4 리뷰가 지적한 경합: 이미 끝난 작업에 뒤늦게 도착한 종료 처리가 최종 사유를 덮으면 안 된다.
    job = paying(runtime)
    runtime.stop(job['id'], job)
    eventually(lambda: not runtime.store.get(job['id'])['active'])
    final = runtime.store.get(job['id'])
    assert final['state'] == 'CLOSED_BY_USER' and final['reason'] == '예약 완료를 확인하지 못하고 작업을 끝냈습니다.'
    class Stuck(FakeBrowser):
        async def close(self): raise RuntimeError('close failed')
    runtime.clients[job['id']] = Stuck(runtime, final, {})
    runtime._submit(runtime._close_payment(job['id'])).result(timeout=3)
    assert runtime.store.get(job['id'])['reason'] == '예약 완료를 확인하지 못하고 작업을 끝냈습니다.'
    # _close_payment 가드를 지나 닫기에 실패해도(_close_client 단독) 끝난 작업의 사유는 그대로다.
    with pytest.raises(Conflict):
        runtime._submit(runtime._close_client(job['id'])).result(timeout=3)
    assert runtime.store.get(job['id'])['reason'] == '예약 완료를 확인하지 못하고 작업을 끝냈습니다.'


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


@pytest.mark.parametrize('state', ['PAYMENT_DISPATCHING', 'PAYMENT_IN_PROGRESS', 'PAYMENT_RESULT_UNKNOWN'])
def test_payment_stop_closes_window_but_patch_is_rejected(runtime, state):
    job = ready(runtime)
    runtime.store.dispatch_payment(job['id'], job)
    if state != 'PAYMENT_DISPATCHING': runtime.store.transition(job['id'], state, 'test')
    browser = runtime.clients[job['id']]
    for action in [lambda: runtime.stop(job['id'], job, inputs()), lambda: runtime.create(inputs()),
                   lambda: runtime.prepare(job['id'], job), lambda: runtime.restart(job['id'], job, inputs()),
                   lambda: runtime.proceed(job['id'], job, True)]:
        with pytest.raises(Conflict): action()
    assert not browser.closed
    runtime.stop(job['id'], job)
    eventually(lambda: not runtime.store.get(job['id'])['active'])
    final = runtime.store.get(job['id'])
    assert browser.closed and final['state'] == 'CLOSED_BY_USER'
    assert final['reason'] == '예약 완료를 확인하지 못하고 작업을 끝냈습니다.'
    # 중지 명령 한 번은 로그 한 행이다(시작 문구 행이 끝 문구로 갱신된다. 기존 STOPPING→STOPPED와 같다).
    assert final['logs'][-1]['message'] == '예약 완료를 확인하지 못하고 작업을 끝냈습니다.'
    assert not hasattr(runtime, 'resolve')


def test_payment_stop_close_failure_can_be_retried(runtime):
    class Stuck(FakeBrowser):
        fail = True
        async def close(self):
            if Stuck.fail: raise RuntimeError('close failed')
            await super().close()
    runtime.client_factory = Stuck
    job = ready(runtime)
    runtime.store.dispatch_payment(job['id'], job)
    runtime.stop(job['id'], job)
    eventually(lambda: runtime.store.get(job['id'])['reason'] == '예약창을 닫지 못했습니다. ‘중지’를 다시 눌러주세요.')
    assert runtime.store.get(job['id'])['active'] and runtime.store.get(job['id'])['state'] == 'CLOSED_BY_USER'
    with pytest.raises(Conflict): runtime.stop(job['id'], runtime.store.get(job['id']), inputs())  # PATCH
    Stuck.fail = False
    runtime.stop(job['id'], runtime.store.get(job['id']))
    eventually(lambda: not runtime.store.get(job['id'])['active'])


def test_recovered_payment_job_without_browser_stops_at_once(tmp_path):
    config = replace(CONFIG, directory=tmp_path / 'data')
    first = GimpoRuntime(config, FakeBrowser, notifier=FakeNotifier())
    job = wait_state(first, first.create(inputs())['id'], READY)
    first.store.dispatch_payment(job['id'], job)
    first.store.transition(job['id'], 'PAYMENT_IN_PROGRESS', 'test')
    first.closing = True; first.outbox.close(); first.loop.call_soon_threadsafe(first.loop.stop); first.thread.join(5)
    first.store.close(); first.lease.close()
    second = GimpoRuntime(config, FakeBrowser, notifier=FakeNotifier())
    try:
        recovered = second.store.active()
        assert recovered['state'] == 'PAYMENT_RESULT_UNKNOWN'
        time.sleep(1)  # 감시 루프가 창 없는 복구 작업을 스스로 끝내지 않는다
        assert second.store.get(job['id'])['active']
        second.stop(job['id'], recovered)
        eventually(lambda: second.store.get(job['id'])['state'] == 'CLOSED_BY_USER' and not second.store.get(job['id'])['active'])
    finally:
        second.close()


@pytest.mark.parametrize('state', ['PAYMENT_DISPATCHING', 'PAYMENT_IN_PROGRESS', 'PAYMENT_RESULT_UNKNOWN'])
def test_user_closing_window_during_payment_ends_job(runtime, state):
    job = paying(runtime, state)
    runtime._submit(runtime.clients[job['id']].close()).result(timeout=3)
    eventually(lambda: not runtime.store.get(job['id'])['active'])
    final = runtime.store.get(job['id'])
    assert (final['state'], final['reason']) == ('CLOSED_BY_USER', '예약 완료를 확인하지 못하고 작업을 끝냈습니다.')


def test_user_closing_window_in_handoff_is_cancel_without_correction(runtime):
    class UserClosed(FakeBrowser):
        async def closed_by_user(self):
            return self.closed
    runtime.client_factory = UserClosed
    job = ready(runtime)
    runtime._submit(runtime.clients[job['id']].close()).result(timeout=3)
    wait_state(runtime, job['id'], 'HANDOFF_CANCELLED')
    assert runtime.store.get(job['id'])['reason'] == '공항 예약창이 닫혔습니다. 계속하려면 ‘빈자리 조회’ 또는 ‘자동 예약 시작’을 눌러주세요.'
    assert not [e for e in runtime.store.events() if e['kind'] == 'CORRECTION']


def test_shutdown_in_handoff_interrupts(tmp_path):
    runtime = GimpoRuntime(replace(CONFIG, directory=tmp_path / 'data'), FakeBrowser, notifier=FakeNotifier())
    job = ready(runtime)
    eventually(lambda: [e['status'] for e in runtime.store.events() if e['kind'] == 'READY'] == ['SENT'])
    runtime.close()
    store = JobStore(tmp_path / 'data' / 'jobs.sqlite3')
    try:
        final = store.get(job['id'])
        assert (final['state'], final['active'], final['reason']) == ('INTERRUPTED', False, '프로그램이 종료되어 결제 대기를 끝냈습니다.')
        assert [e['cause'] for e in store.events() if e['kind'] == 'CORRECTION'] == ['INTERRUPTED']
    finally:
        store.close()


def test_shutdown_racing_payment_start_marks_result_unknown(tmp_path):
    runtime = GimpoRuntime(replace(CONFIG, directory=tmp_path / 'data'), FakeBrowser, notifier=FakeNotifier())
    job = ready(runtime)
    finish_pre = runtime._finish_pre
    async def payment_first(job_id, *args):
        # 종료가 결제 대기를 읽은 직후 사용자가 결제를 시작한 순서를 재현한다.
        runtime.store.dispatch_payment(job_id, runtime.store.get(job_id))
        await finish_pre(job_id, *args)
    runtime._finish_pre = payment_first
    runtime.close()
    store = JobStore(tmp_path / 'data' / 'jobs.sqlite3')
    try:
        final = store.get(job['id'])
        assert (final['state'], final['active']) == ('PAYMENT_RESULT_UNKNOWN', True)
    finally:
        store.close()


def test_expiration_and_cancellation_release_slot(runtime):
    job = ready(runtime)
    runtime.store.clock = lambda:job['handoffDeadline']
    wait_state(runtime, job['id'], 'HANDOFF_EXPIRED')
    eventually(lambda:not runtime.store.get(job['id'])['active'])
    assert job['id'] not in runtime.clients


TARGET_CLOSED_MESSAGE = "Target page, context or browser has been closed"


def test_target_closed_error_and_now_dead_browser_expires_session(runtime):
    # alive() True on the first call, inspect() raises the target-closed error, alive() False afterwards.
    class Browser(FakeBrowser):
        armed = False
        async def inspect(self):
            if not self.armed:
                return await super().inspect()
            self.closed = True
            raise PlaywrightError(TARGET_CLOSED_MESSAGE)
    runtime.client_factory = Browser
    job = ready(runtime)
    runtime.clients[job['id']].armed = True
    wait_state(runtime, job['id'], 'SESSION_EXPIRED')


def test_target_closed_error_with_still_alive_browser_expires_session(runtime):
    # alive() True, inspect() raises the target-closed error, alive() still True.
    class Browser(FakeBrowser):
        armed = False
        async def inspect(self):
            if not self.armed:
                return await super().inspect()
            raise PlaywrightError(TARGET_CLOSED_MESSAGE)
    runtime.client_factory = Browser
    job = ready(runtime)
    runtime.clients[job['id']].armed = True
    wait_state(runtime, job['id'], 'SESSION_EXPIRED')


def test_invalid_inspection_and_now_dead_browser_expires_session(runtime):
    # alive() True, inspect() returns False, alive() False afterwards.
    class Browser(FakeBrowser):
        armed = False
        async def inspect(self):
            if not self.armed:
                return await super().inspect()
            self.closed = True
            return False
    runtime.client_factory = Browser
    job = ready(runtime)
    runtime.clients[job['id']].armed = True
    wait_state(runtime, job['id'], 'SESSION_EXPIRED')


def test_invalid_inspection_with_still_alive_browser_cancels_handoff(runtime):
    # alive() True, inspect() returns False, alive() still True: existing behavior kept.
    class Browser(FakeBrowser):
        armed = False
        async def inspect(self):
            if not self.armed:
                return await super().inspect()
            return False
    runtime.client_factory = Browser
    job = ready(runtime)
    runtime.clients[job['id']].armed = True
    wait_state(runtime, job['id'], 'HANDOFF_CANCELLED')


def test_modal_close_does_not_cancel_payment(runtime):
    job = ready(runtime)
    runtime.store.dispatch_payment(job['id'], job)
    runtime.clients[job['id']].live = False
    time.sleep(.4)
    assert runtime.store.get(job['id'])['state'] == 'PAYMENT_DISPATCHING'


def test_process_lease(runtime):
    with pytest.raises(RuntimeUnavailable): ProcessLease(runtime.config.directory)


def test_stop_during_application_preparation_cannot_create_ready(runtime):
    class Slow(FakeBrowser):
        async def prepare(self, *, bootstrap=False, exit_at=None):
            await asyncio.sleep(30)
            return await super().prepare(bootstrap=bootstrap, exit_at=exit_at)
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
    monkeypatch.setenv('RESERVATION_PASSWORD', 'PrivatePass44')
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


def test_defaults_automatic_storage_and_no_password(gimpo_client, runtime):
    raw = valid_input(datetime.now(SEOUL))
    assert gimpo_client.post('/gimpo-parking/api/defaults',json=raw).status_code == 200
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


def test_final_full_watch_keeps_browser_without_ready(runtime):
    class FinalFull(FakeBrowser):
        final_available=False
    runtime.client_factory=FinalFull
    job=runtime.create(inputs())
    wait_state(runtime,job['id'],'WAITING_AVAILABLE')
    browser = runtime.clients[job['id']]
    assert not browser.closed and browser.bootstrap
    assert browser.checks == 0 and browser.prepares == 1 and browser.proceeds == 1
    assert runtime.store.get(job['id'])['generation']==1
    assert runtime.store.events()==[]
    runtime.stop(job['id'],runtime.store.get(job['id']))
    wait_state(runtime,job['id'],'STOPPED')


def test_final_full_watch_reuses_browser_until_stopped(runtime):
    class SlowClose(FakeBrowser):
        final_available = False
        async def close(self):
            self.closed = True
            await asyncio.sleep(.7)  # Let the 250 ms session monitor run during cleanup.
    runtime.client_factory = SlowClose
    job = runtime.create(inputs())
    wait_state(runtime, job['id'], 'WAITING_AVAILABLE')
    browser = runtime.clients[job['id']]
    assert not browser.closed
    current = runtime.store.get(job['id'])
    assert current['active'] and job['id'] in runtime.inputs
    assert current['generation'] == 1 and runtime.store.events() == []
    # Advance the pending interval by cancelling only its sleeper, then resume
    # the real flow to verify the same browser resumes successfully.
    async def resume():
        task = runtime.tasks.get(job['id'])
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        browser.final_available = True
        await runtime._flow(job['id'])
    runtime._submit(resume()).result(timeout=5)
    wait_state(runtime, job['id'], READY)
    assert runtime.clients[job['id']] is browser and not browser.closed
    assert browser.checks == 0 and browser.prepares == 1 and browser.proceeds == 2


def test_api_uses_only_environment_password(gimpo_client, runtime, monkeypatch):
    monkeypatch.setenv('RESERVATION_PASSWORD', 'EnvironmentPass99')
    raw = valid_input(datetime.now(SEOUL), 'watch')
    raw.pop('reservationPassword')
    raw.pop('passwordConfirmation')
    response = gimpo_client.post('/gimpo-parking/api/jobs', json=raw)
    assert response.status_code == 202
    job = wait_state(runtime, response.json['job']['id'], READY)
    assert runtime.clients[job['id']].inputs['reservationPassword'] == 'EnvironmentPass99'
    assert 'EnvironmentPass99' not in response.get_data(as_text=True)
    assert 'EnvironmentPass99' not in '\n'.join(runtime.store.db.iterdump())


def test_invalid_environment_password_has_actionable_error(gimpo_client, monkeypatch):
    monkeypatch.setenv('RESERVATION_PASSWORD', '')
    raw = valid_input(datetime.now(SEOUL))
    response = gimpo_client.post('/gimpo-parking/api/jobs', json=raw)
    assert response.status_code == 400
    assert 'RESERVATION_PASSWORD' in response.json['error']


def test_password_display_endpoint_is_uncached_and_separate(gimpo_client):
    response = gimpo_client.post('/gimpo-parking/api/reservation-password', json={})
    assert response.status_code == 200
    assert response.json == {'reservationPassword': 'PrivatePass44'}
    assert response.headers['Cache-Control'] == 'no-store'
    assert 'PrivatePass44' not in gimpo_client.get('/gimpo-parking/api/defaults').get_data(as_text=True)
    assert 'PrivatePass44' not in gimpo_client.get('/gimpo-parking/').get_data(as_text=True)


def test_clear_logs_bumps_state_version(gimpo_client, runtime):
    c = gimpo_client
    job = ready(runtime)
    url = f'/gimpo-parking/api/jobs/{job["id"]}/logs/clear'
    busy = c.post(url, json={})
    assert busy.status_code == 409 and busy.json == {'error': '진행 중인 작업의 로그는 지울 수 없습니다.'}
    runtime.stop(job['id'], job)
    eventually(lambda: not runtime.store.get(job['id'])['active'])
    before = runtime.store.get(job['id'])
    done = c.post(url, json={})
    assert done.status_code == 202 and done.json['job']['logs'] == []
    assert done.json['job']['stateVersion'] == before['stateVersion'] + 1
    assert c.post('/gimpo-parking/api/jobs/GMP-missing/logs/clear', json={}).status_code == 404
    assert c.post(url, data='x', content_type='text/plain').status_code == 400


def test_resolve_command_is_gone(gimpo_client, runtime):
    job = ready(runtime)
    response = gimpo_client.post(f'/gimpo-parking/api/jobs/{job["id"]}/resolve', json={**job, 'outcome': 'reserved', 'acknowledged': True})
    assert response.status_code == 404
