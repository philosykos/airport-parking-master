import asyncio
import json
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
def ui_server(client, tmp_path, monkeypatch):
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
        page.wait_for_function("document.getElementById('completion-overlay').classList.contains('open')")
        page.locator('#completion-overlay').get_by_role('button', name='닫기').click()
        page.wait_for_function("document.getElementById('state').textContent === '결제 대기'")
        job_id=runtime.store.active()['id']
        page.reload()
        page.wait_for_function("document.getElementById('state').textContent === '결제 대기'")
        assert page.locator('#header-status-text').inner_text() == '결제 대기'
        assert page.locator('#header-status').get_attribute('data-tone') == 'warning'
        assert runtime.store.active()['id']==job_id
        assert len(runtime.store.events())==1
        assert page.locator('#reservationPassword').get_attribute('type') == 'password'
        assert page.locator('#passwordConfirmation').get_attribute('type') == 'password'
        assert page.locator('#show-browser').is_visible()
        stored = page.evaluate('({...localStorage})')
        assert all(key.startswith('gimpo.completion.') for key in stored)
        assert '123가4567' not in json.dumps(stored, ensure_ascii=False)
        page.evaluate('window.scrollTo(0,0)')
        page.screenshot(path=str(tmp_path/'gimpo-desktop.png'),full_page=True)
        page.click('#stop')
        page.wait_for_function("document.getElementById('state').textContent === '중지됨'")
        assert page.locator('#reprepare').count() == 0
        assert page.locator('#watch').is_enabled()
        assert not errors
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert page.locator('#header-status').is_visible()
        page.click('#log-fab')
        page.wait_for_function("document.getElementById('log-panel').classList.contains('open')")
        assert page.locator('#progress-card').is_visible()
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
        page.locator('#completion-overlay').get_by_role('button', name='닫기').click()
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


def test_defaults_restore_and_shared_calendar_controls(client, ui_server):
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
        assert page.locator('#log-body tr.log-row').count() == 1
        assert '1회 조회 결과: 만차입니다.' in page.locator('#log-body tr.log-row .body-cell').inner_text()
        assert page.get_by_text('1회 조회 결과: 만차입니다.', exact=False).count() == 1
        assert page.locator('#reason, #job-id, #reprepare').count() == 0
        assert page.locator('#job-actions').is_hidden()
        assert page.locator('#freshness').is_hidden()
        page.reload()
        page.wait_for_function("document.getElementById('state').textContent === '중지됨'")
        assert page.locator('#log-body tr.log-row').count() == 1
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


def test_password_fields_share_input_style(ui_server):
    base, runtime = ui_server
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("!document.getElementById('toggle-password').disabled")
        style = "el => { const s = getComputedStyle(el); return [s.height, s.backgroundColor, s.borderRadius]; }"
        assert page.locator('#reservationPassword').evaluate(style) == page.locator('#carNumber').evaluate(style)
        page.click('#toggle-password')
        assert page.locator('#reservationPassword').evaluate(style) == page.locator('#carNumber').evaluate(style)
        browser.close()


def test_settings_dialog_keeps_gimpo_inputs(ui_server):
    base, runtime = ui_server
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("!document.getElementById('check').disabled")
        page.fill('#carNumber', '123가4567')
        page.click('#open-settings')
        assert page.locator('#settings-dialog').is_visible()
        page.keyboard.press('Escape')
        assert not page.locator('#settings-dialog').is_visible()
        assert page.evaluate('document.activeElement.id') == 'open-settings'
        assert page.input_value('#carNumber') == '123가4567'
        assert page.url == base + '/gimpo-parking/'
        browser.close()


def test_missing_fields_show_field_errors_without_starting(ui_server):
    base, runtime = ui_server
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("!document.getElementById('check').disabled")
        page.fill('#carNumber', '')
        page.click('#watch')
        page.locator('.toast-error').first.wait_for()
        assert page.locator('#carNumber').evaluate("el => el.closest('.field-group').classList.contains('has-error')")
        assert runtime.store.active() is None
        browser.close()


def test_date_field_error_clears_on_picker_change(ui_server):
    base, runtime = ui_server
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("!document.getElementById('check').disabled")
        page.evaluate("document.getElementById('entryAt').closest('.field-group').classList.add('has-error')")
        page.evaluate("document.getElementById('entry-picker').dispatchEvent(new CustomEvent('vp.change', {bubbles: true}))")
        assert not page.evaluate("document.getElementById('entryAt').closest('.field-group').classList.contains('has-error')")
        browser.close()


PROGRESS_REASON = '공항 결제창에서 결제를 마친 뒤 예약 내역을 확인해주세요.'


class HeldClose(FakeBrowser):
    """결과 기록 뒤 브라우저 닫기를 붙잡아 '수락됨·아직 활성' 구간을 실제로 만든다."""
    gate = None

    async def close(self):
        if HeldClose.gate is not None:
            await asyncio.to_thread(HeldClose.gate.wait, 10)
        await super().close()


def open_gimpo(p, base, init_script=None, width=1280):
    browser = p.chromium.launch()
    page = browser.new_page(viewport={'width': width, 'height': 1000})
    page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
    if init_script:
        page.add_init_script(init_script)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    return browser, page, errors


def overlay_variant(page):
    return page.evaluate("(() => { const o = document.getElementById('completion-overlay'); return o.classList.contains('open') ? o.dataset.variant : null; })()")


def wait_polls(page, count=2):
    # 김포 화면은 1.5초마다 작업 상태를 읽는다. 고정 대기 대신 그 응답을 count번 받을 때까지 기다린다.
    for _ in range(count):
        with page.expect_response(lambda response: '/gimpo-parking/api/jobs/GMP-' in response.url
                                  and response.request.method == 'GET', timeout=10000):
            pass
    page.evaluate('new Promise(resolve => setTimeout(resolve, 50))')


def to_payment_progress(runtime, job_id):
    runtime.store.dispatch_payment(job_id, runtime.store.get(job_id))
    runtime.store.transition(job_id, 'PAYMENT_IN_PROGRESS', PROGRESS_REASON, expected={'PAYMENT_DISPATCHING'})


def test_payment_handoff_return_and_reserved_overlays(ui_server):
    base, runtime = ui_server
    HeldClose.gate = threading.Event()
    runtime.client_factory = HeldClose
    try:
        job = wait_state(runtime, runtime.create(inputs())['id'], READY)
        with sync_playwright() as p:
            browser, page, errors = open_gimpo(p, base)
            resolves = []
            page.on('request', lambda request: resolves.append(request.url) if request.url.endswith('/resolve') else None)
            page.goto(base + '/gimpo-parking/')
            page.wait_for_function("document.getElementById('completion-overlay').classList.contains('open')")
            overlay = page.locator('#completion-overlay')
            assert overlay_variant(page) == 'action'
            assert '결제해주세요' in overlay.inner_text()
            overlay.get_by_role('button', name='닫기').click()
            page.reload()
            page.wait_for_function("document.getElementById('state').textContent === '결제 대기'")
            wait_polls(page)
            assert overlay_variant(page) is None

            to_payment_progress(runtime, job['id'])
            runtime.store.mark_returned(job['id'])
            page.wait_for_function("document.getElementById('completion-overlay').classList.contains('open')")
            assert '결과를 선택하면 예약창을 닫습니다' in overlay.inner_text()
            overlay.get_by_role('button', name='아직 결제 중').click()
            wait_polls(page)
            assert overlay_variant(page) is None and resolves == []

            page.reload()
            page.wait_for_function("document.getElementById('completion-overlay').classList.contains('open')")
            overlay.get_by_role('button', name='예약 완료').click()
            eventually(lambda: runtime.store.get(job['id'])['state'] == 'CLOSED_BY_USER')
            wait_polls(page)  # 서버는 받아들였지만 브라우저 닫기가 붙잡혀 작업이 아직 활성인 구간
            assert runtime.store.get(job['id'])['active'] is True
            assert overlay_variant(page) is None
            assert len(resolves) == 1
            HeldClose.gate.set()
            eventually(lambda: not runtime.store.get(job['id'])['active'])
            page.wait_for_function("document.getElementById('completion-overlay').dataset.variant === 'success' && document.getElementById('completion-overlay').classList.contains('open')")
            assert runtime.store.get(job['id'])['userReportedOutcome'] == 'reserved'
            overlay.get_by_role('button', name='확인').click()
            page.reload()
            page.wait_for_function("document.getElementById('state').textContent === '종료됨'")
            wait_polls(page)
            assert overlay_variant(page) is None
            stored = page.evaluate('({...localStorage})')
            assert stored and all(key.startswith('gimpo.completion.' + job['id'] + '.') for key in stored)
            assert 'PrivatePass44' not in json.dumps(stored)
            assert not errors
            browser.close()
    finally:
        HeldClose.gate.set()
        HeldClose.gate = None


def test_result_choice_while_busy_is_not_recorded(ui_server):
    base, runtime = ui_server
    job = wait_state(runtime, runtime.create(inputs())['id'], READY)
    to_payment_progress(runtime, job['id'])
    runtime.store.mark_returned(job['id'])
    with sync_playwright() as p:
        browser, page, errors = open_gimpo(p, base)
        resolves = []
        page.on('request', lambda request: resolves.append(request.url) if request.url.endswith('/resolve') else None)
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("document.getElementById('completion-overlay').classList.contains('open')")
        page.evaluate('gimpoScreen.busy = true')
        page.locator('#completion-overlay').get_by_role('button', name='예약 완료').click()
        page.locator('.toast-info').first.wait_for()
        assert resolves == []
        assert not any(key.endswith('.result') for key in page.evaluate('Object.keys(localStorage)'))
        page.evaluate('gimpoScreen.busy = false')
        page.wait_for_function("document.getElementById('completion-overlay').classList.contains('open')")
        assert not errors
        browser.close()


def test_open_payment_overlay_is_replaced_when_state_moves_on(ui_server):
    base, runtime = ui_server
    job = wait_state(runtime, runtime.create(inputs())['id'], READY)
    with sync_playwright() as p:
        browser, page, errors = open_gimpo(p, base)
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("document.getElementById('completion-overlay').classList.contains('open')")
        to_payment_progress(runtime, job['id'])
        runtime.store.mark_returned(job['id'])
        page.wait_for_function("document.getElementById('completion-overlay').textContent.includes('결과를 선택하면')")
        assert overlay_variant(page) == 'action'
        assert not errors
        browser.close()


def test_payment_overlay_is_above_open_mobile_sheet(ui_server):
    base, runtime = ui_server
    with sync_playwright() as p:
        browser, page, errors = open_gimpo(p, base, width=390)
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("!document.getElementById('check').disabled")
        page.fill('#carNumber', '123가4567')
        page.fill('#phone', '01012345678')
        page.click('#watch')
        page.wait_for_function("document.getElementById('log-panel').classList.contains('open')")
        page.wait_for_function("document.getElementById('completion-overlay').classList.contains('open')")
        hit = page.evaluate("document.elementFromPoint(innerWidth / 2, innerHeight - 40).closest('#completion-overlay') !== null")
        assert hit
        assert page.evaluate("UI.layers.top() === document.getElementById('completion-overlay')")
        assert not errors
        browser.close()


def test_second_handoff_shows_payment_overlay_again(ui_server):
    base, runtime = ui_server
    job = wait_state(runtime, runtime.create(inputs())['id'], READY)
    with sync_playwright() as p:
        browser, page, errors = open_gimpo(p, base)
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("document.getElementById('completion-overlay').classList.contains('open')")
        page.locator('#completion-overlay').get_by_role('button', name='닫기').click()
        runtime.store.transition(job['id'], 'RECHECKING', '최종 확인 중')
        runtime.store.ready(job['id'], job['generation'], job['summary'], runtime.store.clock(), 120)
        page.wait_for_function("document.getElementById('completion-overlay').classList.contains('open')")
        assert overlay_variant(page) == 'action'
        assert not errors
        browser.close()


def test_reserved_overlay_waits_until_browser_is_closed(ui_server):
    base, runtime = ui_server

    class Stuck(FakeBrowser):
        fail = True

        async def close(self):
            if Stuck.fail:
                raise RuntimeError('close failed')
            await super().close()

    runtime.client_factory = Stuck
    try:
        job = wait_state(runtime, runtime.create(inputs())['id'], READY)
        to_payment_progress(runtime, job['id'])
        runtime.resolve(job['id'], runtime.store.get(job['id']), 'reserved', True)
        eventually(lambda: '다시 눌러주세요' in runtime.store.get(job['id'])['reason'])
        assert '결과 기록' in runtime.store.get(job['id'])['reason']
        with sync_playwright() as p:
            browser, page, errors = open_gimpo(p, base)
            page.goto(base + '/gimpo-parking/')
            page.wait_for_function("document.getElementById('state').textContent === '종료됨'")
            wait_polls(page)
            assert overlay_variant(page) != 'success'
            assert page.locator('#record-result').is_visible()
            assert not errors
            browser.close()
    finally:
        Stuck.fail = False  # 픽스처 정리(runtime.close)가 브라우저를 닫을 수 있게 한다


def test_old_reserved_job_is_not_celebrated_in_new_browser(ui_server):
    base, runtime = ui_server
    job = wait_state(runtime, runtime.create(inputs())['id'], READY)
    to_payment_progress(runtime, job['id'])
    runtime.resolve(job['id'], runtime.store.get(job['id']), 'reserved', True)
    eventually(lambda: not runtime.store.get(job['id'])['active'])
    with sync_playwright() as p:
        browser, page, errors = open_gimpo(p, base)
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("document.getElementById('state').textContent === '종료됨'")
        wait_polls(page)
        assert overlay_variant(page) is None
        assert not errors
        browser.close()


def test_completion_overlay_works_when_storage_is_blocked(ui_server):
    base, runtime = ui_server
    wait_state(runtime, runtime.create(inputs())['id'], READY)
    blocked = "Object.defineProperty(window, 'localStorage', {get() { throw new DOMException('blocked', 'SecurityError'); }});"
    with sync_playwright() as p:
        browser, page, errors = open_gimpo(p, base, init_script=blocked)
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("document.getElementById('completion-overlay').classList.contains('open')")
        page.locator('#completion-overlay').get_by_role('button', name='닫기').click()
        wait_polls(page)
        assert overlay_variant(page) is None
        assert page.locator('#header-status-text').inner_text() != '연결 끊김'
        assert not errors
        browser.close()


def test_progress_card_shows_disconnection_and_recovers(ui_server):
    base, runtime = ui_server
    job = wait_state(runtime, runtime.create(inputs())['id'], READY)
    with sync_playwright() as p:
        browser, page, errors = open_gimpo(p, base)
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("document.getElementById('state').textContent === '결제 대기'")
        status_url = '**/api/jobs/' + job['id']
        page.route(status_url, lambda route: route.fulfill(status=503, body=''))
        page.wait_for_function("document.getElementById('state').textContent === '연결 끊김'")
        assert page.locator('#state').get_attribute('data-tone') == 'error'
        assert page.locator('#header-status-text').inner_text() == '연결 끊김'
        # 폴링이 아닌 렌더(명령 처리 등)도 다음 폴링이 성공하기 전까지는 연결 끊김을 유지한다.
        page.evaluate('gimpoScreen.render()')
        assert page.locator('#state').inner_text() == '연결 끊김'
        assert page.locator('#state').get_attribute('data-tone') == 'error'
        assert page.locator('#header-status-text').inner_text() == '연결 끊김'
        assert page.locator('#header-status').get_attribute('data-tone') == 'error'
        page.unroute(status_url)
        page.wait_for_function("document.getElementById('state').textContent === '결제 대기'")
        assert page.locator('#state').get_attribute('data-tone') == 'warning'
        assert page.locator('#header-status-text').inner_text() == '결제 대기'
        assert page.locator('#header-status').get_attribute('data-tone') == 'warning'
        assert not errors
        browser.close()


def test_import_t2_failure_without_server_message_shows_fallback_text(ui_server):
    base, runtime = ui_server
    with sync_playwright() as p:
        browser, page, errors = open_gimpo(p, base)
        page.route('**/t2-valet/api/defaults', lambda route: route.fulfill(status=500, body=''))
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("!document.getElementById('check').disabled")
        page.click('#import-t2')
        page.locator('.toast-error').first.wait_for()
        assert 'T2 저장 정보를 불러오지 못했습니다.' in page.locator('.toast-error').first.inner_text()
        assert not errors
        browser.close()


def test_import_t2_failure_prefers_server_message(ui_server):
    base, runtime = ui_server
    message = 'T2 설정 파일을 읽을 수 없습니다.'
    with sync_playwright() as p:
        browser, page, errors = open_gimpo(p, base)
        page.route('**/t2-valet/api/defaults', lambda route: route.fulfill(status=500, json={'error': message}))
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("!document.getElementById('check').disabled")
        page.click('#import-t2')
        page.locator('.toast-error').first.wait_for()
        text = page.locator('.toast-error').first.inner_text()
        assert message in text and 'T2 저장 정보를 불러오지 못했습니다.' not in text
        assert not errors
        browser.close()


@pytest.mark.parametrize('change', [None, 'ends', 'hides_record_result'])
def test_user_opened_result_choice_while_busy_is_offered_again(ui_server, change):
    base, runtime = ui_server
    job = wait_state(runtime, runtime.create(inputs())['id'], READY)
    runtime.store.dispatch_payment(job['id'], runtime.store.get(job['id']))
    runtime.store.transition(job['id'], 'PAYMENT_RESULT_UNKNOWN', 'test', expected={'PAYMENT_DISPATCHING'})
    with sync_playwright() as p:
        browser, page, errors = open_gimpo(p, base)
        resolves = []
        page.on('request', lambda request: resolves.append(request.url) if request.url.endswith('/resolve') else None)
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("document.getElementById('state').textContent === '예약 결과 확인 필요'")
        assert overlay_variant(page) is None
        page.click('#record-result')
        page.wait_for_function("document.getElementById('completion-overlay').classList.contains('open')")
        page.evaluate('gimpoScreen.busy = true')
        page.locator('#completion-overlay').get_by_role('button', name='예약 완료').click()
        page.locator('.toast-info').first.wait_for()
        assert overlay_variant(page) is None
        assert not any(key.endswith('.result') for key in page.evaluate('Object.keys(localStorage)'))
        if change == 'ends':
            runtime.resolve(job['id'], runtime.store.get(job['id']), 'not_reserved', True)
            eventually(lambda: not runtime.store.get(job['id'])['active'])
        elif change == 'hides_record_result':
            # 활성인 채로 결과 기록 버튼이 숨는 상태로 옮긴다.
            runtime.store.transition(job['id'], 'REVIEW_REQUIRED', 'test', expected={'PAYMENT_RESULT_UNKNOWN'})
            page.wait_for_function("document.getElementById('record-result').hidden")
            assert runtime.store.get(job['id'])['active'] is True
        wait_polls(page)
        assert overlay_variant(page) is None  # busy인 동안에는 다시 열지 않는다
        page.evaluate('gimpoScreen.busy = false')
        wait_polls(page)
        if change:
            assert overlay_variant(page) is None
        else:
            assert overlay_variant(page) == 'action'
            assert '예약 결과를 기록해주세요' in page.locator('#completion-overlay').inner_text()
        assert resolves == []
        assert not errors
        browser.close()


def test_completion_keys_of_other_jobs_are_pruned(ui_server):
    base, runtime = ui_server
    job = wait_state(runtime, runtime.create(inputs())['id'], READY)
    current = f"gimpo.completion.{job['id']}.{job['generation']}.{job['handoffEpoch']}.action"
    seeded = [current, 'gimpo.completion.GMP-old.1.1.success',
              f"gimpo.completion.{job['id']}.{job['generation'] - 1}.{job['handoffEpoch']}.result",
              f"gimpo.completion.{job['id']}.{job['generation']}.{job['handoffEpoch'] - 1}.action"]
    # 페이지를 열 때 한 번만 심는다(다시 불러와도 되살아나지 않게 sessionStorage로 표시).
    seed = ("if (!sessionStorage.getItem('seeded')) { sessionStorage.setItem('seeded', '1'); "
            f"for (const key of {json.dumps(seeded)}) localStorage.setItem(key, '1'); localStorage.setItem('other.key', 'x'); }}")
    with sync_playwright() as p:
        browser, page, errors = open_gimpo(p, base, init_script=seed)
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("document.getElementById('state').textContent === '결제 대기'")
        wait_polls(page)
        assert sorted(page.evaluate('Object.keys(localStorage)')) == sorted([current, 'other.key'])
        assert overlay_variant(page) is None  # 현재 작업 기록은 남아 결제 안내를 다시 띄우지 않는다
        assert not errors
        browser.close()
