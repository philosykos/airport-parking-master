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


@pytest.mark.parametrize('path,ready', [
    ('/', None),
    ('/t2-valet/', "() => !document.getElementById('btn-start').disabled"),
    ('/gimpo-parking/', "() => !document.getElementById('check').disabled"),
])
def test_no_horizontal_scroll_and_header_badge_not_clipped(ui_context, client, t2_server, path, ready):
    # 너비마다 페이지를 새로 여는 대신 한 번 연 페이지의 뷰포트를 바꿔 가며 같은 단언을 한다.
    # 배지 문구를 바꾸는 320 확인이 다른 너비에 영향을 주지 않도록 넓은 쪽부터 좁은 쪽으로 돈다.
    base = t2_server
    with open_page(ui_context, base, width=1280, height=800) as (page, errors):
        page.goto(base + path)
        page.wait_for_load_state('networkidle')
        if ready:
            page.wait_for_function(ready)

        def no_horizontal_scroll():
            return page.evaluate('document.documentElement.scrollWidth <= innerWidth')

        def not_clipped():
            return page.evaluate("() => { const el = document.getElementById('header-status-text');"
                                  " return el.clientWidth >= el.scrollWidth; }")

        for width in (1280, 390, 320):
            page.set_viewport_size({'width': width, 'height': 800})
            assert no_horizontal_scroll(), width
            if ready and width in (390, 320):
                assert not_clipped(), width
        if ready:
            page.evaluate("() => UI.statusBadge.set({label: '스케줄 실행 중', tone: 'running'})")
            assert not_clipped()
            assert no_horizontal_scroll()
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
