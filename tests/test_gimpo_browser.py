import asyncio
from dataclasses import replace

import pytest

from services.gimpo_config import CONFIG
from services.gimpo_jobs import GimpoRuntime
from services.gimpo_store import READY
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
