"""Shared UI server/page scopes with bounded and exception-safe cleanup."""
import threading
from contextlib import contextmanager

from werkzeug.serving import make_server

from app import app as flask_app
from tests.support.ui_cleanup import cleanup_ui_server

LOCK_PROBE = ("el => { const s = getComputedStyle(el); "
              "return {bg: s.backgroundColor, color: s.color, border: s.borderColor, cursor: s.cursor}; }")

LOCKED_LOOK = {'bg': 'rgb(243, 244, 245)', 'color': 'rgb(118, 118, 131)', 'border': 'rgb(228, 229, 234)'}

# 화면 테스트용 폴링·새로고침 주기(ms). 운영 기본값(app.py)과 같을 필요는 없고, 화면이 설정값을 따른다는 것만
# test_screens_poll_at_configured_intervals가 확인한다. 느린 기계에서도 응답이 다음 주기 전에 오도록 여유를 둔다.
UI_INTERVALS = {'GIMPO_POLL_MS': 100, 'T2_POLL_MS': 100, 'SETTINGS_REFRESH_MS': 200}


@contextmanager
def run_app_server(app=flask_app, runtime=None):
    # This scope owns partial setup as well as server and optional runtime teardown.
    server = thread = None
    try:
        server = make_server('127.0.0.1', 0, app, threaded=True)
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.05), daemon=True)
        thread.start()
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        cleanup_ui_server(server, thread, runtime)


@contextmanager
def open_page(ui_context, base, width=1280, height=1000, init_script=None):
    # Browser ownership stays in ui_browser; every invocation owns a fresh context.
    with ui_context(viewport={'width': width, 'height': height}) as context:
        page = context.new_page()
        page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
        if init_script:
            page.add_init_script(init_script)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        # CSP 위반은 예외가 아니라 콘솔 오류로만 나온다. 막힌 스크립트가 조용히 빠지지 않게 함께 모은다.
        page.on('console', lambda message: errors.append(message.text)
                if message.type == 'error' and 'Content Security Policy' in message.text else None)
        yield page, errors
