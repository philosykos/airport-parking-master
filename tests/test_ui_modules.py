from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from app import app

ROOT = Path(__file__).resolve().parent.parent
UI_SCRIPTS = ['api', 'overlay', 'toast', 'ripple']
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
