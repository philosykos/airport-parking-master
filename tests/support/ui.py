"""Shared UI server/page scopes with bounded and exception-safe cleanup."""
import threading
from contextlib import contextmanager

from werkzeug.serving import make_server

from app import app as flask_app
from tests.support.ui_cleanup import cleanup_ui_server


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
        yield page, errors
