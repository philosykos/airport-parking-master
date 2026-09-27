from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from app import app

ROOT = Path(__file__).resolve().parent.parent
UI_SCRIPTS = ['api', 'overlay', 'toast', 'ripple', 'sheet', 'select_picker', 'status_badge', 'log_panel']
STYLES = ['tokens', 'layout', 'form', 'log', 'overlay']
ORIGIN = 'http://ui.test'


def render(template):
    return app.jinja_env.get_template(template).render()


def render_string(source):
    return app.jinja_env.from_string(source).render()


def is_open(page, element_id):
    return page.evaluate(f"document.getElementById('{element_id}').classList.contains('open')")


@pytest.fixture
def ui_page():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1280, 'height': 900})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))

        def load(body, routes=None):
            html = f'<!doctype html><html lang="ko"><head><meta charset="utf-8"></head><body>{body}</body></html>'
            page.route(ORIGIN + '/', lambda route: route.fulfill(body=html, content_type='text/html; charset=utf-8'))
            for path, (status, payload) in (routes or {}).items():
                # route.route() 핸들러는 (route, request) 두 개를 위치 인자로 넘긴다. 클로저로 캡처한 기본값
                # 인자(s, b)가 그 두 번째 위치 인자(request)를 실수로 받지 않도록 자리표시자를 하나 더 둔다.
                page.route(ORIGIN + path, lambda route, _request=None, s=status, b=payload: route.fulfill(status=s, body=b, content_type='application/json'))
            page.goto(ORIGIN + '/')
            for name in STYLES:
                page.add_style_tag(content=(ROOT / f'static/css/{name}.css').read_text(encoding='utf-8'))
            for name in UI_SCRIPTS:
                page.add_script_tag(content=(ROOT / f'static/js/ui/{name}.js').read_text(encoding='utf-8'))
            return page

        yield load, errors
        browser.close()


def test_api_returns_json_and_raises_server_message(ui_page):
    load, errors = ui_page
    page = load('', routes={'/ok': (200, '{"value": 1}'),
                            '/bad': (409, '{"error": "상태가 바뀌었습니다."}'),
                            '/html': (500, '<h1>x</h1>')})
    assert page.evaluate("UI.api('/ok')") == {'value': 1}
    assert page.evaluate("UI.api('/bad', {method: 'POST', data: {}}).catch(e => [e.message, e.status])") == ['상태가 바뀌었습니다.', 409]
    assert page.evaluate("UI.api('/html').catch(e => e.message)") == '서버 오류 (HTTP 500)'
    assert not errors


def test_toast_escapes_text_and_keeps_three(ui_page):
    load, errors = ui_page
    page = load('<div class="toast-container" id="toast-container"></div>')
    page.evaluate("() => { for (let i = 0; i < 5; i++) UI.toast('<b>메시지' + i + '</b>', i % 2 ? 'error' : 'success'); }")
    toasts = page.locator('.toast')
    assert toasts.count() == 3
    assert toasts.last.inner_text().strip().endswith('<b>메시지4</b>')
    assert page.locator('.toast b').count() == 0
    assert page.locator('.toast-error[role=alert]').count() == 1
    assert not errors


def test_completion_success_and_action_variants(ui_page):
    load, errors = ui_page
    page = load('<button id="trigger">열기</button>' + render('partials/completion_overlay.html'))
    overlay = page.locator('#completion-overlay')
    expect(overlay).to_be_hidden()
    page.focus('#trigger')
    page.evaluate("UI.completion.success({title: '예약이', highlight: '완료되었습니다', subtitle: '확인해주세요'})")
    expect(overlay).to_be_visible()
    assert overlay.get_attribute('data-variant') == 'success'
    assert int(page.evaluate("document.getElementById('completion-overlay').style.zIndex")) >= 1000
    assert page.locator('#completion-overlay .success-highlight').inner_text() == '완료되었습니다'
    assert page.evaluate('document.body.style.overflow') == 'hidden'
    page.keyboard.press('Escape')
    expect(overlay).to_be_hidden()
    assert page.evaluate("document.getElementById('completion-overlay').style.zIndex") == ''
    assert page.evaluate('document.activeElement.id') == 'trigger'
    assert page.evaluate('document.body.style.overflow') == ''

    page.evaluate("""() => { window.clicked = []; window.dismissed = [];
        UI.completion.action({title: '결제해주세요', subtitle: '공항 예약창',
            actions: [{label: '공항 예약창 보기', onClick: () => clicked.push('show')}, {label: '닫기', variant: 'secondary'}],
            onDismiss: reason => dismissed.push(reason)}); }""")
    assert overlay.get_attribute('data-variant') == 'action'
    expect(page.locator('#completion-overlay .confetti-container')).to_be_hidden()
    page.wait_for_function("document.activeElement.textContent === '공항 예약창 보기'")
    page.keyboard.press('Tab')
    page.keyboard.press('Tab')
    assert page.evaluate('document.activeElement.textContent') == '공항 예약창 보기'
    page.focus('#trigger')  # 포커스가 레이어 밖으로 새어도 Tab이 레이어 안으로 되돌린다
    page.keyboard.press('Tab')
    assert page.evaluate('document.activeElement.textContent') == '공항 예약창 보기'
    overlay.get_by_role('button', name='공항 예약창 보기').click()
    assert page.evaluate('clicked') == ['show'] and page.evaluate('dismissed') == []
    page.evaluate("UI.completion.action({title: 'x', onDismiss: reason => dismissed.push(reason)})")
    page.keyboard.press('Escape')
    assert page.evaluate('dismissed') == ['escape']
    assert not errors


def test_layer_closed_before_its_focus_frame_keeps_focus_outside(ui_page):
    load, errors = ui_page
    page = load('<button id="trigger">열기</button>' + render('partials/completion_overlay.html'))
    page.focus('#trigger')
    page.evaluate("() => { UI.completion.success({title: 'a'}); UI.completion.close(); }")
    page.evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
    assert page.evaluate('document.activeElement.id') == 'trigger'
    assert not errors


def test_select_picker_sheet_on_mobile_only(ui_page):
    load, errors = ui_page
    page = load('<div class="field-group"><label for="color">색상</label><select id="color">'
                '<option value="">선택</option><option value="RED">빨강</option><option value="BLUE">파랑</option>'
                '</select></div>' + render('partials/select_picker.html'))
    page.evaluate("""() => { window.changes = 0;
        document.getElementById('color').addEventListener('change', () => changes++);
        window.picker = new UI.SelectPicker(document.getElementById('select-picker-overlay'));
        picker.attach(document.body); }""")
    page.set_viewport_size({'width': 390, 'height': 844})
    page.dispatch_event('#color', 'mousedown')
    page.wait_for_function("document.getElementById('select-picker-overlay').classList.contains('open')")
    assert page.locator('#select-picker-title').inner_text() == '색상'
    assert page.locator('.select-picker-item').all_inner_texts() == ['빨강', '파랑']
    page.locator('.select-picker-item', has_text='파랑').click()
    assert page.input_value('#color') == 'BLUE' and page.evaluate('changes') == 1
    assert not is_open(page, 'select-picker-overlay')
    page.dispatch_event('#color', 'mousedown')
    page.keyboard.press('Escape')
    assert not is_open(page, 'select-picker-overlay')
    page.set_viewport_size({'width': 1280, 'height': 900})
    page.dispatch_event('#color', 'mousedown')
    assert not is_open(page, 'select-picker-overlay')
    assert not errors


LOG_PANEL = ("{% from 'partials/ui.html' import log_sheet_bar, log_panel %}"
             "<div class='status-badge' id='header-status' data-tone='idle'><span class='ping-container'><span class='ping-ring'></span><span class='ping-dot'></span></span><span id='header-status-text'>대기 중</span></div>"
             "<section class='log-panel' id='log-panel'>{{ log_sheet_bar() }}{{ log_panel('실행 로그', '기록 없음') }}</section>"
             "{% include 'partials/log_mobile.html' %}{% include 'partials/log_detail.html' %}"
             "{% include 'partials/completion_overlay.html' %}")

ENTRIES = """[
  {time: '10:00', type: {label: '1회 요청', variant: 'test'}, status: {label: '601', tone: 'error'},
   summary: '<img src=x onerror=alert(1)>', detail: [{title: 'RESPONSE BODY', kind: 'json', body: '{"a":1}'}]},
  {time: '10:01', type: {label: '상태', variant: 'event'}, status: {label: 'SUCCESS', tone: 'info'},
   summary: '완료', detail: [{title: '메시지', kind: 'text', body: '완료'}]}]"""


def test_log_panel_renders_rows_detail_and_empty_state(ui_page):
    load, errors = ui_page
    page = load(render_string(LOG_PANEL))
    page.evaluate(f"() => {{ window.panel = new UI.LogPanel(document.getElementById('log-panel')); panel.render({ENTRIES}); }}")
    rows = page.locator('#log-body tr.log-row')
    assert rows.count() == 2
    assert rows.first.locator('.cell-time').inner_text() == '10:01'
    assert page.locator('#log-body img').count() == 0
    assert page.locator('#log-count').inner_text() == '2'
    assert rows.nth(1).locator('.cell-status').get_attribute('data-tone') == 'error'
    assert rows.first.get_attribute('class') == 'log-row row-event'
    rows.nth(1).click()
    page.wait_for_function("document.getElementById('detail-overlay').classList.contains('open')")
    assert page.locator('#detail-json .json-key').inner_text() == '"a":'
    page.keyboard.press('Escape')
    assert not is_open(page, 'detail-overlay')
    page.evaluate('panel.render([])')
    assert page.locator('#log-body .empty-msg').inner_text().strip().endswith('기록 없음')
    assert page.locator('#log-count').inner_text() == '0'
    assert not errors


def test_log_panel_is_bottom_sheet_on_mobile_and_escape_closes_top_layer_only(ui_page):
    load, errors = ui_page
    page = load(render_string(LOG_PANEL))
    page.evaluate(f"() => {{ window.panel = new UI.LogPanel(document.getElementById('log-panel')); panel.render({ENTRIES}); }}")
    page.set_viewport_size({'width': 390, 'height': 844})
    expect(page.locator('#log-panel')).to_be_hidden()
    assert page.locator('#log-fab').is_visible()
    assert page.locator('#log-fab-badge').inner_text() == '2'
    page.click('#log-fab')
    page.wait_for_function("document.getElementById('log-panel').classList.contains('open')")
    page.locator('#log-body tr.log-row').first.click()
    page.wait_for_function("document.getElementById('detail-overlay').classList.contains('open')")
    page.keyboard.press('Escape')
    assert not is_open(page, 'detail-overlay')
    assert is_open(page, 'log-panel')
    page.locator('#log-panel .log-sheet-close').click()
    assert not is_open(page, 'log-panel')
    assert page.evaluate('document.body.style.overflow') == ''
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert not errors


def test_status_badge_updates_header_and_log_status(ui_page):
    load, errors = ui_page
    page = load(render_string(LOG_PANEL))
    page.evaluate("UI.statusBadge.set({label: '스케줄 실행 중', short: '실행 중', tone: 'running'})")
    assert page.locator('#header-status').get_attribute('data-tone') == 'running'
    assert page.locator('#header-status-text').inner_text() == '스케줄 실행 중'
    assert page.locator('#status-badge').get_attribute('data-tone') == 'running'
    assert page.locator('#status-badge .status-label').inner_text() == '실행 중'
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.locator('#header-status').is_visible()
    assert not errors


def test_completion_overlay_stacks_above_open_log_sheet(ui_page):
    load, errors = ui_page
    page = load(render_string(LOG_PANEL))
    page.evaluate("window.panel = new UI.LogPanel(document.getElementById('log-panel'))")
    page.set_viewport_size({'width': 390, 'height': 844})
    page.click('#log-fab')
    page.wait_for_function("document.getElementById('log-panel').classList.contains('open')")
    page.evaluate("UI.completion.success({title: '예약이', highlight: '완료되었습니다'})")
    hit = page.evaluate("document.elementFromPoint(innerWidth / 2, innerHeight - 40).closest('#completion-overlay') !== null")
    assert hit
    page.evaluate('panel.openSheet()')  # 완료 안내가 떠 있으면 시트를 다시 올리지 않는다
    assert page.evaluate("UI.layers.top() === document.getElementById('completion-overlay')")
    assert not errors
