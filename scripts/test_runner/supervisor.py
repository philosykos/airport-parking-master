"""Sole state/result writer. Owns deadlines, cancellation and process cleanup."""
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time

import psutil

from .evidence import VERSION, atomic, environment, inputs, utc


def identity(pid):
    p = psutil.Process(pid)
    return {'pid': pid, 'created': p.create_time()}


def live(record):
    try:
        p = psutil.Process(record['pid'])
        return p if p.create_time() == record['created'] and p.status() != psutil.STATUS_ZOMBIE else None
    except psutil.NoSuchProcess:
        return None


def discover(owner, known, recovering=False):
    """Snapshot identities while parentage/group ownership is still observable."""
    parent = live(owner)
    try:
        candidates = parent.children(recursive=True) if parent else []
    except psutil.NoSuchProcess:
        candidates = []
    # Never adopt a new process group merely because its leader reused an old PID.
    try:
        if psutil.Process(owner['pid']).create_time() != owner['created']:
            return
    except psutil.NoSuchProcess:
        pass
    witnessed = parent is not None
    for record in known.values():
        member = live(record)
        if member:
            try:
                witnessed = witnessed or os.getpgid(member.pid) == owner['pid']
            except ProcessLookupError:
                pass
    # A dead group leader can leave reparented children in its session.
    for p in psutil.process_iter(['pid', 'create_time']):
        try:
            if os.getpgid(p.pid) == owner['pid'] and p.create_time() >= owner['created']:
                if recovering and not witnessed:
                    raise RuntimeError('unverified surviving process group')
                candidates.append(p)
        except (ProcessLookupError, psutil.NoSuchProcess):
            pass
    for p in candidates:
        try:
            known[str(p.pid)] = identity(p.pid)
        except psutil.NoSuchProcess:
            pass


def send(record, sig):
    p = live(record)
    if p:
        # psutil send_signal also checks identity before signalling.
        try:
            p.send_signal(sig)
        except psutil.NoSuchProcess:
            pass


def cleanup(owner, known, policy, child=None, recovering=False):
    errors = []
    if not owner:
        return {'ok': True, 'remaining': [], 'errors': []}
    try:
        discover(owner, known, recovering)
        records = {str(owner['pid']): owner, **known}
        for sig, budget in ((signal.SIGTERM, policy['term']), (signal.SIGKILL, policy['reap'])):
            for record in records.values():
                send(record, sig)
            until = time.monotonic() + budget
            while time.monotonic() < until:
                if child:
                    child.poll()
                discover(owner, known, recovering)
                for key, record in known.items():
                    if key not in records:
                        records[key] = record
                        send(record, sig)
                if not any(live(r) for r in records.values()):
                    return {'ok': True, 'remaining': [], 'errors': errors}
                time.sleep(min(policy['poll'], .1))
        remaining = [r for r in records.values() if live(r)]
    except (OSError, psutil.Error, RuntimeError) as error:
        errors.append(str(error))
        remaining = list(known.values())
    return {'ok': not errors and not remaining, 'remaining': remaining, 'errors': errors}


class EvidenceFiles:
    """Detect removed/replaced/truncated mandatory streams between observations."""
    def __init__(self, run):
        self.run = run
        self.seen = {}
        self.errors = set()
        self.check()

    def check(self):
        for name in ('manifest.json', 'events.jsonl', 'stdout.log', 'stderr.log'):
            try:
                stat = (self.run / name).stat()
                current = (stat.st_dev, stat.st_ino, stat.st_size)
                previous = self.seen.get(name)
                if previous and (current[:2] != previous[:2] or current[2] < previous[2]):
                    self.errors.add(name + ' replaced/truncated')
                self.seen[name] = current
            except OSError:
                self.errors.add(name + ' missing/unreadable')


class Events:
    def __init__(self, path):
        self.path, self.offset, self.seq = path, 0, 0
        self.errors = []

    def read(self):
        events = []
        try:
            with self.path.open('rb') as f:
                if f.seek(0, 2) < self.offset:
                    self.errors.append('events truncated')
                    return events
                f.seek(self.offset)
                # Bound each poll; never read an unbounded growing log.
                data = f.read(1024 * 1024)
        except FileNotFoundError:
            self.errors.append('events missing')
            return events
        for line in data.splitlines(keepends=True):
            if not line.endswith(b'\n'):
                break
            self.offset += len(line)
            try:
                event = json.loads(line)
                if event['seq'] != self.seq + 1:
                    self.errors.append('event sequence gap/reordering')
                self.seq = event['seq']
                events.append(event)
            except (ValueError, KeyError, TypeError):
                self.errors.append('invalid event')
        return events


class Progress:
    def __init__(self, start, policy):
        self.policy = policy
        self.data = dict(phase='startup', nodeid=None, phase_started=start,
                         test_started=None, effective_timeout=None, last_transition=start)
        self.deadlines = {'suite': start + policy['suite'], 'startup': start + policy['startup']}
        self.counts = dict(passed=0, failed=0, skipped=0, xfailed=0, xpassed=0, errors=0)
        self.nodeids, self.plugins, self.reports = [], [], []
        self.ready = self.ended = False
        self.contract_errors = []
        self.diag_ready = False
        self.diag_error = None
        self.last_event_time = start

    def expired(self, now):
        reason, deadline = min(self.deadlines.items(), key=lambda pair: pair[1])
        return reason if now >= deadline else None

    def event(self, e):
        t, kind, p = e['monotonic'], e['kind'], self.policy
        self.last_event_time = t
        if kind == 'ready':
            self.ready, self.diag_ready = True, e['diagnostic_ready']
            self.diag_error = e.get('diagnostic_error')
            self.deadlines.pop('startup', None)
            self.deadlines['collection'] = t + p['collection']
            self.data.update(phase='collection', phase_started=t)
        elif kind == 'configured':
            self.plugins = e['plugins']
        elif kind == 'collect':
            self.data['nodeid'] = e['nodeid']
        elif kind == 'collected':
            self.nodeids = e['nodeids']
        elif kind == 'collection_end':
            self.deadlines.pop('collection', None)
            final = e['failed'] or not self.nodeids
            self.deadlines['final' if final else 'gap'] = t + p['final' if final else 'gap']
            self.data.update(phase='final' if final else 'between_tests', phase_started=t)
        elif kind == 'protocol_start':
            self.deadlines.pop('gap', None)
            self.deadlines['test'] = t + e['timeout']
            self.data.update(nodeid=e['nodeid'], phase='protocol', phase_started=t,
                             test_started=t, effective_timeout=e['timeout'])
        elif kind == 'phase_start':
            self.data.update(phase=e['phase'], phase_started=t)
        elif kind == 'protocol_end':
            self.deadlines.pop('test', None)
            key = 'final' if e['last'] else 'gap'
            self.deadlines[key] = t + p[key]
            self.data.update(phase='final' if e['last'] else 'between_tests', phase_started=t,
                             test_started=None)
        elif kind == 'session_finishing':
            self.deadlines.pop('collection', None)
            self.deadlines.pop('gap', None)
            self.deadlines.setdefault('final', t + p['final'])
            self.data.update(phase='final', phase_started=t)
        elif kind == 'session_end':
            self.ended = True
        elif kind == 'contract_error':
            self.contract_errors.append(e['reason'])
        elif kind == 'report':
            self.reports.append(e)
            outcome = e['outcome']
            if e['xfail']:
                self.counts['xfailed' if outcome == 'skipped' else 'xpassed'] += 1
            elif e['phase'] == 'call' or outcome in ('failed', 'skipped'):
                key = 'errors' if e['phase'] != 'call' and outcome == 'failed' else outcome
                self.counts[key] += 1
        self.data['last_transition'] = t


def supervise(run, control, lock):
    manifest = json.loads((run / 'manifest.json').read_text())
    policy, root = manifest['policy'], Path(manifest['root'])
    state = dict(schema_version=VERSION, run_id=run.name, task_id=manifest['task_id'],
                 agent_id=manifest['agent_id'], lifecycle='STARTING', started_at=utc(),
                 supervisor=identity(os.getpid()), children={}, diagnostic_requests=[],
                 diagnostic_status='not_requested', evidence_errors=[])
    child = None
    owner = None
    reason = outcome = None
    progress = None
    reader = watcher = None
    try:
        for name in ('events.jsonl', 'stdout.log', 'stderr.log'):
            (run / name).touch()
        env = os.environ.copy()
        env.update(TEST_RUN_DIRECTORY=str(run), TEST_RUN_POLICY=json.dumps(policy),
                   PYTHONDONTWRITEBYTECODE='1', PYTEST_DEBUG_TEMPROOT=str(run))
        gate_r, gate_w = os.pipe()
        started = time.monotonic()
        progress = Progress(started, policy)
        reader = Events(run / 'events.jsonl')
        watcher = EvidenceFiles(run)
        with (run / 'stdout.log').open('ab') as out, (run / 'stderr.log').open('ab') as err:
            child = subprocess.Popen([sys.executable, str(Path(__file__).with_name('child.py')), str(gate_r),
                                      *manifest['pytest_args']], cwd=root, env=env,
                                     stdout=out, stderr=err, start_new_session=True, pass_fds=(gate_r,))
        os.close(gate_r)
        owner = identity(child.pid)
        state.update(pytest=owner, process_group=child.pid, lifecycle='RUNNING',
                     spawn_monotonic=started, overall_deadline=started + policy['suite'])
        atomic(run / 'status.json', state)
        os.write(gate_w, b'G')
        os.close(gate_w)
        requested = set()
        while True:
            now = time.monotonic()
            watcher.check()
            discover(owner, state['children'])
            for event in reader.read():
                expired = progress.expired(event['monotonic'])
                if expired and outcome is None:
                    outcome, reason = 'TIMED_OUT', expired
                if event['monotonic'] < progress.last_event_time or event['monotonic'] > now + 1:
                    reader.errors.append('invalid event clock')
                progress.event(event)
            expired = progress.expired(now)
            if expired and outcome is None:
                outcome, reason = 'TIMED_OUT', expired
            if select.select([control], [], [], 0)[0]:
                os.read(control, 1024)
                if outcome is None:
                    outcome, reason = 'CANCELLED', 'owner disconnected or cancelled'
            state.update(progress.data, deadlines=progress.deadlines, event_sequence=reader.seq,
                         heartbeat=utc(), slow=bool(requested))
            candidates = [('phase', progress.data['phase_started'])]
            if progress.data['test_started'] is not None:
                candidates.append(('test', progress.data['test_started']))
            threshold = (progress.data['effective_timeout'] / 2 if progress.data['test_started'] is not None
                         else policy['slow'])
            for kind, start in candidates:
                key = (kind, start)
                if now - start >= threshold and key not in requested:
                    requested.add(key)
                    entry = dict(kind=kind, requested_monotonic=now, phase=progress.data['phase'])
                    if progress.diag_ready and child.poll() is None:
                        try:
                            send(owner, signal.SIGUSR1)
                            entry['status'] = 'requested'
                        except (OSError, psutil.Error) as error:
                            entry.update(status='unavailable', reason=str(error))
                    else:
                        entry.update(status='unavailable', reason=progress.diag_error or 'handler not ready/process exited')
                    state['diagnostic_requests'].append(entry)
                    state['diagnostic_status'] = entry['status']
            try:
                if state['diagnostic_requests'] and (run / 'stacks.log').stat().st_size:
                    state.setdefault('diagnostic_captured_monotonic', time.monotonic())
            except OSError:
                pass
            atomic(run / 'status.json', state)
            if outcome or child.poll() is not None:
                break
            time.sleep(policy['poll'])
    except Exception as error:
        outcome, reason = outcome or 'INFRA_ERROR', repr(error)
        state['evidence_errors'].append(repr(error))
    finally:
        state.update(lifecycle='STOPPING', outcome=outcome, termination_reason=reason)
        try:
            atomic(run / 'status.json', state)
        finally:
            cleaned = cleanup(owner, state['children'], policy, child)
        code = child.poll() if child else None
        if reader:
            for event in reader.read():
                if not outcome and progress.expired(event['monotonic']):
                    outcome, reason = 'TIMED_OUT', progress.expired(event['monotonic'])
                progress.event(event)
            state['evidence_errors'].extend(reader.errors)
            if (run / 'events.jsonl').exists() and (run / 'events.jsonl').stat().st_size != reader.offset:
                state['evidence_errors'].append('incomplete final event')
        if watcher:
            watcher.check()
            state['evidence_errors'].extend(sorted(watcher.errors))
        for name in ('manifest.json', 'events.jsonl', 'stdout.log', 'stderr.log'):
            if not (run / name).is_file():
                state['evidence_errors'].append(name + ' missing')
        if not progress or not progress.ready or not progress.ended:
            state['evidence_errors'].append('incomplete plugin session')
        if progress and progress.contract_errors:
            state['evidence_errors'].extend(progress.contract_errors)
        unchanged = environment_unchanged = False
        try:
            after = inputs(root)
            atomic(run / 'manifest-after.json', after)
            unchanged = after['fingerprint'] == manifest['inputs']['fingerprint']
            env_after = json.loads(json.dumps(environment()))
            atomic(run / 'environment-after.json', env_after)
            environment_unchanged = all(manifest['environment'].get(k) == v for k, v in env_after.items())
        except Exception as error:
            state['evidence_errors'].append('final manifest: ' + str(error))
        state['evidence_errors'] = sorted(set(state['evidence_errors']))
        if outcome is None:
            outcome = 'INFRA_ERROR' if state['evidence_errors'] else ('PASSED' if code == 0 else 'FAILED')
        if not cleaned['ok'] and outcome == 'PASSED':
            outcome = 'INFRA_ERROR'
        exit_code = {'TIMED_OUT': 124, 'CANCELLED': 130, 'INFRA_ERROR': 125}.get(outcome, code if code is not None and code >= 0 else 125)
        if not cleaned['ok']:
            exit_code = 125
        counts = progress.counts if progress else {}
        reuse_reasons = list(manifest['reuse_blockers'])
        if not unchanged:
            reuse_reasons.append('inputs changed')
        if not environment_unchanged:
            reuse_reasons.append('environment changed or incomplete')
        if any(counts.get(k, 0) for k in ('skipped', 'xfailed', 'xpassed')):
            reuse_reasons.append('skip/xfail requires acceptance review')
        if not counts.get('passed'):
            reuse_reasons.append('no passing tests')
        if outcome != 'PASSED' or not cleaned['ok'] or state['evidence_errors']:
            reuse_reasons.append('execution evidence is not a complete success')
        stacks = run / 'stacks.log'
        if state['diagnostic_requests']:
            try:
                captured = stacks.stat().st_size > 0
            except OSError:
                captured = False
            state['diagnostic_status'] = 'captured' if captured else 'unavailable'
            if not captured:
                state['diagnostic_reason'] = progress.diag_error if progress and progress.diag_error else 'stack file not retained or empty'
            state['diagnostic_observed_at'] = utc()
        state['playwright_trace_status'] = 'not_enabled'
        state.update(lifecycle='FINISHED', outcome=outcome, termination_reason=reason,
                     pytest_exit_code=code, pytest_signal=-code if code is not None and code < 0 else None,
                     exit_code=exit_code, cleanup=cleaned, finished_at=utc(), counts=counts,
                     nodeids=progress.nodeids if progress else [], plugins=progress.plugins if progress else [],
                     inputs_unchanged=unchanged, environment_unchanged=environment_unchanged, reusable=not reuse_reasons, reuse_blockers=reuse_reasons,
                     elapsed_seconds=time.monotonic() - state.get('spawn_monotonic', time.monotonic()),
                     test_seconds=sum(e.get('duration', 0) for e in progress.reports) if progress else 0,
                     rerun_reason=manifest.get('rerun_reason'),
                     fingerprint=manifest['fingerprint'],
                     log_paths={name: str(run / name) for name in ('stdout.log', 'stderr.log', 'events.jsonl', 'stacks.log')})
        atomic(run / 'result.json', state)
        atomic(run / 'status.json', state)
        os.close(control)
        os.close(lock)


def recover(run):
    """Caller owns the OS lock; never infer ownership from a PID alone."""
    path = run / 'status.json'
    if not path.exists():
        raise RuntimeError('nonterminal run without process identity: ' + run.name)
    state = json.loads(path.read_text())
    if state.get('lifecycle') == 'FINISHED':
        if not state.get('cleanup', {}).get('ok'):
            raise RuntimeError('unresolved cleanup: ' + run.name)
        missing = [name for name in ('result.json', 'events.jsonl', 'stdout.log', 'stderr.log') if not (run / name).is_file()]
        if missing:
            state.update(reusable=False, exit_code=125)
            state.setdefault('evidence_errors', []).append('missing retained evidence: ' + ', '.join(missing))
            if state['outcome'] == 'PASSED':
                state['outcome'] = 'INFRA_ERROR'
            atomic(run / 'result.json', state)
            atomic(path, state)
        return
    if live(state['supervisor']):
        raise RuntimeError('supervisor still alive: ' + run.name)
    manifest = json.loads((run / 'manifest.json').read_text())
    cleaned = cleanup(state.get('pytest'), state.get('children', {}), manifest['policy'], recovering=True)
    state.update(lifecycle='FINISHED', outcome='INTERRUPTED', exit_code=125, reusable=False,
                 termination_reason='supervisor disappeared', cleanup=cleaned, finished_at=utc())
    atomic(run / 'result.json', state)
    atomic(path, state)
    if not cleaned['ok']:
        raise RuntimeError('recovery cleanup uncertain: ' + run.name)


if __name__ == '__main__':
    if sys.argv[1] == '--recover':
        recover(Path(sys.argv[2]))
    else:
        supervise(Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]))
