#!/usr/bin/env python3
"""Reproducible local usage/run evidence. Never copy prompts or raw conversations."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from statistics import median
import time

import psutil

TOKEN_KEYS = ('uncached_input', 'cache_read', 'cache_creation', 'output')
PHASES = ('implementation', 'review', 'waiting', 'test')


def phase_totals(events):
    """Single-controller intervals; unfinished work is never reported as zero."""
    active = None
    totals = {}
    for event in events:
        key = (event['task_id'], event['phase'])
        if event['phase'] not in PHASES:
            raise ValueError('unknown phase')
        if event['event'] == 'start':
            if active:
                raise ValueError('a phase is already active')
            active = event
        elif event['event'] == 'stop':
            if not active or key != (active['task_id'], active['phase']):
                raise ValueError('phase stop does not match start')
            elapsed = event['monotonic'] - active['monotonic']
            if event['boot_time'] != active['boot_time'] or elapsed < 0:
                raise ValueError('phase crossed a reboot or invalid clock')
            task = totals.setdefault(event['task_id'], {})
            task[event['phase']] = task.get(event['phase'], 0) + elapsed
            active = None
        else:
            raise ValueError('unknown phase event')
    return dict(completed_seconds=totals, active=active)


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def capture_usage(path, cutoff=None):
    """Last usage per response/message ID; a complete, hashed JSONL prefix only."""
    with path.open('rb') as stream:
        raw = stream.read(path.stat().st_size if cutoff is None else cutoff)
    if cutoff is not None and len(raw) != cutoff:
        raise ValueError('source is shorter than requested cutoff')
    raw = raw[:raw.rfind(b'\n') + 1]
    records = {}
    model = effort = None
    invalid = 0
    for line in raw.splitlines():
        try:
            row = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            invalid += 1
            continue
        payload = row.get('payload', {})
        if row.get('type') == 'turn_context':
            model, effort = payload.get('model'), payload.get('effort')
        if row.get('type') == 'token_usage_record':
            usage = payload['usage']
            key = payload['response_id']
            read = usage.get('cached_input_tokens')
            write = usage.get('cache_write_input_tokens')
            total = usage.get('input_tokens')
            uncached = total - read - write if None not in (total, read, write) else None
            record = dict(timestamp=row['timestamp'], model=model, effort=effort,
                          uncached_input=uncached, cache_read=read, cache_creation=write,
                          output=usage.get('output_tokens'))
        elif row.get('type') == 'assistant' and row.get('message', {}).get('usage'):
            msg = row['message']; usage = msg['usage']; key = msg['id']
            record = dict(timestamp=row['timestamp'], model=msg.get('model'), effort=None,
                          uncached_input=usage.get('input_tokens'),
                          cache_read=usage.get('cache_read_input_tokens'),
                          cache_creation=usage.get('cache_creation_input_tokens'),
                          output=usage.get('output_tokens'))
        else:
            continue
        if any(record[k] is not None and record[k] < 0 for k in TOKEN_KEYS):
            raise ValueError('inconsistent token usage')
        if key in records:
            record['timestamp'] = records[key]['timestamp']
        records[key] = record
    return dict(source_file=path.name, included_bytes=len(raw),
                prefix_sha256=hashlib.sha256(raw).hexdigest(), invalid_json_lines=invalid,
                method='last usage per response/message ID; first occurrence order',
                records=list(records.values()))


def aggregate_usage(records):
    totals = {k: sum(r[k] for r in records) if records and all(r[k] is not None for r in records)
              else None for k in TOKEN_KEYS}
    inputs = [totals[k] for k in TOKEN_KEYS[:3]]
    total = sum(inputs) if None not in inputs else None
    return dict(requests=len(records), **totals, total_input=total,
                cached_input_fraction=totals['cache_read'] / total if total else None,
                models=sorted({r['model'] for r in records if r['model']}),
                efforts=sorted({r['effort'] for r in records if r['effort']}))


def union_seconds(intervals):
    merged = []
    for start, end in sorted(intervals):
        if end < start:
            raise ValueError('negative interval')
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    return sum(end - start for start, end in merged)


def load_run(root, run_id):
    run = root / run_id
    manifest = json.loads((run / 'manifest.json').read_text())
    result = json.loads((run / 'result.json').read_text())
    if result['run_id'] != run_id or result['fingerprint'] != manifest['fingerprint']:
        raise ValueError('run identity mismatch')
    key = dict(inputs=manifest['inputs']['fingerprint'], environment=manifest['environment'],
               args=manifest['pytest_args'], policy=manifest['policy'])
    return dict(run_id=run_id, task_id=result['task_id'], fingerprint=result['fingerprint'],
                comparison_key=hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest(),
                started_at=result['started_at'], finished_at=result['finished_at'],
                elapsed_seconds=result.get('elapsed_seconds'), test_seconds=result.get('test_seconds'),
                outcome=result['outcome'], counts=result['counts'], cleanup=result['cleanup'],
                inputs_unchanged=result.get('inputs_unchanged'), environment_unchanged=result.get('environment_unchanged'),
                evidence_errors=result['evidence_errors'], rerun_reason=result.get('rerun_reason'),
                diagnostic_requests=result.get('diagnostic_requests', []))


def duplicate_runs(runs):
    seen = {}; duplicates = []
    for run in sorted(runs, key=lambda r: r['started_at']):
        key = run['comparison_key']
        if key in seen:
            reason = run['rerun_reason']
            duplicates.append(dict(run_id=run['run_id'], previous_run_id=seen[key], reason=reason,
                                   classification=('required-by-finishing' if reason == 'required-by-finishing'
                                                   else 'explained' if reason else 'unexplained')))
        seen[key] = run['run_id']
    return duplicates


def task_metrics(task, snapshots, runs):
    windows = [(timestamp(a), timestamp(b)) for a, b in task['observation_windows']]
    usage = [r for snapshot in snapshots for r in snapshot['records']
             if any(a <= timestamp(r['timestamp']) < b for a, b in windows)]
    selected = [r for r in runs if r['run_id'] in task['run_ids']]
    return dict(**task, observed_window_seconds=union_seconds(windows), usage=aggregate_usage(usage),
                test_run_wall_seconds=union_seconds([(timestamp(r['started_at']), timestamp(r['finished_at']))
                                                    for r in selected]),
                test_seconds=(sum(r['test_seconds'] for r in selected)
                              if selected and all(r['test_seconds'] is not None for r in selected) else None),
                note='Shared run durations belong to the run, not exclusively to this task; do not sum tasks.')


def compare_runs(root, pairs, minimum_gain=.05):
    rows = []
    reference = None
    used = set()
    for baseline, candidate in pairs:
        row = {}
        for label, run_id in [('function', baseline), ('module', candidate)]:
            if run_id in used:
                raise ValueError('each benchmark run must be independent')
            used.add(run_id)
            run = load_run(root, run_id)
            manifest = json.loads((root / run_id / 'manifest.json').read_text())
            result = json.loads((root / run_id / 'result.json').read_text())
            scopes = [x for x in manifest['pytest_args'] if x.startswith('--ui-browser-scope=')]
            if scopes != [f'--ui-browser-scope={label}']:
                raise ValueError('benchmark lifetime argument does not match label')
            identity = dict(inputs=manifest['inputs'], environment=manifest['environment'], policy=manifest['policy'],
                            args=[x for x in manifest['pytest_args'] if not x.startswith('--ui-browser-scope=')],
                            nodeids=result['nodeids'])
            if reference is None:
                reference = identity
            if identity != reference:
                raise ValueError('benchmark inputs/environment/policy/test scope differ')
            if run['elapsed_seconds'] is None:
                raise ValueError('benchmark elapsed time is missing')
            run['valid'] = (run['outcome'] == 'PASSED' and run['cleanup']['ok'] and
                            not run['cleanup']['remaining'] and not run['evidence_errors'] and
                            run['inputs_unchanged'] and run['environment_unchanged'] and
                            run['counts']['passed'] > 0 and
                            not any(run['counts'][k] for k in ['failed', 'errors', 'skipped', 'xfailed', 'xpassed']))
            row[label] = run
        row['gain'] = 1 - row['module']['elapsed_seconds'] / row['function']['elapsed_seconds']
        rows.append(row)
    if not rows:
        raise ValueError('benchmark requires pairs')
    baseline = median(r['function']['elapsed_seconds'] for r in rows)
    candidate = median(r['module']['elapsed_seconds'] for r in rows)
    valid = all(r[k]['valid'] for r in rows for k in ['function', 'module'])
    gain = 1 - candidate / baseline
    return dict(pairs=rows, baseline_median_seconds=baseline, candidate_median_seconds=candidate,
                median_gain=gain, minimum_gain=minimum_gain,
                adopt=(len(rows) >= 3 and valid and gain >= minimum_gain and all(r['gain'] > 0 for r in rows)),
                stability='No observed failures is not a statistical guarantee of equal flake rates.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    phase = sub.add_parser('phase', help='record a phase boundary for a single controller')
    phase.add_argument('--journal', type=Path, required=True)
    phase.add_argument('--task-id', required=True)
    phase.add_argument('--phase', choices=PHASES, required=True)
    phase.add_argument('--event', choices=('start', 'stop'), required=True)
    capture = sub.add_parser('capture')
    capture.add_argument('--session', type=Path, required=True)
    capture.add_argument('--cutoff', type=int)
    capture.add_argument('--output', type=Path, required=True)
    report = sub.add_parser('report')
    report.add_argument('--spec', type=Path, required=True)
    report.add_argument('--runs', type=Path, default=Path('.test-runs'))
    report.add_argument('--output', type=Path, required=True)
    compare = sub.add_parser('compare')
    compare.add_argument('--pair', nargs=2, action='append', required=True, metavar=('FUNCTION_RUN', 'MODULE_RUN'))
    compare.add_argument('--runs', type=Path, default=Path('.test-runs'))
    compare.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'phase':
        events = json.loads(args.journal.read_text()) if args.journal.exists() else []
        events.append(dict(task_id=args.task_id, phase=args.phase, event=args.event,
                           utc=datetime.now(timezone.utc).isoformat(), monotonic=time.monotonic(),
                           boot_time=psutil.boot_time()))
        totals = phase_totals(events)
        temporary = args.journal.with_suffix('.tmp')
        temporary.write_text(json.dumps(events, indent=2) + '\n')
        temporary.replace(args.journal)
        print(json.dumps(totals, ensure_ascii=False))
        return
    if args.action == 'capture':
        data = capture_usage(args.session, args.cutoff)
        data['total'] = aggregate_usage(data['records'])
        data['first_recorded_request'] = aggregate_usage(data['records'][:1])
        data['subsequent_requests'] = aggregate_usage(data['records'][1:])
    elif args.action == 'compare':
        data = compare_runs(args.runs, args.pair)
    else:
        spec = json.loads(args.spec.read_text())
        snapshots = [json.loads((args.spec.parent / name).read_text()) for name in spec['usage_snapshots']]
        runs = [load_run(args.runs, run_id) for run_id in spec['run_ids']]
        data = dict(schema_version=1, runs=runs, duplicates=duplicate_runs(runs),
                    tasks=[task_metrics(task, snapshots, runs) for task in spec['tasks']],
                    limitations=spec['limitations'])
        if 'phase_journal' in spec:
            data['phase_timings'] = phase_totals(json.loads((args.spec.parent / spec['phase_journal']).read_text()))
    args.output.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n')


if __name__ == '__main__':
    main()
