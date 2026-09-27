import json

import pytest

from scripts.workflow_metrics import aggregate_usage, capture_usage, compare_runs, duplicate_runs, measurement_acceptance, merge_usage_snapshots, phase_totals, task_metrics, union_seconds


def test_usage_snapshot_deduplicates_and_preserves_first_request(tmp_path):
    path = tmp_path / 'session.jsonl'
    def usage(key, output):
        return dict(type='token_usage_record', timestamp='2026-09-27T12:00:00Z',
                    payload=dict(response_id=key, usage=dict(input_tokens=100, cached_input_tokens=70,
                                                           cache_write_input_tokens=10, output_tokens=output)))
    rows = [dict(type='turn_context', payload=dict(model='observed-model', effort='high')),
            usage('first', 1), usage('second', 2), usage('first', 3)]
    prefix = ''.join(json.dumps(row) + '\n' for row in rows).encode()
    path.write_bytes(prefix + b'{"partial":')
    snapshot = capture_usage(path)
    assert snapshot['included_bytes'] == len(prefix)
    assert len(snapshot['records']) == 2
    assert snapshot['records'][0]['output'] == 3
    assert aggregate_usage(snapshot['records']) == dict(
        requests=2, uncached_input=40, cache_read=140, cache_creation=20, output=5,
        total_input=200, cached_input_fraction=.7, models=['observed-model'], efforts=['high'])
    path.write_bytes(prefix + json.dumps(usage('third', 9)).encode() + b'\n')
    assert capture_usage(path, len(prefix)) == snapshot
    with pytest.raises(ValueError, match='shorter'):
        capture_usage(path, path.stat().st_size + 1)


def test_missing_usage_is_unknown_and_claude_input_is_not_double_subtracted(tmp_path):
    path = tmp_path / 'session.jsonl'
    row = dict(type='assistant', timestamp='2026-09-27T12:00:00Z',
               message=dict(id='first', model='observed', usage=dict(input_tokens=3, cache_read_input_tokens=7,
                                                                  cache_creation_input_tokens=2, output_tokens=1)))
    path.write_text(json.dumps(row) + '\n')
    totals = aggregate_usage(capture_usage(path)['records'])
    assert totals['total_input'] == 12 and totals['uncached_input'] == 3
    assert aggregate_usage([])['cache_read'] is None
    del row['message']['usage']['cache_creation_input_tokens']
    path.write_text(json.dumps(row) + '\n')
    assert aggregate_usage(capture_usage(path)['records'])['total_input'] is None


def test_overlapping_wall_time_is_not_summed():
    assert union_seconds([(0, 5), (2, 4), (4, 8), (10, 11)]) == 9
    with pytest.raises(ValueError):
        union_seconds([(2, 1)])


def test_phase_journal_rejects_overlap_and_keeps_unfinished_unknown():
    start = dict(task_id='T4', phase='review', event='start', monotonic=10, boot_time=1)
    stop = dict(start, event='stop', monotonic=13)
    assert phase_totals([start]) == dict(completed_seconds={}, active=start)
    assert phase_totals([start, stop]) == dict(completed_seconds={'T4': {'review': 3}}, active=None)
    for events in ([stop], [start, start], [start, dict(stop, task_id='wrong')],
                   [start, dict(stop, boot_time=2)]):
        with pytest.raises(ValueError):
            phase_totals(events)


def test_duplicate_reasons_are_separate_from_identity():
    runs = [dict(run_id=str(i), started_at=str(i), comparison_key='same', rerun_reason=reason)
            for i, reason in enumerate([None, None, 'required-by-finishing', 'new-suspicion'])]
    assert [r['classification'] for r in duplicate_runs(runs)] == [
        'unexplained', 'required-by-finishing', 'explained']


@pytest.mark.parametrize('clock,boot', [(5, 1), (20, 2), (float('nan'), 1),
                                       (float('inf'), 1), (20, float('nan'))])
def test_phase_journal_rejects_invalid_boundaries_between_completed_phases(clock, boot):
    start = dict(task_id='one', phase='implementation', event='start', monotonic=10, boot_time=1)
    stop = dict(start, event='stop', monotonic=20)
    following = dict(start, task_id='two', phase='review', monotonic=clock, boot_time=boot)
    with pytest.raises(ValueError, match='clock|reboot'):
        phase_totals([start, stop, following])


def test_phase_journal_accepts_adjacent_intervals_and_task_switches():
    start = dict(task_id='one', phase='implementation', event='start', monotonic=10, boot_time=1)
    stop = dict(start, event='stop', monotonic=20)
    following = dict(start, task_id='two', phase='review', monotonic=20)
    assert phase_totals([start, stop, following, dict(following, event='stop', monotonic=25)]) == dict(
        completed_seconds={'one': {'implementation': 10}, 'two': {'review': 5}}, active=None)


def test_phase_journal_uses_boot_identity_despite_wall_clock_correction():
    start = dict(task_id='one', phase='implementation', event='start', monotonic=10,
                 boot_time=100, boot_id='boot-one')
    stop = dict(start, event='stop', monotonic=15, boot_time=96)
    assert phase_totals([start, stop])['completed_seconds']['one']['implementation'] == 5
    with pytest.raises(ValueError, match='reboot'):
        phase_totals([start, dict(stop, boot_id='boot-two')])
    del stop['boot_id']
    with pytest.raises(ValueError, match='reboot'):
        phase_totals([start, stop])


def test_phase_cli_records_replayable_boot_identity(tmp_path):
    import subprocess
    import sys
    path = tmp_path / 'phases.json'
    args = [sys.executable, 'scripts/workflow_metrics.py', 'phase', '--journal', str(path),
            '--task-id', 'smoke', '--phase', 'implementation', '--event']
    for event in ('start', 'stop'):
        subprocess.run([*args, event], check=True, capture_output=True, timeout=10)
    events = json.loads(path.read_text())
    assert events[0]['boot_id'] and events[0]['boot_id'] == events[1]['boot_id']
    assert phase_totals(events)['completed_seconds']['smoke']['implementation'] > 0


def test_task_report_joins_phases_without_turning_missing_time_into_zero():
    task = dict(task_id='one', observation_windows=[], run_ids=[], implementation_seconds=None,
                observed_absent_phases=['waiting'])
    phases = dict(completed_seconds={'one': {'implementation': 3, 'test': 5, 'review': 2}}, active=None)
    result = task_metrics(task, [], [], phases)
    assert result['phase_seconds'] == dict(implementation=3, test=5, review=2, waiting=0)
    assert result['implementation_seconds'] == 3 and result['phase_measurement_complete']
    del phases['completed_seconds']['one']['review']
    result = task_metrics(task, [], [], phases)
    assert result['review_seconds'] is None and not result['phase_measurement_complete']
    phases['active'] = dict(task_id='one', phase='test')
    assert task_metrics(task, [], [], phases)['test_phase_seconds'] is None
    with pytest.raises(ValueError, match='conflict'):
        task_metrics(dict(task, observed_absent_phases=['test']), [], [], phases)
    del phases['completed_seconds']['one']['test']
    with pytest.raises(ValueError, match='active'):
        task_metrics(dict(task, observed_absent_phases=['test']), [], [], phases)


def test_report_cli_links_the_journal_to_each_task(tmp_path):
    import subprocess
    import sys
    start = dict(task_id='one', phase='review', event='start', monotonic=10, boot_time=1)
    (tmp_path / 'journal.json').write_text(json.dumps([start, dict(start, event='stop', monotonic=12)]))
    spec = dict(usage_snapshots=[], run_ids=[], limitations=[], phase_journal='journal.json',
                tasks=[dict(task_id='one', observation_windows=[], run_ids=[], review_seconds=None)])
    (tmp_path / 'spec.json').write_text(json.dumps(spec))
    subprocess.run([sys.executable, 'scripts/workflow_metrics.py', 'report', '--spec', str(tmp_path / 'spec.json'),
                    '--output', str(tmp_path / 'report.json')], check=True, capture_output=True, timeout=10)
    task = json.loads((tmp_path / 'report.json').read_text())['tasks'][0]
    assert task['review_seconds'] == 2 and task['wait_seconds'] is None
    assert not task['phase_measurement_complete']


def test_usage_keeps_request_context_and_merges_overlapping_prefixes(tmp_path):
    path = tmp_path / 'session.jsonl'
    first = dict(type='token_usage_record', timestamp='2026-09-27T12:00:00Z',
                 payload=dict(response_id='one', usage=dict(input_tokens=100, cached_input_tokens=80,
                                                           cache_write_input_tokens=0, output_tokens=1)))
    rows = [dict(type='turn_context', payload=dict(model='first-model', effort='high')), first]
    path.write_text(''.join(json.dumps(r) + '\n' for r in rows))
    before = capture_usage(path)
    rows += [dict(type='turn_context', payload=dict(model='second-model', effort='low')),
             dict(first, timestamp='2026-09-27T12:01:00Z',
                  payload=dict(first['payload'], usage=dict(first['payload']['usage'], output_tokens=3)))]
    path.write_text(''.join(json.dumps(r) + '\n' for r in rows))
    after = capture_usage(path)
    records = merge_usage_snapshots([after, before])
    assert len(records) == 1 and records[0]['output'] == 3
    assert records[0]['timestamp'] == first['timestamp']
    assert records[0]['model'] == 'first-model' and records[0]['effort'] == 'high'
    assert records[0]['request_id_sha256'] == before['records'][0]['request_id_sha256']
    assert aggregate_usage(records)['total_input'] == 100


def test_usage_rejects_ambiguous_legacy_overlap_and_invalid_cutoff(tmp_path):
    legacy = dict(source_file='session.jsonl', records=[dict(timestamp='2026-09-27T12:00:00Z')])
    with pytest.raises(ValueError, match='legacy'):
        merge_usage_snapshots([legacy, legacy])
    # Reject before opening a source, rather than reading its entire contents with read(-1).
    for cutoff in (-1, 1.5, True):
        with pytest.raises(ValueError, match='cutoff'):
            capture_usage(tmp_path / 'does-not-exist', cutoff)


def test_measurement_acceptance_requires_three_complete_tasks_and_evidence():
    from copy import deepcopy
    task = dict(task_id='one', phase_seconds=dict(implementation=1, test=2, review=3, waiting=0),
                usage=dict(requests=1, uncached_input=1, cache_read=2, cache_creation=0, output=3),
                fix_rounds=1, minor_only_rounds=0, flaky_failures=0, late_regressions=[], scope_expansions=[],
                run_ids=['run'])
    tasks = [dict(task, task_id=str(i)) for i in range(3)]
    runs = [dict(run_id='run', test_seconds=.1)]
    assert measurement_acceptance(tasks, runs, [])['complete']
    assert not measurement_acceptance(tasks[:2], runs, [])['complete']
    assert not measurement_acceptance([task] * 3, runs, [])['complete']
    for phase in ('implementation', 'test', 'review', 'waiting'):
        bad = deepcopy(tasks)
        bad[0]['phase_seconds'][phase] = None
        assert not measurement_acceptance(bad, runs, [])['complete']
    assert not measurement_acceptance(tasks, [], [])['complete']
    assert not measurement_acceptance(tasks, [dict(run_id='run', test_seconds=None)], [])['complete']
    assert not measurement_acceptance(tasks, runs, [dict(classification='unexplained')])['complete']
    result = measurement_acceptance(tasks, runs, [dict(classification='required-by-finishing')])
    assert result['complete'] and result['finishing_duplicates'] == 1
    bad = deepcopy(tasks)
    bad[0]['usage']['cache_creation'] = None
    del bad[0]['flaky_failures']
    assert measurement_acceptance(bad, runs, [])['missing']['0'] == ['usage', 'flaky_failures']


def benchmark_runs(root):
    pairs = []
    for i in range(3):
        pair = []
        for scope, elapsed in [('function', 10), ('module', 8)]:
            run_id = f'{i}-{scope}'
            path = root / run_id
            path.mkdir()
            manifest = dict(fingerprint=run_id, inputs={'fingerprint': 'same'}, environment={}, policy={},
                            pytest_args=['tests/test_ui.py', f'--ui-browser-scope={scope}'])
            result = dict(run_id=run_id, task_id='benchmark', fingerprint=run_id,
                          started_at='2026-09-27T12:00:00Z', finished_at='2026-09-27T12:00:10Z',
                          elapsed_seconds=elapsed, test_seconds=elapsed - 1, outcome='PASSED',
                          counts=dict(passed=1, failed=0, errors=0, skipped=0, xfailed=0, xpassed=0),
                          cleanup=dict(ok=True, remaining=[]), inputs_unchanged=True,
                          environment_unchanged=True, evidence_errors=[], nodeids=['test_ui.py::test_ok'])
            (path / 'manifest.json').write_text(json.dumps(manifest))
            (path / 'result.json').write_text(json.dumps(result))
            pair.append(run_id)
        pairs.append(pair)
    return pairs


def test_benchmark_requires_same_inputs_and_independent_successful_runs(tmp_path):
    pairs = benchmark_runs(tmp_path)
    assert compare_runs(tmp_path, pairs)['adopt']
    assert not compare_runs(tmp_path, pairs[:2])['adopt']
    with pytest.raises(ValueError, match='independent'):
        compare_runs(tmp_path, pairs + pairs[:1])
    path = tmp_path / pairs[-1][-1] / 'result.json'
    result = json.loads(path.read_text())
    result['counts']['failed'] = 1
    path.write_text(json.dumps(result))
    assert not compare_runs(tmp_path, pairs)['adopt']
    path = tmp_path / pairs[-1][-1] / 'manifest.json'
    manifest = json.loads(path.read_text())
    manifest['inputs']['fingerprint'] = 'changed'
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='inputs/environment'):
        compare_runs(tmp_path, pairs)


def test_benchmark_rejects_wrong_variant_and_missing_environment_evidence(tmp_path):
    pairs = benchmark_runs(tmp_path)
    path = tmp_path / pairs[-1][-1] / 'result.json'
    result = json.loads(path.read_text())
    del result['environment_unchanged']
    path.write_text(json.dumps(result))
    assert not compare_runs(tmp_path, pairs)['adopt']
    with pytest.raises(ValueError, match='lifetime argument'):
        compare_runs(tmp_path, [list(reversed(pairs[0]))])
