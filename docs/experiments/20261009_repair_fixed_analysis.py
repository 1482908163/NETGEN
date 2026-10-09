#!/usr/bin/env python3
"""Recompute results from checked-in exports; never generate synthetic timings."""
import argparse
import csv
import hashlib
import io
import json
import math
from pathlib import Path
from statistics import median

PHASES = ('generation', 'repair', 'optimization')
OPS = ('combine', 'split', 'swap', 'swap2')
COUNTS = ('calls', 'input_points', 'input_elements', 'evaluated_items',
          'candidates', 'commit_attempts', 'applied', 'team_evaluations')
TIMES = ('total_seconds', 'prepare_seconds', 'evaluate_seconds', 'order_seconds',
         'commit_seconds', 'cleanup_seconds', 'quality_seconds', 'conform_seconds',
         'rest_seconds', 'worstcase_seconds', 'legal_seconds',
         'serial_evaluate_seconds', 'team_evaluate_seconds')
ROUTES = ('batch_parallel', 'repair_fixed_profile', 'repair_fixed')
SEEDS = (-1, 17, 41)
SCALES = (128, 256, 512)
SOURCE = 'd10b03b5fdcd54f8f99d737474512710a4800f25'
UPLOAD = '92d48ff5077f3b65d92ab100294755b6f14ba93c'


def read_csv(path):
    with path.open(newline='') as stream:
        return list(csv.DictReader(stream))


def inspect_cost(path, anchor):
    """Independent validation of exported operation rows, not missing JSONL."""
    rows = read_csv(path)
    aggregates = {p: dict.fromkeys(TIMES + COUNTS, 0.0) for p in PHASES}
    ranks, seen, gaps = {}, set(), []
    identity_keys = ('source_revision', 'binary_sha256', 'kernel_sha256',
                     'core_sha256', 'input_sha256', 'runtime_sha256')
    identity = {k: rows[0][k] for k in identity_keys}
    for row in rows:
        names = TIMES + COUNTS + ('phase_seconds', 'native_seconds', 'native_calls',
                                 'compute_seconds', 'core_seconds')
        values = {k: float(row[k]) for k in names}
        assert all(math.isfinite(v) and v >= 0 for v in values.values()), path
        assert all(values[k].is_integer() for k in COUNTS + ('native_calls',)), path
        assert all(row[k] == identity[k] for k in identity_keys), path
        rank, phase, op = int(row['rank']), PHASES.index(row['phase']), OPS.index(row['operation'])
        key = rank, phase, op
        assert 0 <= rank < int(row['ranks']) and key not in seen, path
        seen.add(key)
        v = values
        tolerance = 1e-7 + v['total_seconds'] * 1e-6
        for fields, total in ((TIMES[1:6], v['total_seconds']),
                              (TIMES[6:11], v['total_seconds']),
                              (TIMES[11:13], v['evaluate_seconds'])):
            assert abs(sum(v[k] for k in fields) - total) <= tolerance, (path, key)
        assert v['applied'] <= v['commit_attempts'] <= v['candidates'] <= v['evaluated_items'], path
        assert v['team_evaluations'] <= v['calls'] == v['native_calls'], path
        assert row['coverage_match'] == 'True', path
        if v['calls'] == 0:
            assert not any(v[k] for k in TIMES + COUNTS), path
        if v['team_evaluations'] == 0:
            assert v['team_evaluate_seconds'] == 0, path
        if v['team_evaluations'] == v['calls']:
            assert v['serial_evaluate_seconds'] == 0, path
        clock_match = abs(v['native_seconds'] - v['total_seconds']) <= (
            .0002 + .00005 * v['calls'] + .01 * max(v['native_seconds'], v['total_seconds']))
        assert (row['clock_match'] == 'True') == clock_match, path
        if not clock_match:
            gaps.append(dict(rank=rank, phase=row['phase'], operation=row['operation'],
                             native=v['native_seconds'], scoped=v['total_seconds']))
        for k in TIMES + COUNTS:
            aggregates[row['phase']][k] += v[k]
        if rank not in ranks:
            ranks[rank] = dict(rank=rank, compute_seconds=v['compute_seconds'],
                               core_seconds=v['core_seconds'], phase_seconds=[None] * 3,
                               phase_costs=[0] * 3, phase_legal=[0] * 3,
                               calls=[0] * 12, applied=[0] * 12,
                               candidates=[0] * 12, attempts=[0] * 12)
        r = ranks[rank]
        assert r['compute_seconds'] == v['compute_seconds'] and r['core_seconds'] == v['core_seconds'], path
        assert r['phase_seconds'][phase] in (None, v['phase_seconds']), path
        r['phase_seconds'][phase] = v['phase_seconds']
        r['phase_costs'][phase] += v['total_seconds']
        r['phase_legal'][phase] += v['legal_seconds']
        for target, field in (('calls', 'calls'), ('applied', 'applied'),
                              ('candidates', 'candidates'), ('attempts', 'commit_attempts')):
            r[target][phase * 4 + op] = v[field]
    ordered = [ranks[i] for i in range(int(rows[0]['ranks']))]
    assert len(rows) == len(ordered) * 12, path
    for r in ordered:
        assert all(a <= b + 1e-5 for a, b in zip(r['phase_costs'], r['phase_seconds'])), path
    slowest = max(ordered, key=lambda r: r['compute_seconds'])
    return dict(ranks=len(ordered), seed=int(rows[0]['partition_seed']),
                repeat=int(rows[0]['repeat']), records=len(rows),
                clock_gaps=len(gaps), clock_gap_items=gaps, identity=identity,
                aggregate=aggregates, rank_rows=ordered,
                fixed_cost_rows=[r for r in rows if int(r['rank']) == anchor],
                max_compute_rank=slowest['rank'], max_compute_seconds=slowest['compute_seconds'],
                max_core_seconds=max(r['core_seconds'] for r in ordered), fixed_rank=anchor)


def stem(seed):
    return 'sparse_natural' if seed == -1 else f'sparse_seed{seed}_natural'


def load_exports(root):
    manifest_path = Path(__file__).with_name('20261009_repair_fixed_source_manifest.json')
    manifest = json.loads(manifest_path.read_text())
    for item in manifest:
        data = (root / item['path']).read_bytes()
        sha = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        assert sha == item['sha'], ('source changed', item['path'])
    runs, statuses, quality, anchors, costs = [], [], [], {}, []
    for p in SCALES:
        for route in ROUTES:
            directory = root / f'route_{route}/p{p}'
            for row in read_csv(directory / 'analysis/runs.csv'):
                runs.append(dict(row, route=route))
            with (directory / 'run_status.tsv').open(newline='') as stream:
                statuses.extend(dict(row, route=route, ranks=p)
                                for row in csv.DictReader(stream, delimiter='\t'))
        for seed in SEEDS:
            baseline = next(r for r in runs if r['route'] == 'batch_parallel'
                            and int(r['ranks']) == p and int(r['partition_seed']) == seed
                            and int(r['repeat']) == 1)
            anchor = anchors[f'{p}/{seed}'] = int(baseline['slowest_compute_rank'])
            path = f'p{p}/{stem(seed)}/repeat_0/quality_summary.json'
            a = json.loads((root / ('route_batch_parallel/' + path)).read_text())
            for route in ROUTES[1:]:
                b = json.loads((root / (f'route_{route}/' + path)).read_text())
                equal = {k: v for k, v in a.items() if k != 'scheduler'} == {
                    k: v for k, v in b.items() if k != 'scheduler'}
                assert equal and a['structural_pass'] and b['structural_pass'], path
                assert a['identity']['MESH_SOURCE_REVISION'] == SOURCE, path
                quality.append(dict(ranks=p, seed=seed, route=route,
                                    identical_except_scheduler=equal, counts=a['counts'],
                                    identity=a['identity'], per_rank=len(a['per_rank'])))
            for route in ROUTES[1:]:
                for repeat in range(4):
                    path = f'route_{route}/p{p}/{stem(seed)}/repeat_{repeat}/operation_cost_profile.csv'
                    cost = inspect_cost(root / path, anchor)
                    cost.update(path=path, route=route)
                    costs.append(cost)
    return dict(dataset=root.name, upload_commit=UPLOAD, measured_source=SOURCE,
                runs=runs, statuses=statuses, quality_checks=quality, anchors=anchors,
                cost_checks=costs, source_files=manifest,
                runtime_manifest=json.loads((root / 'runtime_manifest.json').read_text()),
                raw_profiles_in_repo=sum(1 for _ in root.rglob('rank_profiles.jsonl*')))


def analyze(e):
    assert len(e['statuses']) == 108 and all(int(r['exit_code']) == 0 for r in e['statuses'])
    assert sum(int(r['repeat']) == 0 for r in e['statuses']) == 27
    assert len(e['runs']) == 81 and len(e['quality_checks']) == 18
    indexed = {(int(r['ranks']), int(r['partition_seed']), int(r['repeat']), r['route']): r
               for r in e['runs']}
    assert len(indexed) == 81
    cost_index = {(r['ranks'], r['seed'], r['repeat'], r['route']): r for r in e['cost_checks']}
    assert len(cost_index) == 72
    pairs, groups, fixed, tails = [], [], [], []
    mismatches = dict(applied=0, candidates=0, warm_calls=0, optimization_calls=0)
    repair = {route: dict(records=0, zero_applied=0, one_round=0, calls=0, applied=0)
              for route in ROUTES[1:]}
    for p in SCALES:
        for seed in SEEDS:
            anchor = e['anchors'][f'{p}/{seed}']
            group = dict(ranks=p, seed=seed, fixed_rank=anchor)
            for control, candidate, kind in ((ROUTES[0], ROUTES[1], 'diagnostic'),
                                              (ROUTES[1], ROUTES[2], 'algorithm'),
                                              (ROUTES[0], ROUTES[2], 'net')):
                values = []
                for repeat in range(1, 4):
                    a, b = indexed[p, seed, repeat, control], indexed[p, seed, repeat, candidate]
                    value = 100 * (1 - float(b['core_seconds']) / float(a['core_seconds']))
                    values.append(value)
                    pairs.append(dict(ranks=p, seed=seed, repeat=repeat, contribution=kind,
                                      control=control, candidate=candidate,
                                      control_seconds=float(a['core_seconds']),
                                      candidate_seconds=float(b['core_seconds']), reduction_pct=value))
                group[kind + '_paired_reduction_pct'] = median(values)
                group[kind + '_wins'] = sum(v > 0 for v in values)
            for route in (ROUTES[0], ROUTES[2]):
                records = [indexed[p, seed, i, route] for i in range(1, 4)]
                group[route + '_imbalance'] = median(float(r['compute_imbalance']) for r in records)
            for route in ROUTES[1:]:
                records = [cost_index[p, seed, i, route]['rank_rows'][anchor] for i in range(1, 4)]
                d = dict(ranks=p, seed=seed, fixed_rank=anchor, route=route,
                         compute_seconds=median(r['compute_seconds'] for r in records),
                         generation_legal_seconds=median(r['phase_legal'][0] for r in records),
                         repair_split_calls=median(r['calls'][5] for r in records),
                         repair_applied=sum(sum(r['applied'][4:8]) for r in records))
                for i, phase in enumerate(PHASES):
                    d[phase + '_seconds'] = median(r['phase_seconds'][i] for r in records)
                fixed.append(d)
                group[route + '_fixed'] = d
            for repeat in range(4):
                a, b = (cost_index[p, seed, repeat, route] for route in ROUTES[1:])
                if repeat:
                    for route, c in ((ROUTES[1], a), (ROUTES[2], b)):
                        run = indexed[p, seed, repeat, route]
                        assert math.isclose(c['max_compute_seconds'], float(run['compute_max_seconds']), abs_tol=1e-8)
                        assert math.isclose(c['max_core_seconds'], float(run['core_seconds']), abs_tol=1e-8)
                        assert c['clock_gaps'] == int(run['cost_clock_gap_count'])
                        if route == ROUTES[2]:
                            assert int(run['repair_fixed_skipped']) > 0 and int(run['repair_fixed_mismatches']) == 0
                        for r in c['rank_rows']:
                            assert r['calls'][5] == r['calls'][6] == r['calls'][7]
                            stat = repair[route]
                            count = sum(r['applied'][4:8])
                            stat['records'] += 1
                            stat['zero_applied'] += count == 0
                            stat['one_round'] += r['calls'][5] == 1
                            stat['calls'] += r['calls'][5]
                            stat['applied'] += count
                for x, y in zip(a['rank_rows'], b['rank_rows']):
                    assert x['rank'] == y['rank']
                    mismatches['applied'] += x['applied'] != y['applied']
                    mismatches['candidates'] += x['candidates'] != y['candidates']
                    mismatches['warm_calls'] += repeat == 0 and x['calls'] != y['calls']
                    mismatches['optimization_calls'] += repeat != 0 and x['calls'][8:] != y['calls'][8:]
            for repeat in range(1, 4):
                row = indexed[p, seed, repeat, ROUTES[2]]
                tail = dict(ranks=p, seed=seed, repeat=repeat, rank=int(row['slowest_compute_rank']),
                            compute_seconds=float(row['compute_max_seconds']))
                for k, v in row.items():
                    if k.startswith(('slowest_compute_kernel_', 'slowest_compute_repair_fixed_')) or k in (
                        'slowest_compute_volume_refine_seconds', 'slowest_compute_cost_optimization_split_evaluate_seconds',
                        'slowest_compute_cost_optimization_split_commit_seconds'):
                        tail[k[len('slowest_compute_'):]] = float(v)
                tails.append(tail)
            groups.append(group)
    assert not any(mismatches.values()), mismatches
    coverage = dict(files=72, records=sum(c['records'] for c in e['cost_checks']),
                    formal_records=sum(c['records'] for c in e['cost_checks'] if c['repeat']),
                    clock_gaps=sum(c['clock_gaps'] for c in e['cost_checks']),
                    formal_clock_gaps=sum(c['clock_gaps'] for c in e['cost_checks'] if c['repeat']),
                    call_coverage_valid=True, partition_conservation_valid=True)
    summary = dict(dataset=e['dataset'], upload_commit=e['upload_commit'], measured_source=e['measured_source'],
                   successful_warmups=27, successful_formal=81, exact_quality_pairs=18,
                   raw_profiles_in_repo=e['raw_profiles_in_repo'], coverage=coverage,
                   work_mismatches=mismatches, repair=repair, groups=groups,
                   net_wins=sum(p['reduction_pct'] > 0 for p in pairs if p['contribution'] == 'net'))
    summary['scales'] = [dict(ranks=p,
                              net_median_of_seed_paired_medians=median(g['net_paired_reduction_pct'] for g in groups if g['ranks'] == p),
                              net_wins=sum(x['reduction_pct'] > 0 for x in pairs if x['ranks'] == p and x['contribution'] == 'net'),
                              baseline_imbalance=median(g[ROUTES[0] + '_imbalance'] for g in groups if g['ranks'] == p),
                              candidate_imbalance=median(g[ROUTES[2] + '_imbalance'] for g in groups if g['ranks'] == p))
                         for p in SCALES]
    return summary, pairs, fixed, tails


def write_csv(path, rows):
    names = list(dict.fromkeys(k for row in rows for k in row))
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=names)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', nargs='?', type=Path)
    parser.add_argument('--checked-exports', type=Path,
                        help='Use the connector-reviewed cache; does not replay missing raw profiles.')
    parser.add_argument('--output', type=Path, default=Path(__file__).parent)
    args = parser.parse_args()
    if not args.checked_exports and not args.root:
        parser.error('root or --checked-exports is required')
    evidence = json.loads(args.checked_exports.read_text()) if args.checked_exports else load_exports(args.root)
    summary, pairs, fixed, tails = analyze(evidence)
    args.output.mkdir(parents=True, exist_ok=True)
    prefix = args.output / '20261009_repair_fixed'
    Path(str(prefix) + '_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    write_csv(Path(str(prefix) + '_pairs.csv'), pairs)
    write_csv(Path(str(prefix) + '_fixed_rank.csv'), fixed)
    write_csv(Path(str(prefix) + '_remaining_tail.csv'), tails)
    print(json.dumps(dict(coverage=summary['coverage'], scales=summary['scales'],
                          work_mismatches=summary['work_mismatches']), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
