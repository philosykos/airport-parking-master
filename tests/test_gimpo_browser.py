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


def test_polling_reuses_document_and_stealth_context(browser_runtime):
    runtime = browser_runtime
    class Polling(FixtureBrowser):
        codes = ('10', '10', '00')
    runtime.client_factory = Polling
    job = runtime.create(inputs())
    wait_state(runtime, job['id'], 'WAITING_AVAILABLE')
    client = runtime.clients[job['id']]
    browser, context, page = client.browser, client.context, client.page

    async def repeat():
        await page.evaluate("window.sameDocument = 'kept'; document.cookie = 'sessionProbe=kept; path=/'")
        assert await page.evaluate('navigator.webdriver') is False
        assert await page.evaluate('navigator.languages') == ['ko-KR', 'ko']
        assert not await client.check()
        assert await client.check()
        assert await page.evaluate('window.sameDocument') == 'kept'
        assert 'sessionProbe=kept' in await page.evaluate('document.cookie')
    runtime._submit(repeat()).result(timeout=10)
    assert (client.browser, client.context, client.page) == (browser, context, page)
    assert len(context.pages) == 1 and client.check_count == 3
    runtime.stop(job['id'], runtime.store.get(job['id']))
    eventually(lambda: not runtime.store.get(job['id'])['active'])
    assert client.closed


def test_final_full_reuses_window_and_updates_payment_generation(browser_runtime):
    runtime = browser_runtime
    class FinalFull(FixtureBrowser):
        codes = ('00', '10', '00', '00')
    runtime.client_factory = FinalFull
    job = runtime.create(inputs())
    wait_state(runtime, job['id'], 'WAITING_AVAILABLE')
    client = runtime.clients[job['id']]
    browser, context, page = client.browser, client.context, client.page

    async def resume():
        await page.evaluate("sessionStorage.setItem('sessionProbe', 'kept')")
        sleeper = runtime.tasks.get(job['id'])
        if sleeper:
            sleeper.cancel()
            await asyncio.gather(sleeper, return_exceptions=True)
        await runtime._flow(job['id'])
        assert await page.evaluate("sessionStorage.getItem('sessionProbe')") == 'kept'
    runtime._submit(resume()).result(timeout=15)
    current = wait_state(runtime, job['id'], READY)
    assert runtime.clients[job['id']] is client
    assert (client.browser, client.context, client.page) == (browser, context, page)
    assert len(context.pages) == 1 and current['generation'] == 2
    assert client.version['generation'] == 2
    assert len(runtime.store.events(job['id'])) == 1

    async def confirm():
        await page.locator('#confirmOk').click()
        await asyncio.sleep(.2)
    runtime._submit(confirm()).result(timeout=5)
    assert client.forwarded == ['/reservation/payment.json']
    assert runtime.store.get(job['id'])['paymentMayHaveBeenSent']
