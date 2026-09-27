"""Bound fixture shutdown and attempt every cleanup even after a failure."""
import threading


def cleanup_ui_server(server, thread, runtime, timeout=3):
    errors = []

    def attempt(name, action):
        raised = []

        def invoke():
            try:
                action()
            except Exception as error:
                raised.append(error)

        worker = threading.Thread(target=invoke, name='ui-cleanup-' + name, daemon=True)
        worker.start()
        worker.join(timeout)
        if worker.is_alive():
            errors.append(RuntimeError(name + ' cleanup thread did not stop'))
        errors.extend(raised)

    if server is not None:
        if thread is not None and thread.is_alive():
            attempt('shutdown', server.shutdown)
        if thread is not None:
            try:
                thread.join(timeout)
                if thread.is_alive():
                    errors.append(RuntimeError('UI server thread did not stop'))
            except Exception as error:
                errors.append(error)
        attempt('server_close', server.server_close)
    attempt('runtime.close', runtime.close)
    owned_threads = (getattr(runtime, 'thread', None),
                     getattr(getattr(runtime, 'outbox', None), 'thread', None))
    for owned in owned_threads:
        if owned is not None and owned.is_alive():
            errors.append(RuntimeError('Runtime thread did not stop: ' + owned.name))
    if errors:
        raise ExceptionGroup('UI fixture cleanup failed', errors)
