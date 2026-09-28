import json
import re
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import expect

from app import app
from services.gimpo.config import CONFIG
from services.gimpo.fee import FeeUnavailable
from services.gimpo.jobs import GimpoRuntime
from services.gimpo.parking import GimpoService
from tests.support.ui import open_page, run_app_server
from tests.gimpo.fakes import FakeBrowser, FakeFeeClient, FakeNotifier
from tests.gimpo.helpers import inputs, wait_state
from tests.support.waiting import eventually
from services.gimpo.store import READY


@pytest.fixture
def ui_server(client, ui_intervals, tmp_path, monkeypatch):
    monkeypatch.setenv('RESERVATION_PASSWORD', 'PrivatePass44')
    runtime=GimpoRuntime(replace(CONFIG,directory=tmp_path/'data'),FakeBrowser,notifier=FakeNotifier())
    service=GimpoService(runtime.config);service._runtime=runtime;service.fee=FakeFeeClient();runtime.fee=service.fee
    monkeypatch.setitem(app.extensions,'gimpo',service)
    with run_app_server(app, runtime=runtime) as base:
        yield base, runtime


def test_fee_row_shows_estimate_and_refetches_on_period_change(ui_server, ui_context):
    base, runtime = ui_server
    with open_page(ui_context, base) as (page, errors):
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("() => !document.getElementById('check').disabled")
        # 기본 입차·출차·주차장·할인은 이미 유효하므로 페이지가 뜨자마자 가짜 요금(8000-1600)을 조회한다.
        page.wait_for_function("() => document.getElementById('fee-estimated').textContent === '6,400원'")
        assert len(runtime.fee.calls) == 1
        first_exit_at = page.input_value('#exitAt')
        next_exit_at = (datetime.strptime(first_exit_at, '%Y-%m-%d %H:%M') + timedelta(minutes=10)).strftime('%Y-%m-%d %H:%M')
        page.evaluate("""value => {
            const exit = document.getElementById('exitAt');
            exit.value = value;
            exit.dispatchEvent(new Event('change', {bubbles: true}));
        }""", next_exit_at)
        # 500ms 디바운스 뒤 다시 조회한다: "계산 중"을 거쳐 값이 다시 채워지는 것으로 재조회를 확인한다
        # (같은 가짜 요금이라 값 자체는 그대로지만, 상태를 오가는 것이 재조회의 증거다).
        page.wait_for_function("() => document.getElementById('fee-estimated').textContent === '계산 중'")
        page.wait_for_function("() => document.getElementById('fee-estimated').textContent === '6,400원'")
        assert len(runtime.fee.calls) == 2
        assert runtime.fee.calls[-1]['exitAt'] == next_exit_at
        assert not errors


def test_fee_row_shows_unavailable_when_fee_lookup_fails(ui_server, ui_context):
    base, runtime = ui_server
    runtime.fee.error = FeeUnavailable('공항 요금을 확인할 수 없습니다.')
    with open_page(ui_context, base) as (page, errors):
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("() => !document.getElementById('check').disabled")
        page.wait_for_function("() => document.getElementById('fee-estimated').textContent === '확인 불가'")
        assert not errors


def test_progress_card_has_no_summary_element_and_title_is_progress(ui_server, ui_context):
    base, runtime = ui_server
    with open_page(ui_context, base) as (page, errors):
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("() => !document.getElementById('check').disabled")
        assert page.locator('#summary').count() == 0
        assert page.locator('#status-title').inner_text() == '진행 상황'
        assert page.locator('#fee-deposit').inner_text() == '예약 정보 입력 때 확인'
        assert not errors


def test_form_to_handoff_refresh_and_stop(ui_server, ui_context):
    base,runtime=ui_server
    with ui_context(timezone_id='America/New_York', viewport={'width':1280,'height':1000}) as context:
        context.route('**/*',lambda route: route.continue_() if route.request.url.startswith(base+'/') else route.abort())
        page=context.new_page();errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(base+'/gimpo-parking/')
        page.wait_for_function("() => !document.getElementById('check').disabled")
        page.fill('#carNumber','123가4567');page.fill('#phone','01012345678')
        assert page.locator('#agree01, #agree03, #agree04, #agree05, #autoProceedConsent').count() == 0
        page.click('#watch')
        page.wait_for_function("() => document.getElementById('completion-overlay').classList.contains('open')")
        page.locator('#completion-overlay').get_by_role('button', name='닫기').click()
        page.wait_for_function("() => document.getElementById('header-status-text').textContent === '결제 대기'")
        job_id=runtime.store.active()['id']
        page.reload()
        page.wait_for_function("() => document.getElementById('header-status-text').textContent === '결제 대기'")
        assert page.locator('#header-status-text').inner_text() == '결제 대기'
        assert page.locator('#header-status').get_attribute('data-tone') == 'warning'
        assert runtime.store.active()['id']==job_id
        assert len(runtime.store.events())==1
        assert page.locator('#reservationPassword').get_attribute('type') == 'password'
        assert page.locator('#passwordConfirmation').get_attribute('type') == 'password'
        assert page.locator('#show-browser').is_visible()
        stored = page.evaluate('({...localStorage})')
        assert stored
        assert all(key.startswith('gimpo.completion.') for key in stored)
        assert '123가4567' not in json.dumps(stored, ensure_ascii=False)
        page.click('#stop')
        page.wait_for_function("() => document.getElementById('header-status-text').textContent === '중지됨'")
        assert page.locator('#reprepare').count() == 0
        assert page.locator('#watch').is_enabled()
        assert not errors
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert page.locator('#header-status').is_visible()
        page.click('#log-fab')
        page.wait_for_function("() => document.getElementById('log-panel').classList.contains('open')")
        assert page.locator('#progress-card').is_visible()


def test_delayed_old_status_cannot_replace_new_work(ui_server, ui_context):
    base, runtime = ui_server
    old = runtime.create(inputs())
    old = wait_state(runtime, old['id'], READY)
    runtime.stop(old['id'], old)
    eventually(lambda: not runtime.store.get(old['id'])['active'])
    snapshot = {'job': runtime.store.get(old['id']), 'notifications': [], 'browserAvailable': False}
    with ui_context() as context:
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
        page.wait_for_function("() => document.getElementById('header-status-text').textContent === '중지됨'")
        page.fill('#carNumber', '123가4567')
        page.fill('#phone', '01012345678')
        page.click('#watch')
        page.wait_for_function("() => document.getElementById('check').disabled && document.getElementById('header-status-text').textContent !== '중지됨'")
        current = runtime.store.active()
        assert current and current['id'] != old['id']
        assert len(delayed) == 1
        delayed[0].fulfill(json=snapshot)
        page.wait_for_timeout(100)  # Allow the delayed response's JS continuation to run.
        assert page.locator('#check').is_disabled()
        page.wait_for_function("() => document.getElementById('header-status-text').textContent === '결제 대기'")
        page.locator('#completion-overlay').get_by_role('button', name='닫기').click()
        assert page.locator('#stop').is_visible()
        assert page.locator('#show-browser').is_visible()


@pytest.mark.parametrize('zone',['Asia/Seoul','America/New_York','Pacific/Honolulu'])
def test_calendar_wall_time_and_t2_default(zone, ui_context):
    script=Path('static/js/datepicker.js').read_text()
    with ui_context(timezone_id=zone) as context:
        page = context.new_page()
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


@pytest.mark.parametrize('zone', ['Asia/Seoul', 'America/New_York'])
def test_shared_time_picker_uses_wall_time_and_minute_step(zone, ui_context):
    with ui_context(timezone_id=zone) as context:
        page = context.new_page()
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
        page.wait_for_function("() => document.querySelectorAll('.td-scroll-item.active').length === 2")
        assert page.locator('.td-scroll-item.active').all_text_contents() == ['12', '20']
        assert columns.nth(1).locator('[data-value]').all_text_contents() == ['00', '10', '20', '30', '40', '50']
        columns.nth(0).evaluate('(el) => { el.scrollTop = 14 * 36; }')
        page.wait_for_function("() => document.querySelector('#g input').value === '2026-09-28 14:20'")
        assert page.locator('.td-icon-prev').evaluate("el => getComputedStyle(el, '::before').content") == '""'
        page.set_viewport_size({'width': 390, 'height': 844})
        page.evaluate('picker.hide(); picker.show()')
        page.wait_for_function("() => getComputedStyle(picker._widget).flexDirection === 'column'")
        assert page.locator('.td-scroll-time').is_visible()


def test_defaults_restore_and_shared_calendar_controls(client, ui_server, ui_context):
    base, runtime = ui_server
    with ui_context() as context:
        page = context.new_page()
        page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("() => !document.getElementById('check').disabled")
        page.fill('#carNumber', '123가4567')
        page.fill('#phone', '01012345678')
        page.select_option('#discountSelection', 'DC007')
        page.wait_for_function("() => fetch('/gimpo-parking/api/defaults').then(r => r.json()).then(d => d.phone === '01012345678' && d.discountSelection === 'DC007')")
        page.reload()
        page.wait_for_function("() => !document.getElementById('check').disabled")
        assert page.input_value('#carNumber') == '123가4567'
        assert page.input_value('#phone') == '01012345678'
        assert page.input_value('#discountSelection') == 'DC007'
        assert page.locator('#saveConsent, #load, #save, #date-policy, .handoff-note').count() == 0
        assert page.locator('label[for="entryAt"]').inner_text() == '입차시간'
        assert page.locator('label[for="exitAt"]').inner_text() == '출차시간'
        assert page.locator('#entry-picker .td-toggle .material-symbols-outlined').inner_text() == 'calendar_today'
        page.click('#entry-picker .td-toggle')
        page.wait_for_function("() => document.querySelector('.tempus-dominus-widget.show .td-scroll-time') !== null")
        assert page.locator('.tempus-dominus-widget.show .td-scroll-time').is_visible()
        page.goto(base + '/t2-valet/')
        assert page.locator('#departingAtPicker .td-toggle .material-symbols-outlined').inner_text() == 'calendar_today'


def test_environment_password_mask_and_reveal(ui_server, ui_context):
    base, runtime = ui_server
    with ui_context() as context:
        page = context.new_page()
        page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("() => !document.getElementById('toggle-password').disabled")
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
        page.wait_for_function("() => !document.getElementById('toggle-password').disabled")
        assert field.get_attribute('type') == 'password'


def test_once_full_displays_one_result_row(ui_server, ui_context):
    base, runtime = ui_server
    class Full(FakeBrowser):
        available = False
    runtime.client_factory = Full
    with ui_context() as context:
        page = context.new_page()
        page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("() => !document.getElementById('check').disabled")
        page.fill('#carNumber', '123가4567')
        page.fill('#phone', '01012345678')
        page.click('#check')
        page.wait_for_function("() => document.getElementById('header-status-text').textContent === '중지됨'")
        assert page.locator('#log-body tr.log-row').count() == 1
        assert '1회 조회 결과: 만차입니다.' in page.locator('#log-body tr.log-row .body-cell').inner_text()
        assert page.get_by_text('1회 조회 결과: 만차입니다.', exact=False).count() == 1
        assert page.locator('#reason, #job-id, #reprepare').count() == 0
        assert page.locator('#job-actions').is_hidden()
        assert page.locator('#job-notice').is_hidden()
        page.reload()
        page.wait_for_function("() => document.getElementById('header-status-text').textContent === '중지됨'")
        assert page.locator('#log-body tr.log-row').count() == 1


def test_saved_interval_survives_reload_with_previous_job(ui_server, ui_context):
    base, runtime = ui_server
    job = runtime.create(inputs())
    job = wait_state(runtime, job['id'], READY)
    runtime.stop(job['id'], job)
    eventually(lambda: not runtime.store.get(job['id'])['active'])
    with ui_context() as context:
        page = context.new_page()
        page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("() => !document.getElementById('check').disabled")
        page.fill('#intervalSeconds', '60')
        page.locator('#intervalSeconds').blur()
        page.wait_for_function("() => fetch('/gimpo-parking/api/defaults').then(r => r.json()).then(d => d.intervalSeconds === 60)")
        page.reload()
        page.wait_for_function("() => !document.getElementById('check').disabled")
        assert page.input_value('#intervalSeconds') == '60'
        assert runtime.store.get_defaults()['intervalSeconds'] == 60


@pytest.mark.parametrize('saved_dates', [True, False])
def test_dates_restore_from_defaults_or_legacy_job(ui_server, saved_dates, ui_context):
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
    with ui_context() as context:
        page = context.new_page()
        page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("() => !document.getElementById('check').disabled")
        assert page.input_value('#entryAt') == raw['entryAt']
        assert page.input_value('#exitAt') == raw['exitAt']
        assert page.input_value('#intervalSeconds') == '60'
        # Calendar/time widgets emit vp.change instead of native change.
        page.locator('#exit-picker').dispatch_event('vp.change')
        page.wait_for_function("() => fetch('/gimpo-parking/api/defaults').then(r => r.json()).then(d => !!d.entryAt && !!d.exitAt)")
        assert runtime.store.get_defaults()['entryAt'] == raw['entryAt']
        page.reload()
        page.wait_for_function("() => !document.getElementById('check').disabled")
        assert page.input_value('#exitAt') == raw['exitAt']


def test_expired_saved_dates_use_current_booking_range(ui_server, ui_context):
    base, runtime = ui_server
    runtime.store.save_defaults({'entryAt': '2000-01-01 10:00', 'exitAt': '2000-01-01 14:00', 'intervalSeconds': 60})
    with ui_context() as context:
        page = context.new_page()
        page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("() => !document.getElementById('check').disabled")
        assert not page.input_value('#entryAt').startswith('2000-')
        assert not page.input_value('#exitAt').startswith('2000-')
        assert page.input_value('#intervalSeconds') == '60'


def test_password_fields_share_input_style(ui_server, ui_context):
    base, runtime = ui_server
    with ui_context() as context:
        page = context.new_page()
        page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("() => !document.getElementById('toggle-password').disabled")
        style = "el => { const s = getComputedStyle(el); return [s.height, s.backgroundColor, s.borderRadius]; }"
        assert page.locator('#reservationPassword').evaluate(style) == page.locator('#carNumber').evaluate(style)
        page.click('#toggle-password')
        assert page.locator('#reservationPassword').evaluate(style) == page.locator('#carNumber').evaluate(style)


def test_missing_fields_show_field_errors_without_starting(ui_server, ui_context):
    base, runtime = ui_server
    with ui_context() as context:
        page = context.new_page()
        page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("() => !document.getElementById('check').disabled")
        page.fill('#carNumber', '')
        page.click('#watch')
        page.locator('.toast-error').first.wait_for()
        assert page.locator('#carNumber').evaluate("el => el.closest('.field-group').classList.contains('has-error')")
        assert runtime.store.active() is None


def test_date_field_error_clears_on_picker_change(ui_server, ui_context):
    base, runtime = ui_server
    with ui_context() as context:
        page = context.new_page()
        page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("() => !document.getElementById('check').disabled")
        page.evaluate("document.getElementById('entryAt').closest('.field-group').classList.add('has-error')")
        page.evaluate("document.getElementById('entry-picker').dispatchEvent(new CustomEvent('vp.change', {bubbles: true}))")
        assert not page.evaluate("document.getElementById('entryAt').closest('.field-group').classList.contains('has-error')")


PROGRESS_REASON = '공항 결제창에서 결제를 마친 뒤 예약 내역을 확인해주세요.'


def overlay_variant(page):
    return page.evaluate("(() => { const o = document.getElementById('completion-overlay'); return o.classList.contains('open') ? o.dataset.variant : null; })()")


def wait_polls(page, count=2):
    # 김포 화면은 설정한 주기(테스트는 UI_INTERVALS)마다 작업 상태를 읽는다. 고정 대기 대신 그 응답을 count번 받을 때까지 기다린다.
    for _ in range(count):
        with page.expect_response(lambda response: '/gimpo-parking/api/jobs/GMP-' in response.url
                                  and response.request.method == 'GET', timeout=10000):
            pass
    page.evaluate('new Promise(resolve => setTimeout(resolve, 50))')


def to_payment_progress(runtime, job_id):
    runtime.store.dispatch_payment(job_id, runtime.store.get(job_id))
    runtime.store.transition(job_id, 'PAYMENT_IN_PROGRESS', PROGRESS_REASON, expected={'PAYMENT_DISPATCHING'})


def test_open_payment_overlay_is_replaced_when_state_moves_on(ui_server, ui_context):
    base, runtime = ui_server
    job = wait_state(runtime, runtime.create(inputs())['id'], READY)
    with open_page(ui_context, base) as (page, errors):
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("() => document.getElementById('completion-overlay').classList.contains('open')")
        to_payment_progress(runtime, job['id'])
        runtime.store.mark_returned(job['id'])
        page.wait_for_function("() => !document.getElementById('completion-overlay').classList.contains('open')")
        assert overlay_variant(page) is None
        assert not errors


def test_second_handoff_shows_payment_overlay_again(ui_server, ui_context):
    base, runtime = ui_server
    job = wait_state(runtime, runtime.create(inputs())['id'], READY)
    with open_page(ui_context, base) as (page, errors):
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("() => document.getElementById('completion-overlay').classList.contains('open')")
        page.locator('#completion-overlay').get_by_role('button', name='닫기').click()
        runtime.store.transition(job['id'], 'RECHECKING', '최종 확인 중')
        runtime.store.ready(job['id'], job['generation'], job['summary'], runtime.store.clock(), 120)
        page.wait_for_function("() => document.getElementById('completion-overlay').classList.contains('open')")
        assert overlay_variant(page) == 'action'
        assert not errors


def test_completion_overlay_works_when_storage_is_blocked(ui_server, ui_context):
    base, runtime = ui_server
    wait_state(runtime, runtime.create(inputs())['id'], READY)
    blocked = "Object.defineProperty(window, 'localStorage', {get() { throw new DOMException('blocked', 'SecurityError'); }});"
    with open_page(ui_context, base, init_script=blocked) as (page, errors):
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("() => document.getElementById('completion-overlay').classList.contains('open')")
        page.locator('#completion-overlay').get_by_role('button', name='닫기').click()
        wait_polls(page)
        assert overlay_variant(page) is None
        assert page.locator('#header-status-text').inner_text() != '연결 끊김'
        assert not errors


def test_progress_card_shows_disconnection_and_recovers(ui_server, ui_context):
    base, runtime = ui_server
    job = wait_state(runtime, runtime.create(inputs())['id'], READY)
    with open_page(ui_context, base) as (page, errors):
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("() => document.getElementById('header-status-text').textContent === '결제 대기'")
        status_url = '**/api/jobs/' + job['id']
        page.route(status_url, lambda route: route.fulfill(status=503, body=''))
        page.wait_for_function("() => document.getElementById('header-status-text').textContent === '연결 끊김'")
        assert page.locator('#header-status').get_attribute('data-tone') == 'error'
        assert page.locator('#header-status-text').inner_text() == '연결 끊김'
        # 폴링이 아닌 렌더(명령 처리 등)도 다음 폴링이 성공하기 전까지는 연결 끊김을 유지한다.
        page.evaluate('gimpoScreen.render()')
        assert page.locator('#header-status-text').inner_text() == '연결 끊김'
        assert page.locator('#header-status').get_attribute('data-tone') == 'error'
        assert page.locator('#header-status-text').inner_text() == '연결 끊김'
        assert page.locator('#header-status').get_attribute('data-tone') == 'error'
        page.unroute(status_url)
        page.wait_for_function("() => document.getElementById('header-status-text').textContent === '결제 대기'")
        assert page.locator('#header-status').get_attribute('data-tone') == 'warning'
        assert page.locator('#header-status-text').inner_text() == '결제 대기'
        assert page.locator('#header-status').get_attribute('data-tone') == 'warning'
        assert not errors


def test_successful_command_clears_disconnection_before_next_poll(ui_server, ui_context):
    base, runtime = ui_server
    job = wait_state(runtime, runtime.create(inputs())['id'], READY)
    with open_page(ui_context, base) as (page, errors):
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("() => document.getElementById('header-status-text').textContent === '결제 대기'")
        page.locator('#completion-overlay').get_by_role('button', name='닫기').click()
        # 상태 조회(폴링) 엔드포인트만 끊는다. 명령 엔드포인트는 계속 정상 응답한다.
        page.route('**/api/jobs/' + job['id'], lambda route: route.fulfill(status=503, body=''))
        page.wait_for_function("() => document.getElementById('header-status-text').textContent === '연결 끊김'")
        page.click('#stop')
        # 중지 명령은 STOPPING으로 즉시 응답하고(최종 STOPPED 전환은 폴링으로만 확인되지만, 폴링은 계속 막혀 있다),
        # 그 응답이 성공했다는 것만으로 다음 폴링을 기다리지 않고 곧바로 연결 끊김을 벗어나야 한다.
        page.wait_for_function("() => document.getElementById('header-status-text').textContent === '중지 중'")
        assert page.locator('#header-status').get_attribute('data-tone') != 'error'
        assert page.locator('#header-status-text').inner_text() == '중지 중'
        assert page.locator('#header-status').get_attribute('data-tone') != 'error'
        assert not errors


def test_import_t2_failure_without_server_message_shows_fallback_text(ui_server, ui_context):
    base, runtime = ui_server
    with open_page(ui_context, base) as (page, errors):
        page.route('**/t2-valet/api/defaults', lambda route: route.fulfill(status=500, body=''))
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("() => !document.getElementById('check').disabled")
        page.click('#import-t2')
        page.locator('.toast-error').first.wait_for()
        assert 'T2 저장 정보를 불러오지 못했습니다.' in page.locator('.toast-error').first.inner_text()
        assert not errors


def test_import_t2_failure_prefers_server_message(ui_server, ui_context):
    base, runtime = ui_server
    message = 'T2 설정 파일을 읽을 수 없습니다.'
    with open_page(ui_context, base) as (page, errors):
        page.route('**/t2-valet/api/defaults', lambda route: route.fulfill(status=500, json={'error': message}))
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("() => !document.getElementById('check').disabled")
        page.click('#import-t2')
        page.locator('.toast-error').first.wait_for()
        text = page.locator('.toast-error').first.inner_text()
        assert message in text and 'T2 저장 정보를 불러오지 못했습니다.' not in text
        assert not errors


def test_completion_keys_of_other_jobs_are_pruned(ui_server, ui_context):
    base, runtime = ui_server
    job = wait_state(runtime, runtime.create(inputs())['id'], READY)
    current = f"gimpo.completion.{job['id']}.{job['generation']}.{job['handoffEpoch']}.action"
    seeded = [current, 'gimpo.completion.GMP-old.1.1.success',
              f"gimpo.completion.{job['id']}.{job['generation'] - 1}.{job['handoffEpoch']}.result",
              f"gimpo.completion.{job['id']}.{job['generation']}.{job['handoffEpoch'] - 1}.action"]
    # 페이지를 열 때 한 번만 심는다(다시 불러와도 되살아나지 않게 sessionStorage로 표시).
    seed = ("if (!sessionStorage.getItem('seeded')) { sessionStorage.setItem('seeded', '1'); "
            f"for (const key of {json.dumps(seeded)}) localStorage.setItem(key, '1'); localStorage.setItem('other.key', 'x'); }}")
    with open_page(ui_context, base, init_script=seed) as (page, errors):
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("() => document.getElementById('header-status-text').textContent === '결제 대기'")
        wait_polls(page)
        assert sorted(page.evaluate('Object.keys(localStorage)')) == sorted([current, 'other.key'])
        assert overlay_variant(page) is None  # 현재 작업 기록은 남아 결제 안내를 다시 띄우지 않는다
        assert not errors


def test_completion_overlay_stays_closed_after_reload(ui_server, ui_context):
    base, runtime = ui_server
    job = wait_state(runtime, runtime.create(inputs())['id'], READY)
    with open_page(ui_context, base) as (page, errors):
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("() => document.getElementById('completion-overlay').classList.contains('open')")
        page.locator('#completion-overlay').get_by_role('button', name='닫기').click()
        page.reload()
        page.wait_for_function("() => document.getElementById('header-status-text').textContent === '결제 대기'")
        wait_polls(page)
        assert overlay_variant(page) is None
        stored = page.evaluate('() => Object.entries(localStorage)')
        assert stored
        assert all(key.startswith(f"gimpo.completion.{job['id']}.") for key, _ in stored)
        assert 'PrivatePass44' not in json.dumps(stored, ensure_ascii=False)  # 키와 값 모두
        assert not errors


def test_log_time_keeps_date_and_empty_progress_card_is_hidden(ui_server, ui_context):
    base, runtime = ui_server
    with open_page(ui_context, base) as (page, errors):
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("() => !document.getElementById('check').disabled")
        # 작업이 없으면 안내 띠·작업 버튼·알림 줄이 모두 없으므로 카드를 숨긴다.
        assert not page.locator('#progress-card').is_visible()
        page.fill('#carNumber', '123가4567')
        page.fill('#phone', '01012345678')
        page.click('#check')
        page.wait_for_function("() => document.getElementById('header-status-text').textContent === '예약 가능'")
        assert page.locator('#progress-card').is_visible()
        # 실행이 여러 날에 걸칠 수 있으므로 시간에 날짜를 함께 표시한다.
        times = page.locator('#log-body .cell-time').all_inner_texts()
        assert times and all(re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', t) for t in times)
        assert page.locator('#log-body .cell-time').first.evaluate('e => e.scrollWidth <= e.clientWidth')
        assert all(h < 30 for h in page.locator('#log-body .cell-status').evaluate_all('els => els.map(e => e.getBoundingClientRect().height)'))
        assert not errors


def request_gaps(page, path, count):
    # 브라우저의 Resource Timing으로 같은 경로 요청들의 시작 간격(ms)을 잰다(파이썬 쪽 이벤트 지연이 섞이지 않는다).
    page.wait_for_function("([path, count]) => performance.getEntriesByType('resource')"
                           ".filter(entry => new URL(entry.name).pathname === path).length >= count",
                           arg=[path, count + 1], timeout=10000)
    return page.evaluate("""path => {
        const starts = performance.getEntriesByType('resource')
            .filter(entry => new URL(entry.name).pathname === path).map(entry => entry.startTime);
        return starts.slice(1).map((start, index) => start - starts[index]);
    }""", path)


def test_screens_poll_at_configured_intervals(ui_context, ui_server, monkeypatch):
    # 운영 기본값(1500/2000/3000)이 아니라 앱 설정값을 따르는지 본다. 세 값을 서로·기본값과 다르게 둔다.
    base, runtime = ui_server
    configured = {'GIMPO_POLL_MS': 300, 'T2_POLL_MS': 400, 'SETTINGS_REFRESH_MS': 500}
    for key, value in configured.items():
        monkeypatch.setitem(app.config, key, value)
    job = wait_state(runtime, runtime.create(inputs())['id'], READY)

    def assert_cadence(gaps, interval, default):
        # 다음 요청은 응답을 받은 뒤 interval만큼 뒤에 예약되므로 간격은 interval 이상이고, 기본값보다 훨씬 짧다.
        gaps = sorted(gaps)
        assert gaps[0] >= interval * .9, gaps
        assert gaps[len(gaps) // 2] < interval + 400 < default, gaps

    with open_page(ui_context, base) as (page, errors):
        page.goto(base + '/gimpo-parking/')
        assert page.evaluate('({...document.body.dataset})') == {
            'gimpoPollMs': '300', 't2PollMs': '400', 'settingsRefreshMs': '500'}
        assert_cadence(request_gaps(page, f"/gimpo-parking/api/jobs/{job['id']}", 3), 300, 1500)
        page.evaluate('UI.settings.open()')  # 결제 안내 레이어가 떠 있어도 설정 팝업을 연다
        assert_cadence(request_gaps(page, '/t2-valet/api/notifications/status', 3), 500, 3000)
        assert not errors
    with open_page(ui_context, base) as (page, errors):
        # T2 화면은 실행 중일 때만 폴링한다. 스케줄러를 돌리지 않고 로그 응답만 실행 중으로 준다.
        page.route('**/t2-valet/api/logs', lambda route: route.fulfill(json={'running': True, 'logs': []}))
        page.goto(base + '/t2-valet/')
        assert_cadence(request_gaps(page, '/t2-valet/api/logs', 3), 400, 2000)
        assert not errors


NOTICE = "() => document.getElementById('job-notice-text').textContent"


def notice(page):
    return page.evaluate(NOTICE)


def test_notice_strip_follows_payment_flow(ui_server, ui_context):
    base, runtime = ui_server
    runtime.completion_hold_sec = 1  # 닫기 대기를 줄여 끝난 모습까지 본다
    job = wait_state(runtime, runtime.create(inputs())['id'], READY)
    with open_page(ui_context, base) as (page, errors):
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("() => document.getElementById('header-status-text').textContent === '결제 대기'")
        page.locator('#completion-overlay').get_by_role('button', name='닫기').click()
        assert page.locator('#job-notice').get_attribute('data-tone') == 'warn'
        assert notice(page).endswith('까지 공항 예약창에서 결제를 진행해주세요. 자리는 아직 확보되지 않았습니다.')
        expect(page.locator('#job-notice #show-browser')).to_be_visible()  # 첫 폴링(browserAvailable) 뒤에 보인다
        assert page.locator('#fee-deposit').inner_text() == '10,000원'  # 가장 최근 작업 요약의 depositAmt
        runtime.store.dispatch_payment(job['id'], job)
        runtime.store.transition(job['id'], 'PAYMENT_IN_PROGRESS', '공항 결제창에서 결제를 진행하고 있습니다.')
        page.wait_for_function("() => document.getElementById('job-notice').dataset.tone === 'info'")
        assert notice(page) == '공항 예약창에서 결제를 진행하고 있습니다. 결제를 마치면 예약 완료를 자동으로 확인합니다.'
        assert page.locator('#stop').is_visible() and page.locator('#stop').is_enabled()
        runtime.loop.call_soon_threadsafe(runtime.reserved, job['id'], '1234AB5678')
        page.wait_for_function("() => document.getElementById('job-notice').dataset.tone === 'ok'")
        assert notice(page) == '예약이 완료되었습니다. 예약번호 1234AB5678 · 변경·취소는 공항 사이트 예약조회에서 할 수 있습니다.'
        assert page.locator('#header-status-text').inner_text() == '예약 완료'
        assert page.locator('#stop').is_disabled() and page.locator('#show-browser').is_hidden()
        assert page.locator('#record-result').count() == 0
        eventually(lambda: not runtime.store.get(job['id'])['active'], timeout=5)
        page.wait_for_function("() => !document.getElementById('watch').hidden")
        assert not errors


def test_payment_stop_asks_then_ends_as_closed(ui_server, ui_context):
    base, runtime = ui_server
    job = wait_state(runtime, runtime.create(inputs())['id'], READY)
    runtime.store.dispatch_payment(job['id'], job)
    runtime.store.transition(job['id'], 'PAYMENT_IN_PROGRESS', 'test')
    with open_page(ui_context, base) as (page, errors):
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("() => document.getElementById('header-status-text').textContent === '결제 진행 중'")
        prompts = []
        page.once('dialog', lambda dialog: (prompts.append(dialog.message), dialog.dismiss()))
        page.click('#stop')
        page.wait_for_timeout(300)
        assert prompts == ['공항 예약창을 닫고 작업을 끝냅니다. 결제 중이면 먼저 마쳐주세요.']
        assert runtime.store.get(job['id'])['active']
        page.once('dialog', lambda dialog: dialog.accept())
        page.click('#stop')
        page.wait_for_function("() => document.getElementById('header-status-text').textContent === '종료됨'")
        # 닫는 동안(진행 중)도 회색 띠라서, 끝난 문구와 시작 버튼이 돌아온 것을 기다린다.
        page.wait_for_function("() => document.getElementById('job-notice-text').textContent.startsWith('예약 완료를 확인하지 못하고')")
        page.wait_for_function("() => !document.getElementById('watch').hidden && !document.getElementById('watch').disabled")
        assert notice(page) == '예약 완료를 확인하지 못하고 작업을 끝냈습니다. 결제했다면 공항 사이트 예약조회에서 예약 내역을 확인해주세요.'
        assert page.evaluate("document.activeElement.id") == 'watch'
        assert not errors


def test_legacy_reserved_outcome_shows_closed(ui_server, ui_context):
    base, runtime = ui_server
    job = wait_state(runtime, runtime.create(inputs())['id'], READY)
    runtime.stop(job['id'], job)
    eventually(lambda: not runtime.store.get(job['id'])['active'])
    legacy = {**runtime.store.get(job['id']), 'state': 'CLOSED_BY_USER', 'userReportedOutcome': 'reserved'}
    with runtime.store.transaction():
        runtime.store._save(legacy)
    with open_page(ui_context, base) as (page, errors):
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("() => document.getElementById('header-status-text').textContent === '종료됨'")
        wait_polls(page)  # 첫 렌더만이 아니라 폴링을 거친 뒤에도 옛 결과 기록으로 완료 안내를 띄우지 않는다
        assert page.locator('#header-status').get_attribute('data-tone') == 'idle'
        assert notice(page) == '예약 완료를 확인하지 못하고 작업을 끝냈습니다. 결제했다면 공항 사이트 예약조회에서 예약 내역을 확인해주세요.'
        assert overlay_variant(page) is None
        assert not errors


def test_notice_strip_for_unknown_result_and_closing_window(ui_server, ui_context):
    base, runtime = ui_server
    job = wait_state(runtime, runtime.create(inputs())['id'], READY)
    runtime.store.dispatch_payment(job['id'], job)
    runtime.store.transition(job['id'], 'PAYMENT_RESULT_UNKNOWN', 'test')
    with open_page(ui_context, base) as (page, errors):
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("() => document.getElementById('job-notice').dataset.tone === 'warn'")
        assert notice(page) == '결제 결과를 확인하지 못했습니다. 결제했다면 공항 사이트 예약조회에서 확인해주세요.'
        expect(page.locator('#job-notice #show-browser')).to_be_visible()
        assert page.locator('#stop').is_enabled()
        # 예약창을 닫지 못해 닫는 중으로 남으면 회색 띠로 다시 누르라고 안내하고, 중지를 누를 수 있다.
        runtime.store.transition(job['id'], 'CLOSED_BY_USER', '예약창을 닫지 못했습니다. ‘중지’를 다시 눌러주세요.')
        page.wait_for_function("() => document.getElementById('job-notice').dataset.tone === 'idle'")
        assert notice(page) == '공항 예약창을 닫고 있습니다. 닫히지 않으면 중지를 다시 눌러주세요.'
        assert page.locator('#show-browser').is_hidden()
        assert page.locator('#stop').is_visible() and page.locator('#stop').is_enabled()
        assert not errors


def test_log_clear_empties_table_and_disables(ui_server, ui_context):
    base, runtime = ui_server
    job = wait_state(runtime, runtime.create(inputs())['id'], READY)
    with open_page(ui_context, base) as (page, errors):
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("() => document.querySelectorAll('#log-body tr.log-row').length > 0")
        assert page.locator('#log-clear').is_disabled()  # 진행 중에는 지울 수 없다
        runtime.stop(job['id'], job)
        page.wait_for_function("() => !document.getElementById('log-clear').disabled")
        page.click('#log-clear')
        page.wait_for_function("() => document.querySelectorAll('#log-body tr.log-row').length === 0")
        page.wait_for_timeout(2 * 200)  # 폴링이 옛 로그를 다시 그리지 않는다
        assert page.locator('#log-body tr.log-row').count() == 0 and page.locator('#log-clear').is_disabled()
        assert not errors


BUTTONS = """() => { const grid = document.querySelector('.action-grid').getBoundingClientRect();
    return [...document.querySelectorAll('.action-grid > button:not([hidden])')].map(b => { const r = b.getBoundingClientRect();
        return {id: b.id, top: Math.round(r.top), share: r.width / grid.width}; }); }"""


def test_mobile_action_buttons_fill_rows(ui_server, ui_context):
    # 모바일 2열에서 셋째 버튼이 반 폭으로 홀로 서지 않고 한 줄을 다 쓴다. PC는 한 줄에 같은 폭이다.
    base, _ = ui_server
    with open_page(ui_context, base, width=390, height=844) as (page, errors):
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("() => !document.getElementById('check').disabled")
        first, second, third = page.evaluate(BUTTONS)
        assert first['top'] == second['top'] < third['top']
        assert abs(first['share'] - second['share']) < 0.01 and third['share'] > 0.99
        page.set_viewport_size({'width': 1440, 'height': 900})
        buttons = page.evaluate(BUTTONS)
        assert len({b['top'] for b in buttons}) == 1 and max(b['share'] for b in buttons) - min(b['share'] for b in buttons) < 0.01
        assert not errors


def test_waiting_available_badge_uses_running_tone(ui_server, ui_context):
    # 만차 · 조회 대기는 라벨은 그대로지만, 톤은 대기(idle)가 아니라 실행 중(running)이어야 한다.
    base, runtime = ui_server
    job = runtime.store.create(inputs(), 'run1')
    runtime.store.transition(job['id'], 'WAITING_AVAILABLE', '만차입니다.')
    with open_page(ui_context, base) as (page, errors):
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("() => document.getElementById('header-status-text').textContent === '만차 · 조회 대기'")
        assert page.locator('#header-status').get_attribute('data-tone') == 'running'
        assert not errors
