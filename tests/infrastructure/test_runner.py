"""Fault injection uses disposable repositories and independent outer deadlines."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import psutil
import pytest

from scripts.test_runner.evidence import POLICY
from scripts.test_runner.supervisor import Progress, live

SOURCE = Path(__file__).resolve().parents[2]
FAST = dict(startup=3., collection=1.5, slow=.6, test=1.2, gap=.6,
            final=.6, suite=8., term=.2, reap=.8, poll=.03)
DRIVER = '''import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from scripts.run_tests import execute
import json
raise SystemExit(execute(Path.cwd(), ['-q'], 'fault', 'harness', json.loads(sys.argv[2])))
'''


@pytest.fixture
def project(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True, timeout=10)
    subprocess.run(['git', '-C', str(tmp_path), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                    'commit', '--allow-empty', '-qm', 'fixture'], check=True, timeout=10)
    (tmp_path / '.gitignore').write_text('.test-runs/\n__pycache__/\n.pytest_cache/\n')
    (tmp_path / 'test_case.py').write_text('def test_ok(): pass\n')
    return tmp_path


def start(project, policy=FAST, extra_env=None):
    env = os.environ.copy()
    for key in list(env):
        if key.startswith(('TEST_RUN_', 'PYTEST_')):
            env.pop(key)
    env.pop('PYTHONPATH', None)
    env.update(PYTHONDONTWRITEBYTECODE='1')
    env.update(extra_env or {})
    return subprocess.Popen([sys.executable, '-c', DRIVER, str(SOURCE), json.dumps(policy)], cwd=project,
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def finish(child, project, timeout=20):
    try:
        out, err = child.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        child.kill()
        child.communicate(timeout=5)
        raise AssertionError('independent outer deadline exceeded')
    paths = sorted((project / '.test-runs').glob('*/result.json'))
    assert paths, (out, err)
    result = json.loads(paths[-1].read_text())
    assert result['cleanup']['ok'], (result['cleanup'], out, err)
    assert child.returncode == result['exit_code'], (out, err, result)
    return result, paths[-1].parent


def wait_status(project, predicate):
    until = time.monotonic() + 10
    while time.monotonic() < until:
        for path in (project / '.test-runs').glob('*/status.json'):
            state = json.loads(path.read_text())
            if predicate(state):
                return state, path.parent
        time.sleep(.03)
    raise AssertionError('status deadline')


@pytest.mark.parametrize('body,code,outcome,count', [
    ('def test_ok(): pass', 0, 'PASSED', 'passed'),
    ('def test_bad(): assert False', 1, 'FAILED', 'failed'),
    ('import pytest\n@pytest.mark.skip(reason="fixture")\ndef test_skip(): pass', 0, 'PASSED', 'skipped'),
    ('', 5, 'FAILED', None),
])
def test_normal_outcomes(project, body, code, outcome, count):
    (project / 'test_case.py').write_text(body)
    result, _ = finish(start(project), project)
    assert (result['exit_code'], result['outcome']) == (code, outcome)
    if count:
        assert result['counts'][count] == 1
    assert result['reusable'] == (count == 'passed')


# 감독기(Progress.event)가 마감을 거는 경로마다 한 건씩 둔다: ready→collection, protocol_start→test,
# protocol_end(last)→final, collection_end→gap. setup·teardown·훅 안의 멈춤도 모두 protocol_start가 건
# 같은 test 마감으로 끝나므로 한 건(출력이 계속 늘어도 마감이 늘지 않는 경우)으로 대표한다.
@pytest.mark.parametrize('phase,source,conftest', [
    ('collection', 'import time; time.sleep(30)', ''),
    ('test', 'import time\ndef test_hang():\n while True:\n  print("still printing", flush=True)\n  time.sleep(.01)', ''),
    ('final', 'def test_ok(): pass', 'import time\ndef pytest_sessionfinish(): time.sleep(30)'),
    ('gap', 'def test_hang(): pass', 'import time\ndef pytest_runtestloop(session): time.sleep(30)'),
])
def test_hang_deadlines(project, phase, source, conftest):
    (project / 'test_case.py').write_text(source)
    (project / 'conftest.py').write_text(conftest)
    result, run = finish(start(project), project)
    assert (result['outcome'], result['exit_code'], result['termination_reason']) == ('TIMED_OUT', 124, phase)
    if phase != 'gap':
        assert result['diagnostic_status'] == 'captured'
        assert (run / 'stacks.log').stat().st_size > 0


def test_startup_without_plugin(project):
    (project / 'pytest.py').write_text('import time; time.sleep(30)')
    result, _ = finish(start(project, extra_env={'PYTHONPATH': str(project)}), project)
    assert result['termination_reason'] == 'startup'
    assert result['diagnostic_status'] == 'unavailable'


def test_protocol_budget_is_cumulative(project):
    (project / 'test_case.py').write_text('import time\ndef test_slow(slow): time.sleep(.5)')
    (project / 'conftest.py').write_text('import pytest,time\n@pytest.fixture\ndef slow():\n time.sleep(.5)\n yield\n time.sleep(.5)')
    result, _ = finish(start(project), project)
    assert result['outcome'] == 'TIMED_OUT'
    assert result['phase'] == 'teardown'


def test_finite_marker_and_diagnostic_failure(project):
    (project / 'test_case.py').write_text('import pytest,time\n@pytest.mark.runner_timeout(2.5, "slow fixture")\ndef test_slow(): time.sleep(1.4)')
    child = start(project)
    _, run = wait_status(project, lambda s: s.get('phase') == 'call')
    # Unlinking the optional diagnostic file cannot invalidate mandatory evidence.
    (run / 'stacks.log').unlink()
    result, _ = finish(child, project)
    assert result['outcome'] == 'PASSED' and result['reusable']
    assert result['diagnostic_status'] == 'unavailable'


@pytest.mark.parametrize('marker', ['runner_timeout(0, "bad")', 'runner_timeout(float("inf"), "bad")',
                                    'runner_timeout(9, "over suite")', 'runner_timeout(2, "")', 'timeout(1)'])
def test_reject_timeout_contract(project, marker):
    (project / 'test_case.py').write_text(f'import pytest\n@pytest.mark.{marker}\ndef test_ok(): pass')
    result, _ = finish(start(project), project)
    assert result['outcome'] == 'INFRA_ERROR' and not result['reusable']


@pytest.mark.parametrize('detached', [False, True])
def test_child_residue_and_unrelated_process(project, detached):
    other = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    try:
        (project / 'test_case.py').write_text(f'''import subprocess,sys,time

def test_spawn():
 subprocess.Popen([sys.executable, '-c', 'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(30)'], start_new_session={detached})
 time.sleep(.3)
''')
        result, _ = finish(start(project), project)
        assert result['outcome'] == 'PASSED'
        assert result['children']
        assert all(live(p) is None for p in result['children'].values())
        assert other.poll() is None
    finally:
        other.terminate()
        other.wait(timeout=5)


@pytest.mark.parametrize('sig', [signal.SIGTERM, signal.SIGKILL])
def test_cli_death_cleans_up(project, sig):
    (project / 'test_case.py').write_text('import time\ndef test_hang(): time.sleep(30)')
    child = start(project)
    _, run = wait_status(project, lambda s: s.get('phase') == 'call')
    child.send_signal(sig)
    child.communicate(timeout=10)
    state, _ = wait_status(project, lambda s: s['lifecycle'] == 'FINISHED')
    assert state['outcome'] == 'CANCELLED'
    assert state['cleanup']['ok'] and live(state['pytest']) is None


def test_supervisor_death_recovered_next_run(project):
    (project / 'test_case.py').write_text('import time\ndef test_hang(): time.sleep(30)')
    child = start(project)
    state, run = wait_status(project, lambda s: s.get('phase') == 'call')
    os.kill(state['supervisor']['pid'], signal.SIGKILL)
    child.communicate(timeout=10)
    assert child.returncode == 125
    (project / 'test_case.py').write_text('def test_ok(): pass')
    result, _ = finish(start(project), project)
    recovered = json.loads((run / 'result.json').read_text())
    assert recovered['outcome'] == 'INTERRUPTED' and recovered['cleanup']['ok']
    assert live(state['pytest']) is None
    assert result['outcome'] == 'PASSED'


def test_lock_and_changed_inputs(project):
    (project / 'test_case.py').write_text('import time\ndef test_slow(): time.sleep(.6)')
    first = start(project)
    wait_status(project, lambda s: s.get('phase') == 'call')
    second = start(project)
    out, err = second.communicate(timeout=10)
    assert second.returncode != 0 and 'another test run' in err
    (project / 'changed.txt').write_text('input change')
    result, _ = finish(first, project)
    assert result['outcome'] == 'PASSED' and not result['reusable']
    assert not result['inputs_unchanged']


# 실제 실행기가 증거 파일 검사를 부르는지만 본다. 파일별 판정은 test_replaced_or_truncated_streams_are_incomplete가
# 프로세스 없이 확인하고, events.jsonl은 EvidenceFiles와 Events 두 검사를 모두 지난다.
@pytest.mark.parametrize('name', ['events.jsonl'])
def test_missing_evidence(project, name):
    (project / 'test_case.py').write_text('import time\ndef test_slow(): time.sleep(.5)')
    child = start(project)
    _, run = wait_status(project, lambda s: s.get('phase') == 'call')
    (run / name).unlink()
    result, _ = finish(child, project)
    assert result['outcome'] == 'INFRA_ERROR' and not result['reusable']


def test_missing_protocol_keeps_parent_deadline():
    p = Progress(10., FAST)
    p.event(dict(kind='ready', monotonic=10.1, diagnostic_ready=True))
    p.event(dict(kind='collected', monotonic=10.2, nodeids=['test_one']))
    p.event(dict(kind='collection_end', monotonic=10.3, failed=False))
    p.event(dict(kind='phase_start', monotonic=10.4, phase='setup'))
    assert p.expired(11.) == 'gap'


def test_stale_pid_does_not_own_current_process():
    assert live({'pid': os.getpid(), 'created': psutil.Process().create_time() - 100}) is None


@pytest.mark.runner_timeout(180, 'real default timeout acceptance; opt-in only')
@pytest.mark.skipif(os.environ.get('RUN_DEFAULT_TIMEOUT_ACCEPTANCE') != '1', reason='120-second policy acceptance is opt-in')
def test_default_policy_deadline(project):
    (project / 'test_case.py').write_text('import time\ndef test_hang(): time.sleep(300)')
    started = time.monotonic()
    result, _ = finish(start(project, POLICY), project, timeout=145)
    assert result['outcome'] == 'TIMED_OUT'
    assert 120 <= time.monotonic() - started < 140
    assert result['diagnostic_status'] == 'captured'


@pytest.mark.parametrize('options', [['-n', '2'], ['--timeout=1'], ['-o', 'timeout=1'], ['-p', 'no:scripts.test_runner.plugin']])
def test_reject_command_overrides(options):
    from scripts.run_tests import validate
    with pytest.raises(ValueError):
        validate(options)


def test_reject_environment_timeout(monkeypatch):
    from scripts.run_tests import validate
    monkeypatch.setenv('PYTEST_TIMEOUT', '2')
    with pytest.raises(ValueError):
        validate([])


def test_reject_ini_timeout(project):
    (project / 'pytest.ini').write_text('[pytest]\ntimeout = 1\n')
    result, _ = finish(start(project), project)
    assert result['outcome'] == 'INFRA_ERROR'


def test_missing_result_detected_by_status(project):
    _, run = finish(start(project), project)
    (run / 'result.json').unlink()
    check = subprocess.run([sys.executable, str(SOURCE / 'scripts/run_tests.py'), '--status', run.name],
                           cwd=project, capture_output=True, text=True, timeout=15)
    assert check.returncode == 125, check.stderr
    assert json.loads(check.stdout)['outcome'] == 'INFRA_ERROR'


def test_evidence_sequence_loss(project):
    (project / 'test_case.py').write_text('import time\ndef test_slow(): time.sleep(.5)')
    child = start(project)
    _, run = wait_status(project, lambda s: s.get('phase') == 'call')
    with (run / 'events.jsonl').open('a') as stream:
        stream.write(json.dumps({'seq': 999, 'kind': 'injected', 'monotonic': time.monotonic()}) + '\n')
    result, _ = finish(child, project)
    assert result['outcome'] == 'INFRA_ERROR' and not result['reusable']


def test_recovery_does_not_adopt_reused_group_pid():
    from scripts.test_runner.supervisor import discover
    known = {}
    discover({'pid': os.getpid(), 'created': psutil.Process().create_time() - 100}, known, recovering=True)
    assert not known


def test_timeout_named_nodeid_is_allowed():
    from scripts.run_tests import validate
    validate(['tests/infrastructure/test_runner.py::test_default_policy_deadline', '-k', 'timeout'])


def test_review_includes_untracked_and_rejects_changed_inputs(project):
    from scripts.review_snapshot import package
    result, run = finish(start(project), project)
    output = package(project, run.name, 'HEAD')
    assert 'test_case.py' in (output / 'untracked.patch').read_text()
    snapshot = json.loads((output / 'snapshot.json').read_text())
    assert snapshot['fingerprint'] == result['fingerprint']
    (project / 'test_case.py').write_text('def test_changed(): pass')
    with pytest.raises(ValueError, match='do not match'):
        package(project, run.name, 'HEAD')


def test_replaced_or_truncated_streams_are_incomplete(tmp_path):
    from scripts.test_runner.supervisor import EvidenceFiles
    for name in ('manifest.json', 'events.jsonl', 'stdout.log', 'stderr.log'):
        (tmp_path / name).write_text('existing evidence\n')
    watcher = EvidenceFiles(tmp_path)
    (tmp_path / 'stdout.log').write_text('')
    (tmp_path / 'stderr.log').rename(tmp_path / 'unlinked.log')
    (tmp_path / 'stderr.log').write_text('existing evidence\n')
    watcher.check()
    assert watcher.errors == {'stdout.log replaced/truncated', 'stderr.log replaced/truncated'}


def test_overall_deadline_does_not_extend(project):
    (project / 'test_case.py').write_text('import time\ndef test_hang(): time.sleep(30)')
    result, _ = finish(start(project, {**FAST, 'suite': 1.}), project)
    assert result['outcome'] == 'TIMED_OUT' and result['termination_reason'] == 'suite'


def test_sigterm_ignoring_pytest_is_killed(project):
    (project / 'test_case.py').write_text('import signal,time\ndef test_hang():\n signal.signal(signal.SIGTERM,signal.SIG_IGN)\n time.sleep(30)')
    result, _ = finish(start(project), project)
    assert result['outcome'] == 'TIMED_OUT' and result['pytest_signal'] == signal.SIGKILL


def test_manifest_covers_deletion_mode_symlink_and_ignored_fixture(project):
    from scripts.test_runner.evidence import inputs
    tracked = project / 'tracked.py'
    tracked.write_text('original')
    subprocess.run(['git', 'add', 'tracked.py'], cwd=project, check=True, timeout=10)
    tracked.unlink()
    (project / '.gitignore').write_text((project / '.gitignore').read_text() + 'fixture.secret\n')
    fixture = project / 'fixture.secret'
    fixture.write_text('controlled test value')
    fixture.chmod(0o600)
    (project / 'linked').symlink_to('fixture.secret')
    first = inputs(project)
    assert first['files']['tracked.py'] == {'deleted': True}
    assert first['files']['fixture.secret']['mode'] == 0o600
    assert first['files']['linked']['target'] == 'fixture.secret'
    assert first['files']['linked']['sha256'] == first['files']['fixture.secret']['sha256']
    assert 'controlled test value' not in json.dumps(first)
    fixture.write_text('changed test value')
    assert inputs(project)['fingerprint'] != first['fingerprint']
