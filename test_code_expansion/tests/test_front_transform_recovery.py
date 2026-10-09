#!/usr/bin/env python3
"""执行完整派生补审；原数据不变、计时不变，未知故障与质量差异仍拒绝。"""
import copy
import csv
import json
import tempfile
from pathlib import Path
from test_worklet_routes import fixture, profiles
from recover_front_transform_replay import SOURCE, corrected_rows, load_rows, recover, sha256


def contaminated(seed=-1):
    rows = fixture('front_transform', 0)
    for row in rows:
        row['metadata'].update(MESH_SOURCE_REVISION=SOURCE, partition_seed=str(seed))
        row['metrics']['front_distance_verified'] = row['metrics']['front_transform_verified']
        row['metrics']['front_distance_reference_seconds'] = row['metrics']['front_transform_reference_seconds']
    return rows


rows = contaminated()
derived, changes = corrected_rows(rows)
assert rows[0]['metrics']['front_distance_verified'] == 2
assert derived[0]['metrics']['front_distance_verified'] == 0
assert all(derived[i]['metrics'][k] == row['metrics'][k]
           for i, row in enumerate(rows) for k in row['metrics']
           if k not in ('front_distance_verified', 'front_distance_reference_seconds'))
for field, value in [('front_distance_hits', 1), ('front_distance_verified', 1),
                     ('front_transform_mismatches', 1), ('front_distance_reference_seconds', .02)]:
    bad = copy.deepcopy(rows)
    bad[0]['metrics'][field] = value
    try:
        corrected_rows(bad)
    except ValueError:
        pass
    else:
        raise AssertionError(field)
for change in ('unknown_revision', 'formal', 'missing_rank'):
    bad = copy.deepcopy(rows)
    if change == 'unknown_revision':
        for row in bad:
            row['metadata']['MESH_SOURCE_REVISION'] = 'unknown'
    elif change == 'formal':
        for row in bad:
            row['repeat'] = 1
    else:
        bad.pop()
    try:
        corrected_rows(bad)
    except ValueError:
        pass
    else:
        raise AssertionError(change)

with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    root = tmp / 'source'
    root.mkdir()
    (root / 'runtime_manifest.json').write_text(json.dumps(dict(source_revision=SOURCE)))
    (root / 'requested_process_counts.txt').write_text('2\n')
    for route in ('split_active', 'front_transform'):
        folder = root / ('route_' + route) / 'p2'
        folder.mkdir(parents=True)
        (folder / 'plan.json').write_text(json.dumps(dict(ranks=2, algorithms=['sparse'], timings=['natural'],
                                                        repeats=3, partition_seeds=[-1, 17, 41], quality_warmup=True)))
        for seed in (-1, 17, 41):
            stem = 'sparse_natural' if seed == -1 else f'sparse_seed{seed}_natural'
            for repeat in range(4):
                directory = folder / stem / f'repeat_{repeat}'
                directory.mkdir(parents=True)
                rows = contaminated(seed) if route == 'front_transform' and repeat == 0 else fixture(route, repeat)
                for row in rows:
                    row['metadata'].update(MESH_SOURCE_REVISION=SOURCE, partition_seed=str(seed))
                (directory / 'rank_profiles.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
                if route == 'front_transform' and repeat == 0:
                    (directory / 'failure_reason.txt').write_text('analysis_validation_failure\nexit_code=1\nfinalization_error=original front entered distance cache\n')
                else:
                    (directory / 'SUCCESS').touch()
    before = {str(p.relative_to(root)): sha256(p) for p in root.rglob('*') if p.is_file()}
    output = recover(root, tmp / 'derived')
    after = {str(p.relative_to(root)): sha256(p) for p in root.rglob('*') if p.is_file() and p.name != '.run.lock'}
    assert before == after
    evidence = json.loads((output / 'replay_recovery_manifest.json').read_text())
    assert evidence['complete'] and len(evidence['runs']) == 24
    assert sum(r['recovered'] for r in evidence['runs']) == 3
    comparisons = list(csv.DictReader((output / 'p2/route_comparisons.csv').open()))
    assert len(comparisons) == 3 and all(row['validated'] == 'True' for row in comparisons)
    assert '.front-replay-' not in (output / 'p2/route_runs.csv').read_text()
    assert (output / 'route_front_transform/p2/analysis/critical_rank_breakdown.csv').exists()
    warm = output / 'route_front_transform/p2/sparse_natural/repeat_0'
    assert load_rows(warm / 'original_rank_profiles.jsonl')[0]['metrics']['front_distance_verified'] == 2
    assert profiles.inspect(warm / 'rank_profiles.jsonl.gz')[0]['front_distance_verified'] == 0
    try:
        profiles.inspect(root / 'route_front_transform/p2/sparse_natural/repeat_0/rank_profiles.jsonl')
    except ValueError as error:
        assert 'original front entered distance cache' in str(error)
    else:
        raise AssertionError('original strict gate was weakened')
    bad = root / 'route_front_transform/p2/sparse_natural/repeat_0/rank_profiles.jsonl'
    rows = load_rows(bad)
    # The structural audit still passes, but a seemingly better quality value
    # differs from the control. The paired audit must reject this derived batch.
    rows[0]['metrics']['quality_shape_sum'] += .01
    bad.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    rejected = tmp / 'rejected'
    try:
        recover(root, rejected)
    except ValueError as error:
        assert 'strict paired audit failed' in str(error)
    else:
        raise AssertionError('quality drift was accepted')
    assert not rejected.exists() and not list(tmp.glob('.front-replay-*'))
print('PASS: isolated historical recovery, 24-run strict audit, raw retention, timing preservation, provenance/identity rejection and quality drift rejection')
