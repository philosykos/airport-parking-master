"""T2·김포가 실행 중 입력칸을 잠글 때 한 가지 모양(잠긴 칸·읽기 전용 칸)으로 보이는지 확인한다."""
import pytest

from tests.gimpo.helpers import inputs
from tests.gimpo.test_ui import ui_server  # noqa: F401 (공통 픽스처)
from tests.support.ui import open_page

WIDTHS = [(1280, 900), (390, 844)]

LOCK_PROBE = ("el => { const s = getComputedStyle(el); "
              "return {bg: s.backgroundColor, color: s.color, border: s.borderColor, cursor: s.cursor}; }")
TOGGLE_PROBE = ("el => { const s = getComputedStyle(el); "
                "return {bg: s.backgroundColor, color: s.color, opacity: s.opacity}; }")

LOCKED_LOOK = {'bg': 'rgb(243, 244, 245)', 'color': 'rgb(118, 118, 131)', 'border': 'rgb(228, 229, 234)'}

GIMPO_INPUTS = ['#airportCode', '#parkingId', '#carNumber', '#phone', '#discountSelection',
                '#intervalSeconds', '#entryAt', '#exitAt']


def assert_locked(style, selector):
    assert style['bg'] == LOCKED_LOOK['bg'], selector
    assert style['color'] == LOCKED_LOOK['color'], selector
    assert style['border'] == LOCKED_LOOK['border'], selector
    assert style['cursor'] == 'not-allowed', selector


@pytest.mark.parametrize('width,height', WIDTHS)
def test_gimpo_running_job_locks_all_fields_with_one_look(ui_server, ui_context, width, height):
    base, runtime = ui_server
    runtime.create(inputs())
    with open_page(ui_context, base, width=width, height=height) as (page, errors):
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("() => document.getElementById('input-fields').disabled === true")
        for selector in GIMPO_INPUTS:
            assert_locked(page.locator(selector).evaluate(LOCK_PROBE), selector)
        toggle = page.locator('#entry-picker .td-toggle').evaluate(TOGGLE_PROBE)
        assert toggle['bg'] == LOCKED_LOOK['bg']
        assert toggle['color'] == LOCKED_LOOK['color']
        assert toggle['opacity'] == '1'
        assert not errors


@pytest.mark.parametrize('width,height', WIDTHS)
def test_gimpo_password_fields_look_locked_and_eye_button_stays_clickable(ui_server, ui_context, width, height):
    base, runtime = ui_server
    with open_page(ui_context, base, width=width, height=height) as (page, errors):
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("() => !document.getElementById('toggle-password').disabled")
        for selector in ['#reservationPassword', '#passwordConfirmation']:
            style = page.locator(selector).evaluate(LOCK_PROBE)
            assert style['bg'] == LOCKED_LOOK['bg'], selector
            assert style['color'] == LOCKED_LOOK['color'], selector
            assert style['border'] == LOCKED_LOOK['border'], selector
            assert style['cursor'] != 'not-allowed', selector
        assert page.locator('#toggle-password').is_enabled()
        assert page.locator('#toggle-password-confirmation').is_enabled()
        page.click('#toggle-password')
        assert page.locator('#reservationPassword').get_attribute('type') == 'text'
        assert not errors


def test_gimpo_locked_select_does_not_open_mobile_sheet(ui_server, ui_context):
    base, runtime = ui_server
    runtime.create(inputs())
    with open_page(ui_context, base, width=390, height=844) as (page, errors):
        page.goto(base + '/gimpo-parking/')
        page.wait_for_function("() => document.getElementById('input-fields').disabled === true")
        page.dispatch_event('#airportCode', 'touchend')
        page.evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        assert not page.evaluate("document.getElementById('select-picker-overlay').classList.contains('open')")
        assert not errors


@pytest.mark.parametrize('width,height', WIDTHS)
def test_t2_locks_inputs_while_running_and_restores_after_stop(t2_server, ui_context, width, height):
    with open_page(ui_context, t2_server, width=width, height=height) as (page, errors):
        page.goto(t2_server + '/t2-valet/')
        page.wait_for_load_state('networkidle')
        assert page.locator('#input-fields').evaluate('el => el.disabled') is False
        # PC 폭(961px+)은 흰 바탕(form.css PC 블록), 그 아래는 지금과 같은 회색 바탕이다.
        # 어느 쪽이든 잠긴 모양(회색 글자·not-allowed 커서)과는 다르다.
        unlocked = page.locator('#carNumber').evaluate(LOCK_PROBE)
        assert unlocked['bg'] == ('rgb(255, 255, 255)' if width >= 961 else 'rgb(243, 244, 245)')
        assert unlocked['color'] != LOCKED_LOOK['color']
        assert unlocked['cursor'] != 'not-allowed'
        page.fill('#name', '홍길동')
        page.fill('#phone', '01012345678')
        page.fill('#carNumber', '12가3456')
        page.fill('#carModel', '그랜저')
        page.select_option('#carBrand', 'HY')
        page.select_option('#carColor', 'WHITE')
        page.click('#btn-start')
        page.wait_for_function("() => document.getElementById('input-fields').disabled === true")
        # 바탕·테두리 색은 트랜지션(--duration-normal)을 타므로 최종 값이 될 때까지 기다린다.
        border_js = "sel => getComputedStyle(document.querySelector(sel)).borderColor === 'rgb(228, 229, 234)'"
        page.wait_for_function(border_js, arg='#carNumber')
        page.wait_for_function(border_js, arg='#carBrand')
        assert_locked(page.locator('#carNumber').evaluate(LOCK_PROBE), '#carNumber')
        assert_locked(page.locator('#carBrand').evaluate(LOCK_PROBE), '#carBrand')
        page.click('#btn-stop')
        page.wait_for_function("() => document.getElementById('input-fields').disabled === false")
        expected_bg = 'rgb(255, 255, 255)' if width >= 961 else 'rgb(243, 244, 245)'
        page.wait_for_function(
            "([sel, bg]) => getComputedStyle(document.querySelector(sel)).backgroundColor === bg",
            arg=['#carNumber', expected_bg])
        restored = page.locator('#carNumber').evaluate(LOCK_PROBE)
        assert restored['bg'] == expected_bg
        assert restored['color'] != LOCKED_LOOK['color']
        assert restored['cursor'] != 'not-allowed'
        assert not errors
