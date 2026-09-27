import threading
from dataclasses import replace

import pytest
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

from app import app
from services.gimpo.config import CONFIG
from services.gimpo.parking import GimpoService
from services.t2 import valet as t2_valet


@pytest.fixture
def t2_server(client, tmp_path, monkeypatch):
    # client 픽스처가 T2 저장소·스케줄러·외부 호출을 tmp_path와 가짜로 바꿔 둔다.
    # 설정 팝업이 김포 알림 상태도 읽으므로 김포 서비스도 임시 경로로 바꾼다.
    service = GimpoService(replace(CONFIG, directory=tmp_path / 'gimpo'))
    monkeypatch.setitem(app.extensions, 'gimpo', service)
    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}'
    server.shutdown()
    thread.join()
    service.close()


def open_page(p, base, width=1280):
    browser = p.chromium.launch()
    page = browser.new_page(viewport={'width': width, 'height': 900})
    page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    return browser, page, errors


def overlay_open(page):
    return page.evaluate("document.getElementById('completion-overlay').classList.contains('open')")


def test_required_fields_show_field_errors_and_toast(t2_server):
    with sync_playwright() as p:
        browser, page, errors = open_page(p, t2_server)
        page.goto(t2_server + '/t2-valet/')
        page.wait_for_load_state('networkidle')
        page.click('#btn-start')
        assert page.locator('.field-group.has-error').count() >= 3
        page.locator('.toast-error').first.wait_for()
        assert page.locator('#btn-start').is_enabled()
        page.fill('#name', '홍길동')
        assert page.locator('#name').evaluate("el => el.closest('.field-group').classList.contains('has-error')") is False
        assert not errors
        browser.close()


def test_new_success_log_shows_completion_once_and_tones(t2_server):
    t2_valet.log_store.append({'time': '2026-01-01 00:00:00', 'type': 'schedule', 'status': 601,
                               'body': '{"result":{"message":"중복"}}', 'url': 'https://example.invalid/reserve', 'payload': {'name': '홍*동'}})
    with sync_playwright() as p:
        browser, page, errors = open_page(p, t2_server)
        page.goto(t2_server + '/t2-valet/')
        page.wait_for_function("() => document.getElementById('log-count').textContent === '1'")
        assert not overlay_open(page)
        t2_valet.log_store.append({'time': '2026-01-01 00:00:05', 'type': 'test', 'status': 200,
                                   'body': '{"result":{"code":200}}', 'url': 'https://example.invalid/reserve', 'payload': {}})
        t2_valet.log_store.append({'time': '2026-01-01 00:00:06', 'type': 'notification', 'status': 'FAILED', 'body': '봇 토큰과 수신자 ID를 설정해주세요.'})
        t2_valet.log_store.append({'time': '2026-01-01 00:00:07', 'type': 'test', 'status': 'ERROR', 'body': 'timeout'})
        page.evaluate('t2Screen.fetchLogs()')
        page.wait_for_function("() => document.getElementById('completion-overlay').classList.contains('open')")
        assert page.locator('#completion-overlay').get_attribute('data-variant') == 'success'
        tones = page.locator('#log-body .cell-status').evaluate_all('nodes => nodes.map(node => node.dataset.tone)')
        assert tones == ['error', 'error', 'success', 'error']
        page.locator('#completion-overlay button').click()
        page.evaluate('t2Screen.fetchLogs()')
        page.wait_for_timeout(300)
        assert not overlay_open(page)
        page.locator('#log-body tr.log-row').nth(2).click()
        page.wait_for_function("() => document.getElementById('detail-overlay').classList.contains('open')")
        assert 'example.invalid' in page.locator('#detail-json').inner_text()
        assert not errors
        browser.close()


def test_mobile_sheet_picker_and_settings_keep_inputs(t2_server):
    with sync_playwright() as p:
        browser, page, errors = open_page(p, t2_server, width=390)
        page.goto(t2_server + '/t2-valet/')
        page.wait_for_load_state('networkidle')
        page.fill('#name', '홍길동')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert page.locator('#header-status').is_visible()
        page.click('#log-fab')
        page.wait_for_function("() => document.getElementById('log-panel').classList.contains('open')")
        page.locator('#log-panel .log-sheet-close').click()
        page.wait_for_function("() => !document.getElementById('log-panel').classList.contains('open')")
        page.dispatch_event('#carColor', 'mousedown')
        page.wait_for_function("() => document.getElementById('select-picker-overlay').classList.contains('open')")
        page.locator('.select-picker-item', has_text='흰색').click()
        assert page.input_value('#carColor') == 'WHITE'
        page.click('#open-settings')
        assert page.locator('#settings-dialog').is_visible()
        page.keyboard.press('Escape')
        assert page.input_value('#name') == '홍길동'
        assert page.url == t2_server + '/t2-valet/'
        assert not errors
        browser.close()


def test_polling_survives_failed_log_request_after_start(t2_server):
    with sync_playwright() as p:
        browser, page, errors = open_page(p, t2_server)
        page.goto(t2_server + '/t2-valet/')
        page.wait_for_load_state('networkidle')
        page.fill('#name', '홍길동')
        page.fill('#phone', '01012345678')
        page.fill('#carNumber', '12가3456')
        page.fill('#carModel', '그랜저')
        page.select_option('#carBrand', 'HY')
        page.select_option('#carColor', 'WHITE')
        failed = []

        def fail_once(route):
            if not failed:
                failed.append(route.request.url)
                route.fulfill(status=503, json={'error': 'busy'})
            else:
                route.continue_()

        page.route('**/t2-valet/api/logs', fail_once)
        page.click('#btn-start')
        page.wait_for_function("() => document.getElementById('header-status-text').textContent === '스케줄 실행 중'")
        page.wait_for_function("() => Number(document.getElementById('log-count').textContent) >= 1", timeout=10000)
        assert failed
        page.click('#btn-stop')
        page.wait_for_function("() => document.getElementById('header-status-text').textContent === '대기 중'")
        assert not errors
        browser.close()
