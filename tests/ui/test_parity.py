import os
from pathlib import Path

import pytest

from tests.support.ui import open_page

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


def computed(page):
    return page.evaluate("""probes => Object.fromEntries(Object.entries(probes).map(([selector, keys]) => {
        const node = document.querySelector(selector);
        const style = node ? getComputedStyle(node) : null;
        return [selector, style ? Object.fromEntries(keys.map(key => [key, style[key]])) : null];
    }))""", STYLE_PROBES)


def test_t2_and_gimpo_share_component_styles(client, t2_server, ui_context):
    base = t2_server
    with open_page(ui_context, base, width=1280, height=900) as (page, errors):
        results = {}
        for path, ready in (('/t2-valet/', "() => !document.getElementById('btn-start').disabled"),
                            ('/gimpo-parking/', "() => !document.getElementById('check').disabled")):
            page.goto(base + path)
            page.wait_for_load_state('networkidle')
            page.wait_for_function(ready)
            results[path] = computed(page)
    assert not errors
    assert results['/t2-valet/'] == results['/gimpo-parking/']


@pytest.mark.parametrize('path', ['/', '/t2-valet/', '/gimpo-parking/'])
@pytest.mark.parametrize('width', [320, 390, 1280])
def test_no_horizontal_scroll(client, t2_server, path, width, ui_context):
    base = t2_server
    with open_page(ui_context, base, width=width, height=800) as (page, errors):
        page.goto(base + path)
        page.wait_for_load_state('networkidle')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors


@pytest.mark.parametrize('path,ready', [
    ('/t2-valet/', "() => !document.getElementById('btn-start').disabled"),
    ('/gimpo-parking/', "() => !document.getElementById('check').disabled"),
])
@pytest.mark.parametrize('width', [320, 390])
def test_header_status_badge_label_not_clipped(client, t2_server, path, ready, width, ui_context):
    base = t2_server
    with open_page(ui_context, base, width=width, height=800) as (page, errors):
        page.goto(base + path)
        page.wait_for_load_state('networkidle')
        page.wait_for_function(ready)

        def not_clipped():
            return page.evaluate("() => { const el = document.getElementById('header-status-text');"
                                  " return el.clientWidth >= el.scrollWidth; }")

        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not_clipped()
        if width == 320:
            page.evaluate("() => UI.statusBadge.set({label: '스케줄 실행 중', tone: 'running'})")
            assert not_clipped()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors


@pytest.mark.skipif(not os.environ.get('UI_CAPTURE_DIR'), reason='UI_CAPTURE_DIR를 지정할 때만 캡처한다')
def test_capture_screens(client, t2_server, ui_context):
    base = t2_server
    out = Path(os.environ['UI_CAPTURE_DIR'])
    out.mkdir(parents=True, exist_ok=True)
    for name, width, height in [('desk', 1440, 900), ('mob', 390, 844)]:
        with open_page(ui_context, base, width=width, height=height) as (page, errors):
            for path in ['', 't2-valet/', 'gimpo-parking/']:
                page.goto(base + '/' + path)
                page.wait_for_load_state('networkidle')
                page.screenshot(path=str(out / f"{name}_{path.strip('/') or 'landing'}.png"), full_page=True)
