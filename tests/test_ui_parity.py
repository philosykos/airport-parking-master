import os
import threading
from dataclasses import replace
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

from app import app
from services.gimpo.config import CONFIG
from services.gimpo.parking import GimpoService

STYLE_PROBES = {
    '.app-header': ['height', 'backgroundColor'],
    '.btn-start': ['height', 'backgroundColor', 'borderRadius', 'fontSize'],
    '.btn-stop': ['backgroundColor', 'borderRadius'],
    '.btn-test': ['backgroundColor', 'borderRadius'],
    '#carNumber': ['height', 'backgroundColor', 'borderRadius', 'fontSize'],
    '.field-group label': ['fontSize', 'fontWeight', 'color'],
    '.log-title': ['fontSize', 'fontWeight'],
    '#header-status': ['fontSize', 'borderRadius'],
    '.form-panel': ['width'],
}


@pytest.fixture
def ui_server(client, tmp_path, monkeypatch):
    # client 픽스처가 T2 저장소·스케줄러·외부 호출을 tmp_path와 가짜로 바꿔 둔다.
    # 김포 서비스도 실제 data/ 대신 tmp_path를 쓰도록 새로 만든다.
    service = GimpoService(replace(CONFIG, directory=tmp_path / 'gimpo'))
    monkeypatch.setitem(app.extensions, 'gimpo', service)
    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}', service
    server.shutdown()
    thread.join()
    service.close()


def computed(page):
    return page.evaluate("""probes => Object.fromEntries(Object.entries(probes).map(([selector, keys]) => {
        const node = document.querySelector(selector);
        const style = node ? getComputedStyle(node) : null;
        return [selector, style ? Object.fromEntries(keys.map(key => [key, style[key]])) : null];
    }))""", STYLE_PROBES)


def test_t2_and_gimpo_share_component_styles(client, ui_server):
    base, _ = ui_server
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1280, 'height': 900})
        page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
        results = {}
        for path, ready in (('/t2-valet/', "() => !document.getElementById('btn-start').disabled"),
                            ('/gimpo-parking/', "() => !document.getElementById('check').disabled")):
            page.goto(base + path)
            page.wait_for_load_state('networkidle')
            page.wait_for_function(ready)
            results[path] = computed(page)
        browser.close()
    assert results['/t2-valet/'] == results['/gimpo-parking/']


@pytest.mark.parametrize('path', ['/', '/t2-valet/', '/gimpo-parking/'])
@pytest.mark.parametrize('width', [320, 390, 1280])
def test_no_horizontal_scroll(client, ui_server, path, width):
    base, _ = ui_server
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': width, 'height': 800})
        page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
        page.goto(base + path)
        page.wait_for_load_state('networkidle')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        browser.close()


@pytest.mark.skipif(not os.environ.get('UI_CAPTURE_DIR'), reason='UI_CAPTURE_DIR를 지정할 때만 캡처한다')
def test_capture_screens(client, ui_server):
    base, _ = ui_server
    out = Path(os.environ['UI_CAPTURE_DIR'])
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, width, height in [('desk', 1440, 900), ('mob', 390, 844)]:
            page = browser.new_page(viewport={'width': width, 'height': height})
            page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
            for path in ['', 't2-valet/', 'gimpo-parking/']:
                page.goto(base + '/' + path)
                page.wait_for_load_state('networkidle')
                page.screenshot(path=str(out / f"{name}_{path.strip('/') or 'landing'}.png"), full_page=True)
            page.close()
        browser.close()
