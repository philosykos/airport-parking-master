
from services.t2 import valet as t2_valet
from tests.support.ui import open_page


def overlay_open(page):
    return page.evaluate("document.getElementById('completion-overlay').classList.contains('open')")


def test_required_fields_show_field_errors_and_toast(t2_server, ui_context):
    with open_page(ui_context, t2_server, height=900) as (page, errors):
        page.goto(t2_server + '/t2-valet/')
        page.wait_for_load_state('networkidle')
        page.click('#btn-start')
        assert page.locator('.field-group.has-error').count() >= 3
        page.locator('.toast-error').first.wait_for()
        assert page.locator('#btn-start').is_enabled()
        page.fill('#name', '홍길동')
        assert page.locator('#name').evaluate("el => el.closest('.field-group').classList.contains('has-error')") is False
        assert not errors


def test_new_success_log_shows_completion_once_and_tones(t2_server, ui_context):
    t2_valet.log_store.append({'time': '2026-01-01 00:00:00', 'type': 'schedule', 'status': 601,
                               'body': '{"result":{"message":"중복"}}', 'url': 'https://example.invalid/reserve', 'payload': {'name': '홍*동'}})
    with open_page(ui_context, t2_server, height=900) as (page, errors):
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
        assert tones == ['error', 'error', 'success', 'warning']
        page.locator('#completion-overlay button').click()
        # evaluate가 fetchLogs의 promise를 기다리고 완료 안내는 그 안에서 동기로 열리므로, 한 프레임만 넘기고 본다.
        page.evaluate('t2Screen.fetchLogs()')
        page.evaluate('new Promise(resolve => requestAnimationFrame(() => resolve()))')
        assert not overlay_open(page)
        page.locator('#log-body tr.log-row').nth(2).click()
        page.wait_for_function("() => document.getElementById('detail-overlay').classList.contains('open')")
        assert 'example.invalid' in page.locator('#detail-json').inner_text()
        assert not errors


def test_completion_overlay_waits_for_open_settings_dialog(t2_server, ui_context):
    with open_page(ui_context, t2_server, height=900) as (page, errors):
        page.goto(t2_server + '/t2-valet/')
        page.wait_for_function("() => document.getElementById('log-count').textContent === '0'")
        page.click('#open-settings')
        assert page.locator('#settings-dialog').is_visible()
        t2_valet.log_store.append({'time': '2026-01-01 00:00:05', 'type': 'test', 'status': 200,
                                   'body': '{"result":{"code":200}}', 'url': 'https://example.invalid/reserve', 'payload': {}})
        page.evaluate('t2Screen.fetchLogs()')
        page.wait_for_function("() => document.getElementById('log-count').textContent === '1'")
        assert not overlay_open(page)
        page.keyboard.press('Escape')
        page.evaluate('t2Screen.fetchLogs()')
        page.wait_for_function("() => document.getElementById('completion-overlay').classList.contains('open')")
        assert not errors


def test_mobile_sheet_picker_and_settings_keep_inputs(t2_server, ui_context):
    with open_page(ui_context, t2_server, width=390, height=900) as (page, errors):
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


def test_polling_survives_failed_log_request_after_start(t2_server, ui_context):
    with open_page(ui_context, t2_server, height=900) as (page, errors):
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
        page.wait_for_function("() => document.getElementById('header-status-text').textContent === '자동 예약 중'")
        page.wait_for_function("() => Number(document.getElementById('log-count').textContent) >= 1", timeout=10000)
        assert failed
        page.click('#btn-stop')
        page.wait_for_function("() => document.getElementById('header-status-text').textContent === '중지됨'")
        assert not errors


def test_stop_shows_idle_immediately_even_if_logs_request_fails(t2_server, ui_context):
    with open_page(ui_context, t2_server, height=900) as (page, errors):
        page.goto(t2_server + '/t2-valet/')
        page.wait_for_load_state('networkidle')
        page.fill('#name', '홍길동')
        page.fill('#phone', '01012345678')
        page.fill('#carNumber', '12가3456')
        page.fill('#carModel', '그랜저')
        page.select_option('#carBrand', 'HY')
        page.select_option('#carColor', 'WHITE')
        page.click('#btn-start')
        page.wait_for_function("() => document.getElementById('header-status-text').textContent === '자동 예약 중'")
        page.route('**/t2-valet/api/logs', lambda route: route.fulfill(status=503, json={'error': 'busy'}))
        page.click('#btn-stop')
        page.wait_for_function("() => document.getElementById('header-status-text').textContent === '중지됨'")
        assert not errors


def test_empty_payload_omits_request_payload_section(t2_server, ui_context):
    t2_valet.log_store.append({'time': '2026-01-01 00:00:00', 'type': 'test', 'status': 200,
                               'body': '{}', 'url': 'https://example.invalid/reserve', 'payload': {}})
    t2_valet.log_store.append({'time': '2026-01-01 00:00:01', 'type': 'test', 'status': 200,
                               'body': '{}', 'url': 'https://example.invalid/reserve', 'payload': {'name': '홍길동'}})
    with open_page(ui_context, t2_server, height=900) as (page, errors):
        page.goto(t2_server + '/t2-valet/')
        page.wait_for_function("() => document.getElementById('log-count').textContent === '2'")
        rows = page.locator('#log-body tr.log-row')
        rows.nth(0).click()
        page.wait_for_function("() => document.getElementById('detail-overlay').classList.contains('open')")
        titles = page.locator('.detail-section-title').all_inner_texts()
        assert 'REQUEST PAYLOAD' in titles
        page.locator('#detail-overlay .detail-close').click()
        page.wait_for_function("() => !document.getElementById('detail-overlay').classList.contains('open')")
        rows.nth(1).click()
        page.wait_for_function("() => document.getElementById('detail-overlay').classList.contains('open')")
        titles = page.locator('.detail-section-title').all_inner_texts()
        assert 'REQUEST PAYLOAD' not in titles
        assert not errors


def test_clear_logs_ignores_stale_log_response(t2_server, ui_context):
    t2_valet.log_store.append({'time': '2026-01-01 00:00:00', 'type': 'test', 'status': 200,
                               'body': '{}', 'url': 'https://example.invalid/reserve', 'payload': {}})
    with open_page(ui_context, t2_server, height=900) as (page, errors):
        # app.js는 건드리지 않고, 붙잡아 둔 응답을 푼 뒤 그 fetch가 실제로 settle됐는지
        # 확인하기 위해 페이지가 로드되기 전에 window.fetch를 감싸 둔다.
        page.add_init_script("""
            window.__t2LogsFetchDone = 0;
            const originalFetch = window.fetch;
            window.fetch = function (...args) {
                const url = typeof args[0] === 'string' ? args[0] : (args[0] && args[0].url) || '';
                const method = (args[1] && args[1].method) || 'GET';
                const promise = originalFetch.apply(this, args);
                if (url.endsWith('/t2-valet/api/logs') && method === 'GET') {
                    promise.finally(() => { window.__t2LogsFetchDone += 1; });
                }
                return promise;
            };
        """)
        held = []

        def hold_or_continue(route):
            if not held:
                held.append(route)
            else:
                route.continue_()

        page.route('**/t2-valet/api/logs', hold_or_continue)
        with page.expect_request('**/t2-valet/api/logs'):
            page.goto(t2_server + '/t2-valet/')
        assert held, '초기 로그 요청이 붙잡히지 않았습니다'
        page.evaluate("() => t2Screen.clearLogs()")
        page.wait_for_function("() => document.getElementById('log-body').children.length === 1 "
                                "&& document.getElementById('log-body').querySelector('.empty-msg')")
        held[0].fulfill(json={'running': False, 'logs': [
            {'time': '2026-01-01 00:00:00', 'type': 'test', 'status': 200,
             'body': '{}', 'url': 'https://example.invalid/reserve', 'payload': {}},
        ]})
        page.wait_for_function("() => window.__t2LogsFetchDone >= 1")
        assert page.locator('#log-body tr.log-row').count() == 0
        assert not errors


def test_start_stop_share_one_slot_and_clear_disables_without_logs(t2_server, ui_context):
    with open_page(ui_context, t2_server) as (page, errors):
        page.goto(t2_server + '/t2-valet/')
        page.wait_for_function("() => !document.getElementById('btn-start').disabled")
        assert page.locator('#btn-stop').is_hidden()
        page.wait_for_function("() => document.getElementById('log-clear').disabled === (document.querySelectorAll('#log-body tr.log-row').length === 0)")
        page.evaluate("t2Screen.updateUI(true)")
        assert page.locator('#btn-start').is_hidden() and page.locator('#btn-stop').is_visible()
        assert not errors


BUTTONS = """() => { const grid = document.querySelector('.action-grid').getBoundingClientRect();
    return [...document.querySelectorAll('.action-grid > button:not([hidden])')].map(b => { const r = b.getBoundingClientRect();
        return {id: b.id, top: Math.round(r.top), share: r.width / grid.width}; }); }"""


def test_mobile_action_buttons_keep_two_columns(t2_server, ui_context):
    # 보이는 버튼이 둘이면 모바일에서도 한 줄 두 칸 그대로다(김포의 셋째 버튼 규칙이 번지지 않는다).
    with open_page(ui_context, t2_server, width=390, height=844) as (page, errors):
        page.goto(t2_server + '/t2-valet/')
        page.wait_for_load_state('networkidle')
        first, second = page.evaluate(BUTTONS)
        assert first['top'] == second['top'] and abs(first['share'] - second['share']) < 0.01 and first['share'] < 0.6
        assert not errors


def test_header_badge_tracks_run_state_across_stop_refresh_and_clear(t2_server, ui_context):
    with open_page(ui_context, t2_server, height=900) as (page, errors):
        page.goto(t2_server + '/t2-valet/')
        page.wait_for_load_state('networkidle')
        page.wait_for_function("() => document.getElementById('header-status-text').textContent === '대기 중'")
        assert page.locator('#header-status').get_attribute('data-tone') == 'idle'
        page.fill('#name', '홍길동')
        page.fill('#phone', '01012345678')
        page.fill('#carNumber', '12가3456')
        page.fill('#carModel', '그랜저')
        page.select_option('#carBrand', 'HY')
        page.select_option('#carColor', 'WHITE')
        page.click('#btn-start')
        page.wait_for_function("() => document.getElementById('header-status-text').textContent === '자동 예약 중'")
        assert page.locator('#header-status').get_attribute('data-tone') == 'running'
        page.click('#btn-stop')
        page.wait_for_function("() => document.getElementById('header-status-text').textContent === '중지됨'")
        assert page.locator('#header-status').get_attribute('data-tone') == 'idle'
        page.reload()
        page.wait_for_function("() => document.getElementById('header-status-text').textContent === '중지됨'")
        assert page.locator('#header-status').get_attribute('data-tone') == 'idle'
        page.click('#log-clear')
        page.wait_for_function("() => document.getElementById('header-status-text').textContent === '대기 중'")
        assert page.locator('#header-status').get_attribute('data-tone') == 'idle'
        assert not errors


def test_success_event_sets_badge_to_success_tone(t2_server, ui_context):
    t2_valet.log_store.append({'time': '2026-01-01 00:00:00', 'type': 'event', 'status': 'START', 'body': '자동 예약 시작 (30초 간격)'})
    t2_valet.log_store.append({'time': '2026-01-01 00:00:05', 'type': 'event', 'status': 'SUCCESS', 'body': '예약이 완료되어 자동 예약을 종료합니다.'})
    with open_page(ui_context, t2_server, height=900) as (page, errors):
        page.goto(t2_server + '/t2-valet/')
        page.wait_for_function("() => document.getElementById('header-status-text').textContent === '예약 완료'")
        assert page.locator('#header-status').get_attribute('data-tone') == 'success'
        assert not errors


def test_badge_idle_tone_matches_log_chip_idle_colors(t2_server, ui_context):
    with open_page(ui_context, t2_server, height=900) as (page, errors):
        page.goto(t2_server + '/t2-valet/')
        page.wait_for_function("() => document.getElementById('header-status-text').textContent === '대기 중'")
        colors = page.evaluate("""() => {
            const badge = getComputedStyle(document.getElementById('header-status'));
            const chip = document.createElement('span');
            chip.className = 'cell-status';
            chip.dataset.tone = 'idle';
            document.body.appendChild(chip);
            const chipStyle = getComputedStyle(chip);
            const result = {badgeBg: badge.backgroundColor, badgeColor: badge.color,
                            chipBg: chipStyle.backgroundColor, chipColor: chipStyle.color};
            chip.remove();
            return result;
        }""")
        assert colors['badgeBg'] == colors['chipBg']
        assert colors['badgeColor'] == colors['chipColor']
        assert not errors


def test_log_status_chips_use_korean_labels_and_keep_raw_code_in_detail(t2_server, ui_context):
    t2_valet.log_store.append({'time': '2026-01-01 00:00:00', 'type': 'event', 'status': 'START', 'body': '자동 예약 시작 (30초 간격)'})
    t2_valet.log_store.append({'time': '2026-01-01 00:00:01', 'type': 'schedule', 'status': 601,
                               'body': '{"result":{"message":"입력하신 기간에 이미 중복된 예약이 존재합니다.","code":601}}',
                               'url': 'https://example.invalid/reserve', 'payload': {}})
    t2_valet.log_store.append({'time': '2026-01-01 00:00:02', 'type': 'test', 'status': 200,
                               'body': '{"result":{"code":200}}', 'url': 'https://example.invalid/reserve', 'payload': {}})
    t2_valet.log_store.append({'time': '2026-01-01 00:00:03', 'type': 'test', 'status': 'ERROR', 'body': 'timeout'})
    t2_valet.log_store.append({'time': '2026-01-01 00:00:04', 'type': 'notification', 'status': 'FAILED', 'body': '봇 토큰과 수신자 ID를 설정해주세요.'})
    t2_valet.log_store.append({'time': '2026-01-01 00:00:05', 'type': 'event', 'status': 'STOP', 'body': '자동 예약 중지'})
    with open_page(ui_context, t2_server, height=900) as (page, errors):
        page.goto(t2_server + '/t2-valet/')
        page.wait_for_function("() => document.getElementById('log-count').textContent === '6'")
        labels = page.locator('#log-body .cell-status').all_inner_texts()
        # 표는 최신 순이다: STOP, notification, ERROR, 200, 601, START
        assert labels == ['중지', '전송 실패', '연결 오류', '예약 성공', '중복 예약', '시작']
        rows = page.locator('#log-body tr.log-row')
        assert rows.nth(4).locator('.body-cell').inner_text() == '입력하신 기간에 이미 중복된 예약이 존재합니다.'
        rows.nth(4).click()
        page.wait_for_function("() => document.getElementById('detail-overlay').classList.contains('open')")
        detail_text = page.locator('#detail-json').inner_text()
        assert '입력하신 기간에 이미 중복된 예약이 존재합니다.' in detail_text
        assert '601' in detail_text
        assert '상태 코드' in page.locator('.detail-section-title').all_inner_texts()
        assert not errors
