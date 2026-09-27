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
    monkeypatch.setenv('RESERVATION_PASSWORD', 'PrivatePass44')
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
        assert page.locator('#agree01, #agree03, #agree04, #agree05, #autoProceedConsent').count() == 0
        page.click('#watch')
        page.wait_for_function("document.getElementById('state').textContent === '결제 대기'")
        job_id=runtime.store.active()['id']
        page.reload()
        page.wait_for_function("document.getElementById('state').textContent === '결제 대기'")
        assert runtime.store.active()['id']==job_id
        assert len(runtime.store.events())==1
        assert page.locator('#reservationPassword').get_attribute('type') == 'password'
        assert page.locator('#passwordConfirmation').get_attribute('type') == 'password'
        assert page.locator('#show-browser').is_visible()
        assert page.evaluate('Object.keys(localStorage).length')==0
        page.evaluate('window.scrollTo(0,0)')
        page.screenshot(path=str(tmp_path/'gimpo-desktop.png'),full_page=True)
        page.click('#stop')
        page.wait_for_function("document.getElementById('state').textContent === '중지됨'")
        assert page.locator('#reprepare').count() == 0
        assert page.locator('#watch').is_enabled()
        assert not errors
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        browser.close()


def test_delayed_old_status_cannot_replace_new_work(ui_server):
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
        page.click('#watch')
        page.wait_for_function("document.getElementById('check').disabled && document.getElementById('state').textContent !== '중지됨'")
        current = runtime.store.active()
        assert current and current['id'] != old['id']
        assert len(delayed) == 1
        delayed[0].fulfill(json=snapshot)
        page.wait_for_timeout(100)  # Allow the delayed response's JS continuation to run.
        assert page.locator('#check').is_disabled()
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


@pytest.mark.parametrize('zone', ['Asia/Seoul', 'America/New_York'])
def test_shared_time_picker_uses_wall_time_and_minute_step(zone):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(timezone_id=zone)
        page.set_content('<div id="g"><input></div>')
        for name in ['tokens', 'form']:
            page.add_style_tag(content=Path(f'static/css/{name}.css').read_text())
        for name in ['datepicker', 'timepicker']:
            page.add_script_tag(content=Path(f'static/js/{name}.js').read_text())
        page.evaluate('''() => {
            const D = VanillaPicker.WallClockDate;
            window.picker = new VanillaPicker(document.getElementById('g'), {
                wallClock: true, defaultDate: new D(2026, 8, 28, 12, 20)
            });
            createScrollTimePicker(picker, 'g', 10);
            picker.show();
        }''')
        columns = page.locator('.td-scroll-column-inner')
        page.wait_for_function("document.querySelectorAll('.td-scroll-item.active').length === 2")
        assert page.locator('.td-scroll-item.active').all_text_contents() == ['12', '20']
        assert columns.nth(1).locator('[data-value]').all_text_contents() == ['00', '10', '20', '30', '40', '50']
        columns.nth(0).evaluate('(el) => { el.scrollTop = 14 * 36; }')
        page.wait_for_function("document.querySelector('#g input').value === '2026-09-28 14:20'")
        assert page.locator('.td-icon-prev').evaluate("el => getComputedStyle(el, '::before').content") == '""'
        page.set_viewport_size({'width': 390, 'height': 844})
        page.evaluate('picker.hide(); picker.show()')
        page.wait_for_function("getComputedStyle(picker._widget).flexDirection === 'column'")
        assert page.locator('.td-scroll-time').is_visible()
        browser.close()


def test_defaults_restore_and_shared_calendar_controls(ui_server):
    base, runtime = ui_server
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("!document.getElementById('check').disabled")
        page.fill('#carNumber', '123가4567')
        page.fill('#phone', '01012345678')
        page.select_option('#discountSelection', 'DC007')
        page.wait_for_function("fetch('/gimpo-parking/api/defaults').then(r => r.json()).then(d => d.phone === '01012345678' && d.discountSelection === 'DC007')")
        page.reload()
        page.wait_for_function("!document.getElementById('check').disabled")
        assert page.input_value('#carNumber') == '123가4567'
        assert page.input_value('#phone') == '01012345678'
        assert page.input_value('#discountSelection') == 'DC007'
        assert page.locator('#saveConsent, #load, #save, #date-policy, .handoff-note').count() == 0
        assert page.locator('label[for="entryAt"]').inner_text() == '입차시간'
        assert page.locator('label[for="exitAt"]').inner_text() == '출차시간'
        assert page.locator('#entry-picker .td-toggle .material-symbols-outlined').inner_text() == 'calendar_today'
        page.click('#entry-picker .td-toggle')
        page.wait_for_function("document.querySelector('.tempus-dominus-widget.show .td-scroll-time') !== null")
        assert page.locator('.tempus-dominus-widget.show .td-scroll-time').is_visible()
        page.goto(base + '/t2-valet/')
        assert page.locator('#departingAtPicker .td-toggle .material-symbols-outlined').inner_text() == 'calendar_today'
        browser.close()


def test_environment_password_mask_and_reveal(ui_server):
    base, runtime = ui_server
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("!document.getElementById('toggle-password').disabled")
        field = page.locator('#reservationPassword')
        assert field.input_value() == 'PrivatePass44'
        assert field.get_attribute('type') == 'password'
        assert field.get_attribute('readonly') is not None
        confirmation = page.locator('#passwordConfirmation')
        assert confirmation.input_value() == field.input_value()
        assert confirmation.get_attribute('type') == 'password'
        assert confirmation.get_attribute('readonly') is not None
        page.get_by_role('button', name='확인 비밀번호 보기', exact=True).click()
        assert confirmation.get_attribute('type') == 'text'
        assert field.get_attribute('type') == 'password'
        page.get_by_role('button', name='확인 비밀번호 숨기기', exact=True).click()
        assert confirmation.get_attribute('type') == 'password'
        assert page.evaluate("new FormData(document.getElementById('reservation-form')).has('passwordConfirmation')") is False
        page.get_by_role('button', name='비밀번호 보기', exact=True).click()
        assert field.get_attribute('type') == 'text'
        assert page.locator('#toggle-password').get_attribute('aria-pressed') == 'true'
        page.get_by_role('button', name='비밀번호 숨기기', exact=True).click()
        assert field.get_attribute('type') == 'password'
        assert page.evaluate("new FormData(document.getElementById('reservation-form')).has('reservationPassword')") is False
        assert 'PrivatePass44' not in str(runtime.store.get_defaults())
        page.reload()
        page.wait_for_function("!document.getElementById('toggle-password').disabled")
        assert field.get_attribute('type') == 'password'
        browser.close()


def test_once_full_displays_one_result_row(ui_server):
    base, runtime = ui_server
    class Full(FakeBrowser):
        available = False
    runtime.client_factory = Full
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("!document.getElementById('check').disabled")
        page.fill('#carNumber', '123가4567')
        page.fill('#phone', '01012345678')
        page.click('#check')
        page.wait_for_function("document.getElementById('state').textContent === '중지됨'")
        assert page.locator('#logs li').count() == 1
        assert '1회 조회 결과: 만차입니다.' in page.locator('#logs li').inner_text()
        assert page.get_by_text('1회 조회 결과: 만차입니다.', exact=False).count() == 1
        assert page.locator('#reason, #job-id, #reprepare').count() == 0
        assert page.locator('#job-actions').is_hidden()
        assert page.locator('#freshness').is_hidden()
        page.reload()
        page.wait_for_function("document.getElementById('state').textContent === '중지됨'")
        assert page.locator('#logs li').count() == 1
        browser.close()


def test_summary_displays_discounted_price(ui_server):
    base, runtime = ui_server
    class Discounted(FakeBrowser):
        async def prepare(self, **kwargs):
            summary = await super().prepare(**kwargs)
            return {**summary, 'calculateAmt': 104000, 'discountAmt': 52000, 'receiptAmt': 42000}
    runtime.client_factory = Discounted
    job = runtime.create(inputs())
    wait_state(runtime, job['id'], READY)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("document.querySelector('#summary').textContent.includes('52,000원')")
        assert '104,000원' not in page.locator('#summary').inner_text()
        browser.close()


def test_saved_interval_survives_reload_with_previous_job(ui_server):
    base, runtime = ui_server
    job = runtime.create(inputs())
    job = wait_state(runtime, job['id'], READY)
    runtime.stop(job['id'], job)
    eventually(lambda: not runtime.store.get(job['id'])['active'])
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("!document.getElementById('check').disabled")
        page.fill('#intervalSeconds', '60')
        page.locator('#intervalSeconds').blur()
        page.wait_for_function("fetch('/gimpo-parking/api/defaults').then(r => r.json()).then(d => d.intervalSeconds === 60)")
        page.reload()
        page.wait_for_function("!document.getElementById('check').disabled")
        assert page.input_value('#intervalSeconds') == '60'
        assert runtime.store.get_defaults()['intervalSeconds'] == 60
        browser.close()


@pytest.mark.parametrize('saved_dates', [True, False])
def test_dates_restore_from_defaults_or_legacy_job(ui_server, saved_dates):
    base, runtime = ui_server
    raw = inputs()
    job = runtime.create(raw)
    job = wait_state(runtime, job['id'], READY)
    runtime.stop(job['id'], job)
    eventually(lambda: not runtime.store.get(job['id'])['active'])
    defaults = {'intervalSeconds': 60}
    if saved_dates:
        defaults.update(entryAt=raw['entryAt'], exitAt=raw['exitAt'])
    runtime.store.save_defaults(defaults)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("!document.getElementById('check').disabled")
        assert page.input_value('#entryAt') == raw['entryAt']
        assert page.input_value('#exitAt') == raw['exitAt']
        assert page.input_value('#intervalSeconds') == '60'
        # Calendar/time widgets emit vp.change instead of native change.
        page.locator('#exit-picker').dispatch_event('vp.change')
        page.wait_for_function("fetch('/gimpo-parking/api/defaults').then(r => r.json()).then(d => !!d.entryAt && !!d.exitAt)")
        assert runtime.store.get_defaults()['entryAt'] == raw['entryAt']
        page.reload()
        page.wait_for_function("!document.getElementById('check').disabled")
        assert page.input_value('#exitAt') == raw['exitAt']
        browser.close()


def test_expired_saved_dates_use_current_booking_range(ui_server):
    base, runtime = ui_server
    runtime.store.save_defaults({'entryAt': '2000-01-01 10:00', 'exitAt': '2000-01-01 14:00', 'intervalSeconds': 60})
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("!document.getElementById('check').disabled")
        assert not page.input_value('#entryAt').startswith('2000-')
        assert not page.input_value('#exitAt').startswith('2000-')
        assert page.input_value('#intervalSeconds') == '60'
        browser.close()
