"""Observation only: no timers, loaded explicitly before project conftests."""
from datetime import datetime, timezone
import faulthandler
import json
import math
import os
from pathlib import Path
import signal
import time

import pytest

RUN = Path(os.environ['TEST_RUN_DIRECTORY'])
POLICY = json.loads(os.environ['TEST_RUN_POLICY'])
STREAM = (RUN / 'events.jsonl').open('a', buffering=1)
SEQ = 0
STACK = None


def emit(kind, **data):
    global SEQ
    SEQ += 1
    STREAM.write(json.dumps(dict(schema_version='1', run_id=RUN.name,
                                 utc=datetime.now(timezone.utc).isoformat(),
                                 seq=SEQ, worker_id='master', kind=kind,
                                 monotonic=time.monotonic(), **data)) + '\n')
    STREAM.flush()


try:
    STACK = (RUN / 'stacks.log').open('a')
    faulthandler.register(signal.SIGUSR1, file=STACK, all_threads=True)
    emit('ready', diagnostic_ready=True)
except (OSError, RuntimeError) as error:
    emit('ready', diagnostic_ready=False, diagnostic_error=str(error))


def pytest_configure(config):
    config.addinivalue_line('markers', 'runner_timeout(seconds, reason): finite protocol budget')
    unsupported = [key for key in config.inicfg if key.startswith('timeout') or key == 'faulthandler_timeout' and float(config.inicfg[key]) != 0]
    if unsupported or config.pluginmanager.hasplugin('xdist') or config.pluginmanager.hasplugin('timeout'):
        emit('contract_error', reason='Unsupported timeout/xdist configuration: ' + str(unsupported))
        raise pytest.UsageError('run_tests: timeout/xdist configuration is unsupported')
    emit('configured', plugins=sorted(str(name) for name, _ in config.pluginmanager.list_name_plugin()))


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_collection(session):
    emit('collection_start')
    yield
    emit('collection_end', failed=session.testsfailed)


def pytest_collectstart(collector):
    emit('collect', nodeid=collector.nodeid)


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(session, config, items):
    budgets = {}
    for item in items:
        if item.get_closest_marker('timeout'):
            emit('contract_error', reason='pytest-timeout marker: ' + item.nodeid)
            raise pytest.UsageError('Use runner_timeout(seconds, reason), not timeout')
        marker = item.get_closest_marker('runner_timeout')
        seconds, reason = POLICY['test'], 'default'
        if marker:
            try:
                if marker.kwargs:
                    seconds = marker.kwargs['seconds']
                    reason = marker.kwargs['reason']
                    if marker.args or set(marker.kwargs) != {'seconds', 'reason'}:
                        raise ValueError()
                else:
                    seconds, reason = marker.args
                if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or not 0 < seconds <= POLICY['suite'] or not isinstance(reason, str) or not reason.strip():
                    raise ValueError()
            except (KeyError, TypeError, ValueError):
                emit('contract_error', reason='Invalid runner_timeout: ' + item.nodeid)
                raise pytest.UsageError('runner_timeout requires finite positive seconds <= suite and a reason')
        item._runner_budget = seconds
        budgets[item.nodeid] = {'seconds': seconds, 'reason': reason}
    emit('collected', nodeids=[i.nodeid for i in items], budgets=budgets)


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_protocol(item, nextitem):
    emit('protocol_start', nodeid=item.nodeid, timeout=item._runner_budget)
    yield
    emit('protocol_end', nodeid=item.nodeid, last=nextitem is None)


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_setup(item):
    emit('phase_start', nodeid=item.nodeid, phase='setup')
    yield
    emit('phase_end', nodeid=item.nodeid, phase='setup')


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_call(item):
    emit('phase_start', nodeid=item.nodeid, phase='call')
    yield
    emit('phase_end', nodeid=item.nodeid, phase='call')


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_teardown(item, nextitem):
    emit('phase_start', nodeid=item.nodeid, phase='teardown')
    yield
    emit('phase_end', nodeid=item.nodeid, phase='teardown')


def pytest_runtest_logreport(report):
    emit('report', nodeid=report.nodeid, phase=report.when, outcome=report.outcome,
         xfail=hasattr(report, 'wasxfail'), duration=report.duration)


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_sessionfinish(session, exitstatus):
    emit('session_finishing', exitstatus=int(exitstatus))
    yield
    emit('session_end', exitstatus=int(exitstatus))
