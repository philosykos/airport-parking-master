"""Real-browser state isolation and exceptional context teardown."""
import pytest


@pytest.mark.parametrize('iteration', range(2))
def test_context_state_and_exception_cleanup(ui_browser, ui_context, iteration):
    assert ui_browser.contexts == []
    with pytest.raises(RuntimeError, match='intentional test body failure'):
        with ui_context(timezone_id='America/New_York', viewport={'width': 390, 'height': 844}) as context:
            context.route('**/*', lambda route: route.fulfill(body='<title>isolated</title>'))
            page = context.new_page()
            page.goto('https://isolation.invalid/')
            assert page.evaluate('localStorage.length') == 0
            assert context.cookies() == []
            page.evaluate("localStorage.setItem('leftover', '1'); sessionStorage.setItem('leftover', '1')")
            context.add_cookies([{'name': 'leftover', 'value': '1', 'url': 'https://isolation.invalid/'}])
            context.add_init_script('window.leftover = true')
            page.evaluate("window.open('/popup')")
            raise RuntimeError('intentional test body failure')
    assert ui_browser.contexts == []
    with ui_context(timezone_id='Asia/Seoul') as context:
        context.route('**/*', lambda route: route.fulfill(body='<title>fresh</title>'))
        page = context.new_page()
        page.goto('https://isolation.invalid/')
        assert context.cookies() == []
        assert page.evaluate('[localStorage.length, sessionStorage.length, !!window.leftover]') == [0, 0, False]
        assert page.evaluate('Intl.DateTimeFormat().resolvedOptions().timeZone') == 'Asia/Seoul'
        assert page.viewport_size == {'width': 1280, 'height': 720}
        assert len(context.pages) == 1
    assert ui_browser.contexts == []


def test_shared_page_helper_keeps_browser_and_closes_each_context(ui_browser, ui_context):
    from tests.support.ui import open_page
    with pytest.raises(RuntimeError, match='body failed'):
        with open_page(ui_context, 'https://isolation.invalid', width=390,
                       init_script='window.fromFirstContext = true') as (page, errors):
            page.route('https://isolation.invalid/', lambda route: route.fulfill(body='<title>first</title>'))
            page.goto('https://isolation.invalid/')
            page.evaluate("localStorage.setItem('leftover', '1')")
            assert page.evaluate('window.fromFirstContext')
            assert not errors
            raise RuntimeError('body failed')
    assert ui_browser.is_connected() and ui_browser.contexts == []
    with open_page(ui_context, 'https://isolation.invalid') as (page, errors):
        page.route('https://isolation.invalid/', lambda route: route.fulfill(body='<title>fresh</title>'))
        page.goto('https://isolation.invalid/')
        assert page.evaluate('[localStorage.length, !!window.fromFirstContext]') == [0, False]
        assert page.viewport_size == {'width': 1280, 'height': 1000}
        assert not errors
    assert ui_browser.is_connected() and ui_browser.contexts == []
