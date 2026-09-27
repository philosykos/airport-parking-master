#!/usr/bin/env python3
"""Public entry point for supervised sequential pytest runs."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import time
import uuid

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from scripts.test_runner.evidence import POLICY, VERSION, atomic, digest, environment, git, inputs, utc


def validate(args):
    tokens = args + shlex.split(os.environ.get('PYTEST_ADDOPTS', ''))
    if any(k.startswith('PYTEST_TIMEOUT') for k in os.environ):
        raise ValueError('PYTEST_TIMEOUT environment is unsupported')
    for token in tokens:
        if (token.startswith(('--timeout', '--dist', '--numprocesses', '--tx', '--looponfail'))
                or token == '-f' or token.startswith('-n')
                or token.startswith(('timeout=', 'timeout_method=', 'timeout_func_only=', 'faulthandler_timeout='))
                or token.startswith('no:') or token in ('-p', '--override-ini', '-o') or token.startswith('-o')):
            raise ValueError('Unsupported timeout, parallel or plugin/config override: ' + token)
    if os.environ.get('PYTEST_PLUGINS'):
        raise ValueError('PYTEST_PLUGINS is unsupported; use project conftest plugins')


def recover_all(base, lock):
    for run in sorted(base.iterdir()):
        if run.is_dir() and (run / 'manifest.json').exists():
            status = run / 'status.json'
            if status.exists():
                data = json.loads(status.read_text())
                if (data.get('lifecycle') == 'FINISHED' and data.get('cleanup', {}).get('ok')
                        and all((run / name).is_file() for name in ('result.json', 'events.jsonl', 'stdout.log', 'stderr.log'))):
                    continue
            result = subprocess.run([sys.executable, '-m', 'scripts.test_runner.supervisor', '--recover', str(run)],
                                    env=child_env(), pass_fds=(lock,), timeout=25, capture_output=True, text=True)
            if result.returncode:
                raise RuntimeError(result.stderr[-2000:])


def child_env():
    env = os.environ.copy()
    env['PYTHONPATH'] = str(SOURCE) + os.pathsep + env.get('PYTHONPATH', '')
    return env


def execute(root, args, task, agent, policy=None, rerun_reason=None):
    """Private policy injection exists only for the isolated fault harness."""
    validate(args)
    policy = dict(POLICY if policy is None else policy)
    base = root / '.test-runs'
    base.mkdir(exist_ok=True)
    with (base / 'lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('another test run owns this worktree')
        recover_all(base, lock.fileno())
        run_id = time.strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:10]
        run = base / run_id
        snapshot = inputs(root)
        env = environment()
        env['implementation'] = {str(p.relative_to(SOURCE)): digest(p.read_text())
                                 for p in (SOURCE / 'scripts').rglob('*.py')}
        # These settings may affect tests; store only digests, never raw values.
        env['extra_input_hashes'] = {k: digest(os.environ[k]) for k in ('PYTHONPATH', 'PYTEST_ADDOPTS', 'PLAYWRIGHT_BROWSERS_PATH') if k in os.environ}
        blockers = []
        if any(entry.get('external') or entry.get('kind') == 'non-file'
               or 'target' in entry and 'sha256' not in entry for entry in snapshot['files'].values()):
            blockers.append('external/directory symlinks or submodules require input review')
        if os.environ.get('PLAYWRIGHT_BROWSERS_PATH') or os.environ.get('PYTHONPATH'):
            blockers.append('external browser/import paths require input review')
        if (root / '.env').exists() or (root / 'user_data.json').exists() or any(k in os.environ for k in ('RESERVATION_PASSWORD', 'TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID')):
            blockers.append('secret/application inputs require controlled test values and acceptance review')
        command = ['-p', 'no:timeout', '-p', 'no:xdist', '-p', 'no:xdist.looponfail', '-p', 'no:looponfail',
                   '-p', 'scripts.test_runner.plugin', *args]
        manifest = dict(schema_version=VERSION, run_id=run_id, task_id=task, agent_id=agent,
                        root=str(root), created_at=utc(), head=git(root, 'rev-parse', 'HEAD').decode().strip(),
                        inputs=snapshot, environment=env, policy=policy, pytest_args=command,
                        command=[sys.executable, '-m', 'pytest', *command], reuse_blockers=blockers,
                        rerun_reason=rerun_reason,
                        write_contract='no input writes until FINISHED; restored writes are not detectable')
        manifest['fingerprint'] = digest(dict(inputs=snapshot, environment=env, command=command, policy=policy))
        run.mkdir()
        atomic(run / 'manifest.json', manifest)
        control_r, control_w = os.pipe()
        with (run / 'supervisor.log').open('ab') as log:
            supervisor = subprocess.Popen([sys.executable, '-m', 'scripts.test_runner.supervisor',
                                           str(run), str(control_r), str(lock.fileno())],
                                          env=child_env(), stdout=log, stderr=log, start_new_session=True,
                                          pass_fds=(control_r, lock.fileno()))
        os.close(control_r)
        print('run_id=' + run_id, flush=True)
        previous = {}
        def cancel(signum, frame):
            try:
                os.write(control_w, b'C')
            except OSError:
                pass
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, cancel)
        try:
            last = time.monotonic()
            while supervisor.poll() is None:
                if time.monotonic() - last >= 30:
                    path = run / 'status.json'
                    if path.exists():
                        state = json.loads(path.read_text())
                        print(f"{run_id}: {state['lifecycle']} {state.get('phase')} {state.get('nodeid')}", flush=True)
                    last = time.monotonic()
                time.sleep(.1)
        finally:
            os.close(control_w)
            for signum, handler in previous.items():
                signal.signal(signum, handler)
        result = run / 'result.json'
        if not result.exists():
            print('supervisor exited without result; next run/status will recover', file=sys.stderr)
            return 125
        state = json.loads(result.read_text())
        print(f"{state['outcome']} {state['counts']} evidence={run}", flush=True)
        for name in ('stdout.log', 'stderr.log'):
            path = run / name
            if path.exists():
                with path.open('rb') as stream:
                    stream.seek(max(0, path.stat().st_size - 4000))
                    print(stream.read(4000).decode(errors='replace'), end='')
        return state['exit_code']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task-id', default='manual')
    parser.add_argument('--agent-id', default='local')
    parser.add_argument('--status', metavar='RUN_ID')
    parser.add_argument('--rerun-reason', help='Reason for repeated verification, e.g. required-by-finishing')
    parser.add_argument('pytest_args', nargs=argparse.REMAINDER)
    options = parser.parse_args()
    try:
        root = Path(git(Path.cwd(), 'rev-parse', '--show-toplevel').decode().strip())
        if options.status:
            if not re.fullmatch(r'[A-Za-z0-9_-]+', options.status):
                raise ValueError('invalid run id')
            base = root / '.test-runs'
            path = base / options.status / 'status.json'
            with (base / 'lock').open('a+') as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    pass
                else:
                    recover_all(base, lock.fileno())
            state = json.loads(path.read_text())
            print(json.dumps(state, indent=2))
            if state['lifecycle'] == 'FINISHED' and not path.with_name('result.json').exists():
                return 125
            return state.get('exit_code', 0)
        args = options.pytest_args
        if args[:1] == ['--']:
            args = args[1:]
        return execute(root, args, options.task_id, options.agent_id, rerun_reason=options.rerun_reason)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print('run_tests: ' + str(error), file=sys.stderr)
        return 125


if __name__ == '__main__':
    raise SystemExit(main())
