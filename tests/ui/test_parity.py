import os
from pathlib import Path

import pytest

from services.gimpo.store import READY
from tests.gimpo.helpers import inputs, wait_state
from tests.gimpo.test_ui import ui_server
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
    '.log-clear': ['height', 'fontSize'],
    '.form-title': ['fontSize', 'fontWeight'],
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


PC_VALUES = {
    '.app-header': {'height': '56px'},
    '.title-main': {'fontSize': '17px', 'color': 'rgb(25, 28, 29)'},
    '.field-group label': {'fontSize': '13px', 'textTransform': 'none', 'letterSpacing': 'normal'},
    '#carNumber': {'height': '40px', 'fontSize': '15px', 'borderTopColor': 'rgb(213, 215, 224)', 'backgroundColor': 'rgb(255, 255, 255)'},
    '.action-grid .btn-start': {'height': '40px', 'fontSize': '14px', 'backgroundColor': 'rgb(0, 11, 96)'},
    '.log-card': {'backgroundColor': 'rgb(255, 255, 255)', 'borderTopLeftRadius': '12px'},
    '.form-title': {'fontSize': '16px', 'fontWeight': '800'},
    '.form-panel': {'backgroundColor': 'rgb(248, 249, 250)'},
    '.form-body': {'backgroundColor': 'rgb(255, 255, 255)', 'borderTopColor': 'rgb(228, 229, 234)', 'borderTopLeftRadius': '12px'},
}


@pytest.mark.parametrize('path,ready', [('/t2-valet/', "() => !document.getElementById('btn-start').disabled"),
                                        ('/gimpo-parking/', "() => !document.getElementById('check').disabled")])
def test_pc_visual_values(client, t2_server, ui_context, path, ready):
    base = t2_server
    with open_page(ui_context, base, width=1280, height=900) as (page, errors):
        page.goto(base + path)
        page.wait_for_function(ready)
        for selector, expected in PC_VALUES.items():
            actual = page.evaluate("([s, keys]) => { const st = getComputedStyle(document.querySelector(s)); return Object.fromEntries(keys.map(k => [k, st[k]])); }",
                                   [selector, list(expected)])
            assert actual == expected, selector
        assert page.locator('.header-back-text').is_hidden()
        visible = page.evaluate("[...document.querySelectorAll('.action-grid > button')].filter(b => !b.hidden).map(b => Math.round(b.getBoundingClientRect().width))")
        assert len(set(visible)) == 1 and len(visible) == (2 if 't2' in path else 3)
        rows = page.evaluate("[...document.querySelectorAll('.action-grid > button')].filter(b => !b.hidden).map(b => Math.round(b.getBoundingClientRect().top))")
        assert len(set(rows)) == 1
        assert not errors


@pytest.mark.parametrize('path,ready', [('/t2-valet/', "() => !document.getElementById('btn-start').disabled"),
                                        ('/gimpo-parking/', "() => !document.getElementById('check').disabled")])
def test_form_card_border_turns_success_while_running(client, t2_server, ui_context, path, ready):
    base = t2_server
    with open_page(ui_context, base, width=1280, height=900) as (page, errors):
        page.goto(base + path)
        page.wait_for_function(ready)
        # 실행 중이 아닐 때는 카드 테두리가 평소 색이다
        idle_color = page.eval_on_selector('.form-body', 'el => getComputedStyle(el).borderTopColor')
        assert idle_color == 'rgb(228, 229, 234)'
        page.evaluate("() => document.querySelector('.form-panel').classList.add('form-panel--active')")
        active_color = page.eval_on_selector('.form-body', 'el => getComputedStyle(el).borderTopColor')
        assert active_color == 'rgb(16, 185, 129)'
        # 칸 자체의 경계선은 여전히 없다
        panel_border = page.eval_on_selector('.form-panel', 'el => getComputedStyle(el).borderRightStyle')
        assert panel_border == 'none'
        assert not errors


@pytest.mark.parametrize('path,ready', [('/t2-valet/', "() => !document.getElementById('btn-start').disabled"),
                                        ('/gimpo-parking/', "() => !document.getElementById('check').disabled")])
def test_form_card_aligns_with_log_panel_first_card(client, t2_server, ui_context, path, ready):
    base = t2_server
    with open_page(ui_context, base, width=1280, height=900) as (page, errors):
        page.goto(base + path)
        page.wait_for_function(ready)
        rects = page.evaluate("""() => {
            const formBody = document.querySelector('.form-body');
            const progressCard = document.querySelector('.progress-card');
            const topCard = (progressCard && progressCard.offsetParent !== null) ? progressCard : document.querySelector('.log-card');
            const logCard = document.querySelector('.log-card');
            const fb = formBody.getBoundingClientRect();
            const tc = topCard.getBoundingClientRect();
            const lc = logCard.getBoundingClientRect();
            return {formTop: fb.top, formBottom: fb.bottom, topCardTop: tc.top, logCardBottom: lc.bottom};
        }""")
        assert abs(rects['formTop'] - rects['topCardTop']) <= 1
        assert abs(rects['formBottom'] - rects['logCardBottom']) <= 1
        assert not errors


def test_gimpo_log_rows_and_summary_render_correctly_on_pc(ui_server, ui_context):
    # 로그 행이 있는 상태(빈 표 문구가 아님)에서 .cell-time 글자 크기를, 요약 다섯 칸이 보이는 상태에서
    # 칸 사이 간격과 카드 정렬을 확인한다. 빈 상태로는 통과할 수 없도록 실제 작업을 만든다.
    base, runtime = ui_server
    job = wait_state(runtime, runtime.create(inputs())['id'], READY)
    assert job['logs'], '로그가 있는 상태에서 확인해야 한다'
    with open_page(ui_context, base, width=1280, height=900) as (page, errors):
        page.goto(base + '/gimpo-parking/')
        # 이 작업은 이미 진행 중(결제 대기)이라 조회 버튼은 계속 비활성 상태다. 상태 배지로 화면이 준비됐음을 본다.
        page.wait_for_function("() => document.getElementById('header-status-text').textContent === '결제 대기'")
        page.wait_for_function("() => document.querySelectorAll('.cell-time').length > 0")
        row_count = page.locator('.cell-time').count()
        assert row_count >= 1
        time_size = page.eval_on_selector('.cell-time', 'el => getComputedStyle(el).fontSize')
        assert time_size == '13px'

        page.wait_for_function("() => document.querySelectorAll('#summary .summary-item').length === 5")
        metrics = page.evaluate("""() => {
            const card = document.querySelector('.progress-card');
            const cardRect = card.getBoundingClientRect();
            const style = getComputedStyle(card);
            const innerLeft = cardRect.left + parseFloat(style.paddingLeft);
            const innerRight = cardRect.right - parseFloat(style.paddingRight);
            const items = [...document.querySelectorAll('#summary .summary-item')].map(el => el.getBoundingClientRect());
            const gaps = [];
            for (let i = 1; i < items.length; i++) gaps.push(Math.round((items[i].left - items[i - 1].right) * 10) / 10);
            return {gaps, firstLeft: items[0].left, lastRight: items[items.length - 1].right, innerLeft, innerRight};
        }""")
        assert len(metrics['gaps']) == 4
        assert max(metrics['gaps']) - min(metrics['gaps']) <= 1
        assert all(gap >= 16 - 0.5 for gap in metrics['gaps'])
        assert abs(metrics['firstLeft'] - metrics['innerLeft']) <= 1
        assert abs(metrics['lastRight'] - metrics['innerRight']) <= 1

        # 폼 카드는 (요약이 보이는) 진행 카드의 위 끝, 실행 로그 카드의 아래 끝에 맞춘다
        alignment = page.evaluate("""() => {
            const formBody = document.querySelector('.form-body');
            const progressCard = document.querySelector('.progress-card');
            const logCard = document.querySelector('.log-card');
            const fb = formBody.getBoundingClientRect();
            const pc = progressCard.getBoundingClientRect();
            const lc = logCard.getBoundingClientRect();
            return {formTop: fb.top, formBottom: fb.bottom, progressTop: pc.top, logBottom: lc.bottom};
        }""")
        assert abs(alignment['formTop'] - alignment['progressTop']) <= 1
        assert abs(alignment['formBottom'] - alignment['logBottom']) <= 1
        assert not errors


@pytest.mark.parametrize('width', [1200, 1000, 961])
def test_gimpo_summary_shrinks_without_clipping_and_parking_first(ui_server, ui_context, width):
    # 좁은 PC 폭(961~1100px)에서도 요약 칸이 카드 밖으로 잘려 나가면 안 된다. 주차장 칸이 먼저 줄어들어
    # 말줄임하고, 그래도 모자라면 나머지 칸도 순서대로 줄어들어 말줄임한다(조용히 잘리지 않는다).
    base, runtime = ui_server
    wait_state(runtime, runtime.create(inputs())['id'], READY)
    with open_page(ui_context, base, width=width, height=900) as (page, errors):
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("() => document.getElementById('header-status-text').textContent === '결제 대기'")
        page.wait_for_function("() => document.querySelectorAll('#summary .summary-item').length === 5")
        rows = page.evaluate("""() => {
            const card = document.querySelector('.progress-card');
            const cardRect = card.getBoundingClientRect();
            const style = getComputedStyle(card);
            const innerRight = cardRect.right - parseFloat(style.paddingRight);
            return [...document.querySelectorAll('#summary .summary-item')].map(el => {
                const dd = el.querySelector('dd');
                return {right: el.getBoundingClientRect().right, innerRight,
                        cut: dd.scrollWidth - dd.clientWidth};
            });
        }""")
        assert len(rows) == 5
        # 카드 밖(오른쪽)으로 잘려 나간 칸이 없다(그 전에는 뒤 네 칸이 overflow:hidden에 잘렸다).
        for row in rows:
            assert row['right'] <= row['innerRight'] + 1, row
        # 주차장(첫 칸)이 이 폭들에서 이미 뚜렷하게 말줄임된 상태이고(글자 몇 개가 아니라 한 뭉치가 잘림),
        # 다른 어느 칸보다 더 많이(먼저) 줄어들어 있다. 나머지 칸도 1px 안팎의 미세한 반올림 차이 정도는
        # 있을 수 있지만(레이아웃 배분 오차), 주차장만큼 뚜렷하게 잘리지는 않는다.
        assert rows[0]['cut'] > 10, rows
        assert rows[0]['cut'] >= max(row['cut'] for row in rows[1:]), rows
        assert not errors


@pytest.mark.parametrize('path,ready', [('/t2-valet/', "() => !document.getElementById('btn-start').disabled"),
                                        ('/gimpo-parking/', "() => !document.getElementById('check').disabled")])
def test_action_grid_button_labels_stay_on_one_line(client, t2_server, ui_context, path, ready):
    # 카드가 좁아진 뒤에도 폼 버튼 라벨("T2 정보 가져오기" 등)이 두 줄로 꺾이거나 잘리면 안 된다.
    base = t2_server
    with open_page(ui_context, base, width=1280, height=900) as (page, errors):
        page.goto(base + path)
        page.wait_for_function(ready)
        rows = page.evaluate("""() => [...document.querySelectorAll('.action-grid > button')].filter(b => !b.hidden).map(b => {
            const label = b.querySelector('.btn-label');
            const range = document.createRange();
            range.selectNodeContents(label);
            const lineRects = [...range.getClientRects()];
            return {id: b.id, text: label.textContent, numLines: lineRects.length,
                    scrollWidth: label.scrollWidth, clientWidth: label.clientWidth};
        })""")
        assert len(rows) == (2 if 't2' in path else 3)
        for row in rows:
            # 글자 자체가 차지하는 줄 상자(Range.getClientRects)가 하나뿐이면 한 줄이다.
            # (글꼴에 따라 line-height가 달라 label 상자의 bounding height만으로는 줄바꿈을 못 가른다.)
            assert row['numLines'] == 1, row  # 한 줄(두 줄로 꺾이지 않음)
            assert row['scrollWidth'] <= row['clientWidth'] + 0.5, row  # 잘리지 않음
        assert not errors


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
