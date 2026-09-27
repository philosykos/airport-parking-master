import threading
from types import SimpleNamespace

import pytest

from tests.ui_cleanup import cleanup_ui_server


def test_cleanup_continues_after_shutdown_error():
    called = []
    stop = threading.Event()
    thread = threading.Thread(target=lambda: stop.wait(2), daemon=True)
    thread.start()

    def shutdown():
        stop.set()
        raise ValueError('shutdown failed')

    server = SimpleNamespace(shutdown=shutdown, server_close=lambda: called.append('server'))
    runtime = SimpleNamespace(close=lambda: called.append('runtime'))
    with pytest.raises(ExceptionGroup, match='cleanup failed') as caught:
        cleanup_ui_server(server, thread, runtime, timeout=.2)
    assert called == ['server', 'runtime']
    assert any(isinstance(e, ValueError) for e in caught.value.exceptions)


def test_stuck_shutdown_and_server_thread_are_reported():
    stop = threading.Event()
    thread = threading.Thread(target=stop.wait, daemon=True)
    thread.start()
    called = []
    server = SimpleNamespace(shutdown=stop.wait, server_close=lambda: called.append('server'))
    runtime = SimpleNamespace(close=lambda: called.append('runtime'))
    try:
        with pytest.raises(ExceptionGroup) as caught:
            cleanup_ui_server(server, thread, runtime, timeout=.03)
        assert len(caught.value.exceptions) == 2
        assert called == ['server', 'runtime']
    finally:
        stop.set()
        thread.join(1)


def test_partial_setup_still_closes_runtime():
    called = []
    cleanup_ui_server(None, None, SimpleNamespace(close=lambda: called.append('runtime')))
    assert called == ['runtime']


def test_runtime_close_cannot_silently_leave_its_thread():
    stop = threading.Event()
    thread = threading.Thread(target=stop.wait, daemon=True, name='runtime-worker')
    thread.start()
    runtime = SimpleNamespace(close=lambda: None, thread=thread)
    try:
        with pytest.raises(ExceptionGroup) as caught:
            cleanup_ui_server(None, None, runtime)
        assert 'runtime-worker' in str(caught.value.exceptions[0])
    finally:
        stop.set()
        thread.join(1)


def test_thread_start_failure_still_closes_server_and_runtime():
    called = []
    thread = threading.Thread(target=lambda: None)
    server = SimpleNamespace(shutdown=lambda: called.append('shutdown'),
                             server_close=lambda: called.append('server'))
    runtime = SimpleNamespace(close=lambda: called.append('runtime'))
    with pytest.raises(ExceptionGroup):
        cleanup_ui_server(server, thread, runtime)
    assert called == ['server', 'runtime']
