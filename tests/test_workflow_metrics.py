import json

import pytest

from scripts.workflow_metrics import aggregate_usage, capture_usage, compare_runs, duplicate_runs, phase_totals, union_seconds


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
