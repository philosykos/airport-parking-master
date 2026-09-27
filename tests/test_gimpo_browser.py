import asyncio
from dataclasses import replace

import pytest

from services.gimpo.config import CONFIG
from services.gimpo.jobs import GimpoRuntime
from services.gimpo.store import READY
from tests.gimpo_fakes import FakeNotifier, FixtureBrowser
from tests.test_gimpo_jobs import eventually, inputs, wait_state


@pytest.fixture
def browser_runtime(tmp_path):
    runtime = GimpoRuntime(replace(CONFIG,directory=tmp_path/'data',browser_timeout_sec=5),FixtureBrowser,FakeNotifier())
    yield runtime
    # Tests release payment sessions explicitly, never leave test browsers behind.
    job=runtime.store.active()
    if job and job['paymentMayHaveBeenSent']:
        runtime.resolve(job['id'],job,'unknown',True)
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


@pytest.mark.parametrize('setting,value,state', [('codes',('10',),'STOPPED'),('duplicate','10','REVIEW_REQUIRED'),
    ('duplicate','20','REVIEW_REQUIRED'),('payment_amount','0','REVIEW_REQUIRED'),('payment_amount','-1','REVIEW_REQUIRED'),
    ('error_html',True,'SESSION_EXPIRED')])
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
    wait_state(runtime,job['id'],'SESSION_EXPIRED')
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


@pytest.mark.parametrize('discount,amount', [('DC005', 4000), ('DC007', 1600)])
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
