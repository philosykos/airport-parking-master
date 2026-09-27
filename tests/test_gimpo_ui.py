import threading
from dataclasses import replace
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

from app import app
from services.gimpo.config import CONFIG
from services.gimpo.jobs import GimpoRuntime
from services.gimpo.parking import GimpoService
from tests.gimpo_fakes import FakeBrowser, FakeNotifier
from tests.test_gimpo_jobs import eventually, inputs, wait_state
from services.gimpo.store import READY


@pytest.fixture
def ui_server(tmp_path, monkeypatch):
    runtime=GimpoRuntime(replace(CONFIG,directory=tmp_path/'data'),FakeBrowser,FakeNotifier())
    service=GimpoService(runtime.config);service._runtime=runtime
    monkeypatch.setitem(app.extensions,'gimpo',service)
    server=make_server('127.0.0.1',0,app,threaded=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    yield f'http://127.0.0.1:{server.server_port}',runtime
    server.shutdown();thread.join();runtime.close()


def test_form_to_handoff_refresh_and_stop(ui_server,tmp_path):
    base,runtime=ui_server
    with sync_playwright() as p:
        browser=p.chromium.launch()
        context=browser.new_context(timezone_id='America/New_York', viewport={'width':1280,'height':1000})
        context.route('**/*',lambda route: route.continue_() if route.request.url.startswith(base+'/') else route.abort())
        page=context.new_page();errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(base+'/gimpo-parking/')
        page.wait_for_function("!document.getElementById('check').disabled")
        page.fill('#carNumber','123가4567');page.fill('#phone','01012345678')
        page.fill('#reservationPassword','PrivatePass44');page.fill('#passwordConfirmation','PrivatePass44')
        for id in ['agree01','agree03','agree04','agree05','autoProceedConsent']: page.check('#'+id)
        page.click('#watch')
        page.wait_for_function("document.getElementById('state').textContent === '결제 대기'")
        job_id=runtime.store.active()['id']
        page.reload()
        page.wait_for_function("document.getElementById('state').textContent === '결제 대기'")
        assert runtime.store.active()['id']==job_id
        assert len(runtime.store.events())==1
        assert page.input_value('#reservationPassword')==''
        assert page.locator('#show-browser').is_visible()
        assert page.evaluate('Object.keys(localStorage).length')==0
        page.evaluate('window.scrollTo(0,0)')
        page.screenshot(path=str(tmp_path/'gimpo-desktop.png'),full_page=True)
        page.click('#stop')
        page.wait_for_function("document.getElementById('state').textContent === '중지됨'")
        assert page.locator('#reprepare').is_visible()
        assert not errors
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        browser.close()


@pytest.mark.parametrize('restart', [False, True])
def test_delayed_old_status_cannot_replace_new_work(ui_server, restart):
    base, runtime = ui_server
    old = runtime.create(inputs())
    old = wait_state(runtime, old['id'], READY)
    runtime.stop(old['id'], old)
    eventually(lambda: not runtime.store.get(old['id'])['active'])
    snapshot = {'job': runtime.store.get(old['id']), 'notifications': [], 'browserAvailable': False}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context()
        context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
        page = context.new_page()
        delayed = []
        def hold_first(route):
            if not delayed:
                delayed.append(route)
            else:
                route.continue_()
        page.route('**/api/jobs/' + old['id'], hold_first)
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("document.getElementById('state').textContent === '중지됨'")
        page.fill('#carNumber', '123가4567')
        page.fill('#phone', '01012345678')
        page.fill('#reservationPassword', 'PrivatePass44')
        page.fill('#passwordConfirmation', 'PrivatePass44')
        for name in ['agree01', 'agree03', 'agree04', 'agree05', 'autoProceedConsent']:
            page.check('#' + name)
        page.click('#reprepare' if restart else '#watch')
        page.wait_for_function("document.getElementById('check').disabled && document.getElementById('state').textContent !== '중지됨'")
        current = runtime.store.active()
        assert current and (current['id'] == old['id']) is restart
        assert len(delayed) == 1
        delayed[0].fulfill(json=snapshot)
        page.wait_for_timeout(100)  # Allow the delayed response's JS continuation to run.
        assert page.locator('#check').is_disabled()
        assert current['id'] in page.locator('#job-id').inner_text()
        page.wait_for_function("document.getElementById('state').textContent === '결제 대기'")
        assert page.locator('#stop').is_visible()
        assert page.locator('#show-browser').is_visible()
        browser.close()


@pytest.mark.parametrize('zone',['Asia/Seoul','America/New_York','Pacific/Honolulu'])
def test_calendar_wall_time_and_t2_default(zone):
    script=Path('static/js/datepicker.js').read_text()
    with sync_playwright() as p:
        browser=p.chromium.launch()
        page=browser.new_page(timezone_id=zone)
        page.set_content('<div id="g"><input></div><div id="t"><input></div>')
        page.add_script_tag(content=script)
        result=page.evaluate('''() => {
            const D=VanillaPicker.WallClockDate;
            const min=new D(2026,2,8,2,30), max=new D(2026,3,22,23,50);
            const g=new VanillaPicker(document.getElementById('g'),{wallClock:true, defaultDate:min,minDate:min,maxDate:max,now:()=>new D(2026,2,8),validate:d=>d.getMinutes()%10===0});
            g.show();
            const before=g._input.value;
            g._input.value='2026-03-08 02:40';g._applyInputValue();
            const after=g._input.value;
            g._viewDate=new D(2026,3,1);g._renderCalendar();
            const disabled=g._calGrid.querySelector('[data-year="2026"][data-month="3"][data-day="23"]').classList.contains('disabled');
            g._input.value='2026-04-23 00:00';g._applyInputValue();
            const invalid=!g._input.checkValidity();
            const t=new VanillaPicker(document.getElementById('t'),{defaultDate:new Date()});
            const defaultUnchanged=t._maxDate===null && t._Date===Date;
            return {before,after,disabled,invalid,defaultUnchanged};
        }''')
        assert result=={'before':'2026-03-08 02:30','after':'2026-03-08 02:40','disabled':True,'invalid':True,'defaultUnchanged':True}
        browser.close()
