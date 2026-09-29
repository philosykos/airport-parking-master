import asyncio
import json
import random
import subprocess
import sys
import time
import urllib.request
from dataclasses import replace

import pytest

from services.gimpo.config import CONFIG
from services.gimpo.jobs import GimpoRuntime
from services.gimpo.store import READY
from services.gimpo.watch import exit_candidates, jittered
from tests.gimpo.fakes import FakeNotifier, FixtureBrowser, GatedSleep
from tests.gimpo.helpers import inputs, long_inputs, wait_state
from tests.support.waiting import eventually


@pytest.fixture(scope='module', autouse=True)
def shared_chromium(tmp_path_factory):
    # 이 기계에서는 Chromium 기동과 첫 페이지가 테스트당 수 초를 쓴다. 모듈에서 한 번만 띄우고
    # 각 작업은 CDP로 붙어 새 context만 만든다(context를 닫으면 연결만 끊기고 브라우저는 남는다).
    executable = subprocess.run(
        [sys.executable, '-c', 'from playwright.sync_api import sync_playwright\n'
         'with sync_playwright() as p: print(p.chromium.executable_path)'],
        capture_output=True, text=True, check=True, timeout=60).stdout.strip()
    profile = tmp_path_factory.mktemp('chromium')
    process = subprocess.Popen(
        [executable, '--headless', '--remote-debugging-port=0', '--no-first-run', '--no-default-browser-check',
         '--disable-background-networking', '--disable-component-update', '--disable-sync', '--mute-audio',
         f'--user-data-dir={profile}', 'about:blank'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        # Chromium은 열린 포트와 경로를 프로필의 DevToolsActivePort 파일에 쓴다.
        active = profile / 'DevToolsActivePort'
        deadline = time.monotonic() + 30
        while not (active.exists() and len(active.read_text().split()) >= 2):
            assert process.poll() is None and time.monotonic() < deadline, 'shared Chromium did not start'
            time.sleep(.05)
        port, path = active.read_text().split()[:2]
        endpoint = f'ws://127.0.0.1:{port}{path}'
        FixtureBrowser.cdp_endpoint = endpoint
        yield endpoint
        # 끊긴 연결의 context가 치워지지 않고 쌓이면 테스트끼리 섞인다: 처음 연 빈 페이지만 남아야 한다.
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list', timeout=5) as response:
            pages = [t['url'] for t in json.load(response) if t['type'] == 'page']
        assert pages == ['about:blank'], pages
    finally:
        FixtureBrowser.cdp_endpoint = None
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@pytest.fixture
def browser_runtime(tmp_path):
    runtime = GimpoRuntime(replace(CONFIG,directory=tmp_path/'data',browser_timeout_sec=5),FixtureBrowser,notifier=FakeNotifier())
    yield runtime
    # Tests release payment sessions explicitly, never leave test browsers behind.
    job=runtime.store.active()
    if job and job['paymentMayHaveBeenSent']:
        runtime.stop(job['id'],job)
        eventually(lambda:not runtime.store.active())
    runtime.close()


def test_real_browser_flow_double_click_accumulated_handlers(browser_runtime):
    runtime=browser_runtime
    job=runtime.create(inputs())
    eventually(lambda:runtime.store.get(job['id'])['state'] in {READY,'ERROR','REVIEW_REQUIRED'},timeout=15)
    assert runtime.store.get(job['id'])['state']==READY, runtime.store.get(job['id'])['reason']
    client=runtime.clients[job['id']]
    assert client.forwarded==[]
    async def clicks():
        await client.page.evaluate('payment(); payment();')
        await client.page.locator('#confirmOk').dblclick()
        await asyncio.sleep(.4)
    runtime._submit(clicks()).result(timeout=5)
    assert client.forwarded==['/reservation/payment.json']
    assert runtime.store.get(job['id'])['paymentMayHaveBeenSent']
    assert runtime.store.get(job['id'])['state']=='PAYMENT_RESULT_UNKNOWN'


@pytest.mark.parametrize('mutation,state', [('cancel','HANDOFF_CANCELLED'),('navigate','HANDOFF_CANCELLED'),('amount','HANDOFF_CANCELLED')])
def test_handoff_invalidates_before_any_payment(browser_runtime,mutation,state):
    runtime=browser_runtime
    job=runtime.create(inputs())
    eventually(lambda:runtime.store.get(job['id'])['state']==READY,timeout=15)
    client=runtime.clients[job['id']]
    async def change():
        if mutation=='cancel': await client.page.click('#confirmNo')
        elif mutation=='navigate':
            # Navigation invalidation closes the context before goto can complete.
            from playwright.async_api import Error
            try:
                await client.page.goto('https://park.airport.co.kr/reservation/recheck.do')
            except Error:
                assert runtime.store.get(job['id'])['state'] == 'HANDOFF_CANCELLED'
        else: await client.page.evaluate("document.getElementById('paymentAmt').value='0'")
    runtime._submit(change()).result(timeout=5)
    wait_state(runtime,job['id'],state)
    eventually(lambda:not runtime.store.get(job['id'])['active'])
    assert client.forwarded==[]


# 중복 코드 '10'/'20'과 결제 금액 '0'/'-1'은 각각 같은 분기(code != '00', paymentAmt <= 0)로 끝나므로 브라우저는
# 한 건씩만 돈다. 코드·금액 판정 자체는 test_gimpo_contract.py가 OfficialContract로 직접 확인한다.
@pytest.mark.parametrize('setting,value,state', [('codes',('10',),'STOPPED'),('duplicate','10','REVIEW_REQUIRED'),
    ('payment_amount','0','REVIEW_REQUIRED'),('error_html',True,'SESSION_EXPIRED')])
def test_non_success_is_never_ready(browser_runtime,setting,value,state):
    runtime=browser_runtime
    class Scenario(FixtureBrowser): pass
    setattr(Scenario,setting,value)
    runtime.client_factory=Scenario
    raw=inputs('once' if setting=='codes' else 'watch')
    job=runtime.create(raw)
    eventually(lambda:runtime.store.get(job['id'])['state']==state,timeout=15)
    assert not any(e['kind']=='READY' for e in runtime.store.events())


def test_cancel_and_immediate_modal_reentry_never_sends(browser_runtime):
    runtime=browser_runtime
    job=runtime.create(inputs())
    eventually(lambda:runtime.store.get(job['id'])['state']==READY,timeout=15)
    client=runtime.clients[job['id']]
    async def rapid_reentry():
        await client.page.evaluate("document.getElementById('confirmNo').click(); payment(); document.getElementById('confirmOk').click();")
    runtime._submit(rapid_reentry()).result(timeout=5)
    wait_state(runtime,job['id'],'HANDOFF_CANCELLED')
    assert client.forwarded==[]


def test_zero_amount_direct_submission_blocked(browser_runtime):
    runtime=browser_runtime
    job=runtime.create(inputs())
    eventually(lambda:runtime.store.get(job['id'])['state']==READY,timeout=15)
    client=runtime.clients[job['id']]
    async def submit():
        await client.page.evaluate("const f=document.getElementById('reservationVO');f.method='POST';f.action='/reservation/insertAction.do';f.submit()")
    runtime._submit(submit()).result(timeout=5)
    eventually(lambda:runtime.store.get(job['id'])['state'] in {'REVIEW_REQUIRED','HANDOFF_CANCELLED'})
    assert client.forwarded==[]


def test_browser_closed_before_payment(browser_runtime):
    runtime=browser_runtime
    job=runtime.create(inputs())
    eventually(lambda:runtime.store.get(job['id'])['state']==READY,timeout=15)
    client=runtime.clients[job['id']]
    runtime._submit(client.page.close()).result(timeout=5)
    # 공유 Chromium은 연결된 채 예약창만 닫혔으므로 사용자가 닫은 것으로 본다.
    wait_state(runtime,job['id'],'HANDOFF_CANCELLED')
    assert client.forwarded==[]


def test_other_tab_cannot_dispatch_first_payment(browser_runtime):
    runtime=browser_runtime
    job=runtime.create(inputs())
    eventually(lambda:runtime.store.get(job['id'])['state']==READY,timeout=15)
    client=runtime.clients[job['id']]
    async def second_tab():
        other=await client.context.new_page()
        await other.goto('https://park.airport.co.kr/reservation/recheck.do')
        await other.evaluate("fields => fetch('/reservation/payment.json',{method:'POST',body:new URLSearchParams(Object.entries(fields).map(([k,v])=>[k,v[0]]))}).catch(()=>{})",client.sealed_form)
        await other.close()
    runtime._submit(second_tab()).result(timeout=5)
    assert client.forwarded==[]
    assert runtime.store.get(job['id'])['state']==READY


def test_final_full_in_manual_browser_flow(browser_runtime):
    runtime=browser_runtime
    class FullOnRecheck(FixtureBrowser): codes=('00','10')
    runtime.client_factory=FullOnRecheck
    job=runtime.create(inputs('once'))
    eventually(lambda:runtime.store.get(job['id'])['state']=='AVAILABLE',timeout=15)
    runtime.prepare(job['id'],job)
    eventually(lambda:runtime.store.get(job['id'])['state']=='PREPARED',timeout=15)
    runtime.proceed(job['id'],job,True)
    eventually(lambda:runtime.store.get(job['id'])['state']=='STOPPED',timeout=15)
    assert runtime.store.events()==[]


# DC001이 아닌 할인은 모두 같은 재계산 분기를 지난다(DC007 금액 판정은 test_gimpo_contract.py).
@pytest.mark.parametrize('discount,amount', [('DC005', 4000)])
def test_official_discount_recalculation(browser_runtime, discount, amount):
    raw = inputs()
    raw['discountSelection'] = discount
    job = browser_runtime.create(raw)
    eventually(lambda: browser_runtime.store.get(job['id'])['state'] in {READY, 'ERROR', 'REVIEW_REQUIRED'}, timeout=15)
    current = browser_runtime.store.get(job['id'])
    assert current['state'] == READY, current['reason']
    assert current['summary']['discountAmt'] == amount
    assert browser_runtime.clients[job['id']].sealed_form['discountCd'] == [discount]
    assert browser_runtime.clients[job['id']].forwarded == []


def advance_application_retry(runtime, job_id):
    """Wake the configured wait without shortening production input limits."""
    async def resume():
        task = runtime.tasks.get(job_id)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        runtime._schedule(job_id, runtime._flow(job_id))
    runtime._submit(resume()).result(timeout=5)


def test_watch_bootstraps_then_repeats_requested_dates_in_same_application_document(browser_runtime):
    runtime = browser_runtime
    class Polling(FixtureBrowser):
        codes = ('00', '10', '10', '00')
    runtime.client_factory = Polling
    job = runtime.create(inputs())
    eventually(lambda: runtime.store.get(job['id'])['state'] == 'WAITING_AVAILABLE', timeout=15)
    client = runtime.clients[job['id']]
    browser, context, page = client.browser, client.context, client.page
    dialogs = []
    page.on('dialog', lambda dialog: dialogs.append(dialog.message))

    async def inspect_first():
        assert page.url.endswith('/reservation/resInsert.do')
        assert await page.locator('#flashMessage').is_visible()
        assert await page.locator('#reservationBtn').is_disabled()
        await page.evaluate("window.sameDocument = 'kept'; document.cookie = 'sessionProbe=kept; path=/'")
        assert await page.evaluate('navigator.webdriver') is False
        assert await page.evaluate('navigator.languages') == ['ko-KR', 'ko']
        return await client._form()
    original = runtime._submit(inspect_first()).result(timeout=5)
    assert client.check_count == 2
    stages = client.pacing_stages
    assert stages.index('submit') < stages.index('transition')
    assert stages[stages.index('transition') + 1] == 'page'
    assert stages.count('field') >= 8
    assert stages[-1] == 'submit'
    assert len(runtime.store.get(job['id'])['logs']) == 1
    advance_application_retry(runtime, job['id'])
    eventually(lambda: client.check_count == 3 and runtime.store.get(job['id'])['state'] == 'WAITING_AVAILABLE')
    assert len(runtime.store.get(job['id'])['logs']) == 2
    advance_application_retry(runtime, job['id'])
    current = wait_state(runtime, job['id'], READY)
    assert len(current['logs']) == 3

    async def inspect_final():
        assert await page.evaluate('window.sameDocument') == 'kept'
        assert 'sessionProbe=kept' in await page.evaluate('document.cookie')
        assert await client._form() == original
        assert len(context.pages) == 1
    runtime._submit(inspect_final()).result(timeout=5)
    assert (client.browser, client.context, client.page) == (browser, context, page)
    assert sum(path == '/reservation/resInsert.do' for path, _ in client.requests) == 1
    checks = [frame for path, frame in client.requests if path == '/reservation/reservationCheck.json']
    assert len(checks) == 4 and checks[0].endswith('/reservation/recheck.do')
    assert all(frame.endswith('/reservation/resInsert.do') for frame in checks[1:])
    assert client.bootstrap_form['resInDttm'] != client.inputs['entryAt'] + ':00'
    for fields in client.availability_forms[1:]:
        assert fields['resInDttm'] == [client.inputs['entryAt'] + ':00']
        assert fields['resOutDttm'] == [client.inputs['exitAt'] + ':00']
    assert client.quote_requests[0]['inDttm'] == [client.inputs['entryAt'] + ':00']
    assert client.quote_requests[0]['outDttm'] == [client.inputs['exitAt'] + ':00']
    assert sum(path == '/reservation/duplicateReservation.json' for path, _ in client.requests) == 3
    assert dialogs == []
    assert client.version['generation'] == current['generation'] == 1
    assert client.forwarded == []
    assert len(runtime.store.events(job['id'])) == 1

    async def confirm():
        await page.locator('#confirmOk').dblclick()
        await asyncio.sleep(.2)
    runtime._submit(confirm()).result(timeout=5)
    assert client.forwarded == ['/reservation/payment.json']
    assert runtime.store.get(job['id'])['paymentMayHaveBeenSent']


def test_stop_while_waiting_in_application_closes_browser(browser_runtime):
    runtime = browser_runtime
    class Full(FixtureBrowser):
        codes = ('00', '10')
    runtime.client_factory = Full
    job = runtime.create(inputs())
    eventually(lambda: runtime.store.get(job['id'])['state'] == 'WAITING_AVAILABLE', timeout=15)
    client = runtime.clients[job['id']]
    runtime.stop(job['id'], runtime.store.get(job['id']))
    eventually(lambda: not runtime.store.get(job['id'])['active'])
    assert client.closed and client.check_count == 2 and client.forwarded == []


def test_bootstrap_price_is_replaced_with_requested_period_price(browser_runtime):
    runtime = browser_runtime
    class LongerStay(FixtureBrowser):
        quote_amount = '72000'
    runtime.client_factory = LongerStay
    job = runtime.create(inputs())
    eventually(lambda: runtime.store.get(job['id'])['state'] in {READY, 'ERROR', 'REVIEW_REQUIRED'}, timeout=15)
    current = runtime.store.get(job['id'])
    assert current['state'] == READY, current['reason']
    assert current['summary']['calculateAmt'] == 72000
    assert current['summary']['receiptAmt'] == 62000
    client = runtime.clients[job['id']]
    async def inspect():
        display = await client.page.locator('#application-date-display').inner_text()
        assert client.inputs['entryAt'] + ':00' in display
        assert client.inputs['exitAt'] + ':00' in display
        assert client.bootstrap_form['resInDttm'] not in display
    runtime._submit(inspect()).result(timeout=5)
    assert client.forwarded == []


def test_missing_official_requested_price_stops_before_reservation(browser_runtime):
    runtime = browser_runtime
    class NoQuote(FixtureBrowser):
        quote_amount = None
    runtime.client_factory = NoQuote
    job = runtime.create(inputs())
    eventually(lambda: runtime.store.get(job['id'])['state'] == 'REVIEW_REQUIRED', timeout=15)
    assert runtime.store.events(job['id']) == []


def test_background_confirmation_rejects_mismatch_and_preserves_manual_dialog(browser_runtime):
    runtime = browser_runtime
    class Full(FixtureBrowser):
        codes = ('00', '10')
    runtime.client_factory = Full
    job = runtime.create(inputs())
    eventually(lambda: runtime.store.get(job['id'])['state'] == 'WAITING_AVAILABLE', timeout=15)
    client = runtime.clients[job['id']]
    async def inspect():
        assert await client.page.evaluate('''() => {
            window.__gimpoAutomaticConfirmation = true;
            const accepted = confirm('작성 내용을 다시 한번 확인해주세요. wrong dates');
            window.__gimpoAutomaticConfirmation = false;
            return !accepted && window.__gimpoConfirmationMismatch;
        }''')
        async with client.page.expect_event('dialog') as pending:
            await client.page.evaluate("setTimeout(() => confirm('manual confirmation'), 0)")
        dialog = await pending.value
        assert dialog.message == 'manual confirmation'
        await dialog.dismiss()
    runtime._submit(inspect()).result(timeout=5)
    assert client.forwarded == []


def dispatched(runtime):
    job = runtime.create(inputs())
    eventually(lambda: runtime.store.get(job['id'])['state'] == READY, timeout=15)
    client = runtime.clients[job['id']]
    async def pay():
        await client.page.evaluate('payment();')
        await client.page.locator('#confirmOk').click()
        await asyncio.sleep(.4)
    runtime._submit(pay()).result(timeout=5)
    assert runtime.store.get(job['id'])['paymentMayHaveBeenSent']
    return job, client


def arrive(runtime, client, path):
    async def go():
        await client.page.goto('https://park.airport.co.kr' + path)
    runtime._submit(go()).result(timeout=5)


def test_completion_page_reserves_and_closes_window(browser_runtime):
    runtime = browser_runtime
    runtime.completion_hold_sec = 0
    job, client = dispatched(runtime)
    arrive(runtime, client, '/reservation/resComplete.do')
    eventually(lambda: not runtime.store.get(job['id'])['active'], timeout=10)
    final = runtime.store.get(job['id'])
    assert (final['state'], final['reservationNo']) == ('RESERVED', '1234AB5678')
    assert client.closed


@pytest.mark.parametrize('overrides', [{'__RESERVATION_NO__': 'x'}, {'__CAR_NUMBER__': '999가9999'}])
def test_mismatched_completion_page_is_not_judged(browser_runtime, overrides):
    runtime = browser_runtime
    class Scenario(FixtureBrowser): pass
    Scenario.completion_overrides = overrides
    runtime.client_factory = Scenario
    job, client = dispatched(runtime)
    before = runtime.store.get(job['id'])['state']
    arrive(runtime, client, '/reservation/resComplete.do')
    eventually(lambda: runtime.store.get(job['id'])['reason'] == '예약확인 화면을 확인하지 못했습니다.', timeout=10)
    assert runtime.store.get(job['id'])['state'] == before and runtime.store.get(job['id'])['active']


def test_non_complete_airport_page_is_not_judged(browser_runtime):
    runtime = browser_runtime
    job, client = dispatched(runtime)
    arrive(runtime, client, '/reservation/resView.do')
    time.sleep(1)
    current = runtime.store.get(job['id'])
    assert current['state'] != 'RESERVED' and current['active'] and 'reservationNo' not in current


def test_stop_during_transition_pause_never_opens_application(browser_runtime):
    runtime = browser_runtime
    class Paused(FixtureBrowser):
        async def _pace(self, stage):
            await super()._pace(stage)
            if stage == 'transition':
                await asyncio.Event().wait()
    runtime.client_factory = Paused
    job = runtime.create(inputs())
    eventually(lambda: job['id'] in runtime.clients and
               'transition' in runtime.clients[job['id']].pacing_stages, timeout=15)
    client = runtime.clients[job['id']]
    runtime.stop(job['id'], runtime.store.get(job['id']))
    eventually(lambda: not runtime.store.get(job['id'])['active'])
    assert client.closed
    assert not any(path == '/reservation/resInsert.do' for path, _ in client.requests)
    assert client.forwarded == []


def test_application_switches_exit_candidates_with_cached_quotes(browser_runtime):
    runtime = browser_runtime
    class Full(FixtureBrowser):
        # 1단계 진입 00 → 런타임의 첫 신청 10 → 아래 직접 신청 10, 10, 10, 00
        codes = ('00', '10', '10', '10', '10', '00')
    runtime.client_factory = Full
    raw = long_inputs(discount='DC005')
    job = runtime.create(raw)
    eventually(lambda: runtime.store.get(job['id'])['state'] == 'WAITING_AVAILABLE', timeout=15)
    client = runtime.clients[job['id']]
    d, d1, d2 = exit_candidates(raw['entryAt'], raw['exitAt'])
    def discount_requests():
        return sum(path == '/reservation/calculateDiscountAmt.json' for path, _ in client.requests)
    first_form = runtime._submit(client._form()).result(timeout=5)
    assert client.exit_at == d
    assert (len(client.quote_requests), discount_requests()) == (1, 1)

    available, _, summary = runtime._submit(client.proceed(d1)).result(timeout=10)
    assert not available and summary['exitAt'] == d1
    assert client.availability_forms[-1]['resInDttm'] == [raw['entryAt'] + ':00']
    assert client.availability_forms[-1]['resOutDttm'] == [d1 + ':00']
    assert client.quote_requests[-1]['outDttm'] == [d1 + ':00']
    assert (len(client.quote_requests), discount_requests()) == (2, 2)

    # 이미 받은 후보로 돌아가면 공항 요금을 다시 부르지 않고, 폼은 처음 받은 값과 같다.
    runtime._submit(client.proceed(d)).result(timeout=10)
    assert runtime._submit(client._form()).result(timeout=5) == first_form
    runtime._submit(client.proceed(d1)).result(timeout=10)
    assert (len(client.quote_requests), discount_requests()) == (2, 2)

    available, _, summary = runtime._submit(client.proceed(d2)).result(timeout=10)
    assert available and summary['exitAt'] == d2 and summary['discountAmt'] == 4000
    assert client.sealed_form['resOutDttm'] == [d2 + ':00']
    async def inspect():
        display = await client.page.locator('#application-date-display').inner_text()
        assert display == f"{raw['entryAt']}:00 ~ {d2}:00"
        assert await client.page.evaluate('window.__gimpoConfirmationMismatch') is False
    runtime._submit(inspect()).result(timeout=5)
    assert client.forwarded == []


def test_manual_confirmation_shows_current_candidate_dates(browser_runtime):
    runtime = browser_runtime
    class Full(FixtureBrowser):
        codes = ('00', '10', '10')
    runtime.client_factory = Full
    raw = long_inputs()
    job = runtime.create(raw)
    eventually(lambda: runtime.store.get(job['id'])['state'] == 'WAITING_AVAILABLE', timeout=15)
    client = runtime.clients[job['id']]
    d1 = exit_candidates(raw['entryAt'], raw['exitAt'])[1]
    runtime._submit(client.proceed(d1)).result(timeout=10)
    async def inspect():
        async with client.page.expect_event('dialog') as pending:
            await client.page.evaluate("setTimeout(() => confirm('작성 내용을 다시 한번 확인해주세요. ' + "
                                       "window.__gimpoPageDates.join(' ')), 0)")
        dialog = await pending.value
        assert dialog.message == f"작성 내용을 다시 한번 확인해주세요. {raw['entryAt']}:00 {d1}:00"
        await dialog.dismiss()
    runtime._submit(inspect()).result(timeout=5)


def test_bootstrap_window_wait_is_jittered_and_stoppable(tmp_path):
    gate = GatedSleep(free=1)
    class WindowsFull(FixtureBrowser):
        codes = ('10', '10', '00')
    runtime = GimpoRuntime(replace(CONFIG, directory=tmp_path / 'data', browser_timeout_sec=5), WindowsFull,
                           notifier=FakeNotifier(), rng=random.Random(3), sleep=gate)
    try:
        job = runtime.create(inputs())
        eventually(lambda: len(gate.delays) == 2, timeout=15)
        replica = random.Random(3)
        assert gate.delays == [jittered(30, replica), jittered(30, replica)]
        client = runtime.clients[job['id']]
        assert client.check_count == 2
        runtime.stop(job['id'], runtime.store.get(job['id']))
        eventually(lambda: not runtime.store.get(job['id'])['active'])
        assert client.check_count == 2 and client.closed
    finally:
        runtime.close()


def test_candidate_completion_is_judged_with_candidate_exit(tmp_path):
    gate = GatedSleep(free=1)
    class SecondFree(FixtureBrowser):
        codes = ('00', '10', '00')
    runtime = GimpoRuntime(replace(CONFIG, directory=tmp_path / 'data', browser_timeout_sec=5), SecondFree,
                           notifier=FakeNotifier(), rng=random.Random(3), sleep=gate)
    runtime.completion_hold_sec = 0
    try:
        raw = long_inputs()
        job = runtime.create(raw)
        eventually(lambda: runtime.store.get(job['id'])['state'] == READY, timeout=15)
        d1 = exit_candidates(raw['entryAt'], raw['exitAt'])[1]
        assert runtime.store.get(job['id'])['summary']['exitAt'] == d1
        client = runtime.clients[job['id']]
        async def pay():
            await client.page.locator('#confirmOk').click()
            await asyncio.sleep(.4)
            await client.page.goto('https://park.airport.co.kr/reservation/resComplete.do')
        runtime._submit(pay()).result(timeout=5)
        eventually(lambda: runtime.store.get(job['id'])['state'] == 'RESERVED', timeout=10)
    finally:
        runtime.close()
